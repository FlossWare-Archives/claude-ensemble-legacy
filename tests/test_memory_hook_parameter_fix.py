"""Tests for memory hook parameter mismatch and semantic search contract fixes."""

import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "memory-service"))
from memory_service import MemoryService, create_http_server


class MemoryHookParameterFixTests(unittest.TestCase):
    """Test parameter compatibility and semantic search endpoint integration."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.temp_dir = Path(tempfile.mkdtemp())
        cls.memory_dir = cls.temp_dir / "memory"
        cls.socket_path = cls.temp_dir / "memory.sock"

        cls.service = MemoryService(cls.socket_path, cls.memory_dir)
        cls.http_server = create_http_server(cls.service, "127.0.0.1", 0)
        cls.service.http_server = cls.http_server
        cls.service.http_port = cls.http_server.server_port
        cls.http_thread = threading.Thread(target=cls.http_server.serve_forever, daemon=True)
        cls.http_thread.start()

        cls.base_url = f"http://127.0.0.1:{cls.service.http_port}"

        # Seed test memory & knowledge
        cls.service.store.write_file(
            "decision_001",
            "# Decision 001\nWe use REST contracts for Memory retrieval."
        )
        cls.service.store.write_file(
            "claude-code-arch",
            "# Architecture\nClaude Ensemble memory hook communicates over HTTP REST."
        )

    @classmethod
    def tearDownClass(cls) -> None:
        cls.http_server.shutdown()
        cls.http_server.server_close()

    def test_semantic_search_accepts_top_k(self) -> None:
        """Verify Memory service accepts top_k parameter in search-semantic request."""
        import urllib.request
        req = urllib.request.Request(
            f"{self.base_url}/memory/search-semantic",
            data=json.dumps({"query": "REST contracts", "top_k": 5, "source": "memory"}).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req) as resp:
            data = json.loads(resp.read().decode())
        self.assertTrue(data.get("ok"))
        results = data.get("results", [])
        self.assertGreater(len(results), 0)
        self.assertEqual(results[0]["file"], "decision_001")

    def test_hook_uses_search_semantic_endpoint(self) -> None:
        """Verify prompt hook invokes /memory/search-semantic and retrieves context."""
        hook_path = ROOT / "hooks" / "memory-search-on-prompt.js"
        env = os.environ.copy()
        env["FLOSSWARE_MEMORY_URL"] = self.base_url
        env["FLOSSWARE_MEMORY_TIMEOUT_MS"] = "2000"

        event = {
            "hook_event_name": "UserPromptSubmit",
            "session_id": "parameter-fix-session",
            "prompt": "What do we use for Memory retrieval in REST contracts?"
        }

        proc = subprocess.run(
            ["node", str(hook_path)],
            input=json.dumps(event),
            text=True,
            capture_output=True,
            timeout=5,
            env=env,
            check=False
        )

        self.assertEqual(proc.returncode, 0, f"Hook failed: {proc.stderr}")
        payload = json.loads(proc.stdout)
        output = payload.get("hookSpecificOutput", {})
        self.assertEqual(output.get("hookEventName"), "UserPromptSubmit")
        context = output.get("additionalContext", "")
        self.assertIn("We use REST contracts for Memory retrieval.", context)

    def test_hook_handles_unavailable_service(self) -> None:
        """Verify prompt hook fails open when service URL is unreachable."""
        hook_path = ROOT / "hooks" / "memory-search-on-prompt.js"
        env = os.environ.copy()
        env["FLOSSWARE_MEMORY_URL"] = "http://127.0.0.1:1"

        event = {
            "hook_event_name": "UserPromptSubmit",
            "session_id": "unavailable-session",
            "prompt": "Test query when offline"
        }

        proc = subprocess.run(
            ["node", str(hook_path)],
            input=json.dumps(event),
            text=True,
            capture_output=True,
            timeout=5,
            env=env,
            check=False
        )

        self.assertEqual(proc.returncode, 0)
        self.assertEqual(proc.stdout.strip(), "")
        self.assertIn("Memory search unavailable", proc.stderr)


if __name__ == "__main__":
    unittest.main()
