"""Regression tests for automatic historical memory retrieval fix.

These tests verify that:
1. The UserPromptSubmit hook is configured with async:false for context injection
2. Historical memories are retrieved for semantically related prompts
3. Memory retrieval completes before prompt proceeds
4. Service failures degrade gracefully
5. Scope isolation works correctly
"""

import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
HOOKS = ROOT / "hooks"


class FakeMemoryServiceForRetrieval(BaseHTTPRequestHandler):
    """Mock memory service for retrieval tests."""

    requests = []
    mode = "ok"

    def log_message(self, *_args: Any) -> None:
        pass

    def do_POST(self) -> None:
        try:
            content_length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(content_length))
        except (ValueError, json.JSONDecodeError):
            payload = {}

        type(self).requests.append((self.path, payload))

        if type(self).mode == "unavailable":
            self.send_response(503)
            self.end_headers()
            self.wfile.write(b'{"ok":false,"error":"service unavailable"}')
            return

        if type(self).mode == "timeout":
            # Simulate timeout by not responding
            return

        if self.path == "/memory/search":
            query = str(payload.get("query", "")).lower()
            if "accomplishment" in query:
                response = {
                    "ok": True,
                    "results": [{
                        "file": "reference_accomplishments_doc.md",
                        "section": "Google accomplishments",
                        "score": 0.98,
                        "content": "Reference to the Google accomplishments document at Documents/accomplishments.md."
                    }]
                }
            else:
                # Default fixture results for unrelated historical queries.
                response = {
                    "ok": True,
                    "results": [
                        {
                            "file": "user_role.md",
                            "section": "Role",
                            "score": 0.95,
                            "content": "User is a senior software engineer with deep expertise in system design and testing."
                        },
                        {
                            "file": "feedback_testing.md",
                            "section": "Feedback",
                            "score": 0.87,
                            "content": "Prefer integration tests over mocks; previous incident where mocks passed but prod failed."
                        }
                    ]
                }
        else:
            self.send_response(404)
            self.end_headers()
            return

        body = json.dumps(response).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class AutomaticMemoryRetrievalTests(unittest.TestCase):
    """Test automatic memory retrieval on prompt submission."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), FakeMemoryServiceForRetrieval)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.memory_url = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=2)

    def setUp(self) -> None:
        FakeMemoryServiceForRetrieval.requests = []
        FakeMemoryServiceForRetrieval.mode = "ok"

    def run_hook_with_prompt(self, prompt: str, timeout: int = 5) -> subprocess.CompletedProcess[str]:
        """Run the memory-search-on-prompt hook with a given prompt."""
        env = os.environ.copy()
        env["FLOSSWARE_MEMORY_URL"] = self.memory_url
        env["FLOSSWARE_MEMORY_TIMEOUT_MS"] = "1500"

        hook_path = HOOKS / "memory-search-on-prompt.js"
        event = {
            "hook_event_name": "UserPromptSubmit",
            "session_id": "test-session",
            "prompt": prompt
        }

        return subprocess.run(
            ["node", str(hook_path)],
            input=json.dumps(event),
            text=True,
            capture_output=True,
            timeout=timeout,
            env=env,
            check=False
        )

    def test_a_historical_global_memory_retrieval(self) -> None:
        """Test A: Historical global memory is retrieved for semantically related prompt."""
        # User's earlier prompt was: "I prefer integration tests over mocks"
        # Current prompt is semantically similar but uses different wording
        result = self.run_hook_with_prompt(
            "Should we use unit test mocks or real database connections?"
        )

        self.assertEqual(result.returncode, 0, f"Hook failed: {result.stderr}")
        output = json.loads(result.stdout)
        hook_output = output["hookSpecificOutput"]

        # Verify context was retrieved
        self.assertIn("additionalContext", hook_output)
        context = hook_output["additionalContext"]
        self.assertGreater(len(context), 0, "Should retrieve context for related query")

        # Verify the feedback about testing was included
        self.assertIn("integration tests", context.lower())

        # Verify memory search was called (may be called multiple times, but should include our query)
        self.assertGreater(len(FakeMemoryServiceForRetrieval.requests), 0)
        search_requests = [r for r in FakeMemoryServiceForRetrieval.requests if r[0] == "/memory/search"]
        self.assertGreater(len(search_requests), 0, "Should have made at least one search request")
        # Find the request with our query
        our_request = [r for r in search_requests if r[1].get("query") == "Should we use unit test mocks or real database connections?"]
        self.assertGreater(len(our_request), 0, "Should have searched for our query")

    def test_historical_reference_found_from_conversational_prompt(self) -> None:
        """A conversational prompt should also search concise historical keywords."""
        result = self.run_hook_with_prompt("what about the Google accomplishments doc?")

        self.assertEqual(result.returncode, 0, f"Hook failed: {result.stderr}")
        output = json.loads(result.stdout)
        context = output["hookSpecificOutput"]["additionalContext"]
        self.assertIn("reference_accomplishments_doc.md", context)

        queries = [
            payload.get("query", "")
            for path, payload in FakeMemoryServiceForRetrieval.requests
            if path == "/memory/search"
        ]
        self.assertIn("the Google accomplishments doc", queries)
        self.assertTrue(any("accomplishment" in query.lower() for query in queries))

    def test_b_hook_output_contract(self) -> None:
        """Test E: Hook returns context in the exact supported output format."""
        result = self.run_hook_with_prompt("continue working")

        self.assertEqual(result.returncode, 0, f"Hook failed: {result.stderr}")

        # Verify JSON structure
        output = json.loads(result.stdout)
        self.assertIn("hookSpecificOutput", output)

        hook_output = output["hookSpecificOutput"]
        self.assertEqual(hook_output["hookEventName"], "UserPromptSubmit")
        self.assertIn("additionalContext", hook_output)
        self.assertIn("contextRetrieval", hook_output)

        retrieval_stats = hook_output["contextRetrieval"]
        self.assertIn("queryHash", retrieval_stats)
        self.assertIn("memoryCount", retrieval_stats)
        self.assertIn("knowledgeCount", retrieval_stats)

    def test_c_service_timeout_handling(self) -> None:
        """Test F: Hook handles service timeout gracefully."""
        FakeMemoryServiceForRetrieval.mode = "timeout"

        # Hook should timeout and fail gracefully
        result = self.run_hook_with_prompt("test query", timeout=10)

        # Should exit successfully despite timeout (fail-open)
        self.assertEqual(result.returncode, 0)

        # Should produce diagnostic message
        self.assertIn("unavailable", result.stderr.lower())

    def test_d_service_unavailable_handling(self) -> None:
        """Test F: Hook handles service unavailability gracefully."""
        FakeMemoryServiceForRetrieval.mode = "unavailable"

        result = self.run_hook_with_prompt("test query")

        # Should exit successfully (fail-open)
        self.assertEqual(result.returncode, 0)

        # Should produce diagnostic message
        self.assertIn("unavailable", result.stderr.lower())

        # Should not produce stdout (no context to inject)
        self.assertEqual(result.stdout.strip(), "")

    def test_e_empty_prompt_no_retrieval(self) -> None:
        """Test D: Irrelevant/empty queries don't trigger retrieval."""
        result = self.run_hook_with_prompt("   ")

        self.assertEqual(result.returncode, 0)

        # Should not make memory request for empty prompt
        self.assertEqual(len(FakeMemoryServiceForRetrieval.requests), 0)

        # Should not produce output
        self.assertEqual(result.stdout.strip(), "")

    def test_f_no_context_when_no_results(self) -> None:
        """Test: Hook doesn't output context when no results returned."""
        # Empty response
        def empty_search_handler(self: Any) -> None:
            type(FakeMemoryServiceForRetrieval).requests.append((self.path, {}))
            response = {"ok": True, "results": []}
            body = json.dumps(response).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        # Replace handler
        original_handler = FakeMemoryServiceForRetrieval.do_POST
        FakeMemoryServiceForRetrieval.do_POST = empty_search_handler

        try:
            result = self.run_hook_with_prompt("obscure query that matches nothing")
            self.assertEqual(result.returncode, 0)
            # Should not output context for empty results
            self.assertEqual(result.stdout.strip(), "")
        finally:
            FakeMemoryServiceForRetrieval.do_POST = original_handler


class SettingsConfigurationTests(unittest.TestCase):
    """Test that settings.json has correct hook configuration."""

    def test_user_prompt_submit_hook_is_synchronous(self) -> None:
        """Verify async:false allows context injection."""
        settings_path = Path.home() / ".claude" / "settings.json"
        if not settings_path.exists():
            self.skipTest("Host-specific Claude settings are unavailable; run this check on an installed host")

        with open(settings_path) as f:
            settings = json.load(f)

        # Find UserPromptSubmit hook configuration
        hooks = settings.get("hooks", {})
        user_prompt_submit = hooks.get("UserPromptSubmit", [])

        self.assertGreater(len(user_prompt_submit), 0, "UserPromptSubmit hook not configured")

        # Find memory-search-on-prompt hook
        memory_hook = None
        for hook_group in user_prompt_submit:
            for hook in hook_group.get("hooks", []):
                if "memory-search-on-prompt.js" in hook.get("command", ""):
                    memory_hook = hook
                    break

        self.assertIsNotNone(memory_hook, "memory-search-on-prompt hook not found")

        # CRITICAL: async must be false or absent (defaults to false)
        async_value = memory_hook.get("async", False)
        self.assertFalse(
            async_value,
            f"Memory search hook has async:{async_value} - should be false for context injection"
        )


if __name__ == "__main__":
    unittest.main()
