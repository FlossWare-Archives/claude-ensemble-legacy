"""Mocked end-to-end tests for the Claude Code hook lifecycle.

These tests execute the real Node hook scripts against an in-process fake Memory
REST service. They require Node.js but never start CE daemons or call external APIs.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
HOOKS = ROOT / "hooks"
SETTINGS = ROOT / "settings.json.default"
NODE = shutil.which("node")


class FakeMemoryHandler(BaseHTTPRequestHandler):
    requests: list[tuple[str, dict[str, Any]]] = []
    mode = "ok"

    def log_message(self, *_args: Any) -> None:
        pass

    def do_POST(self) -> None:  # noqa: N802
        try:
            payload = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))))
        except (ValueError, json.JSONDecodeError):
            payload = {}
        type(self).requests.append((self.path, payload))

        if type(self).mode == "unavailable":
            self.send_response(503)
            self.end_headers()
            self.wfile.write(b'{"ok":false,"error":"mock unavailable"}')
            return

        if self.path == "/memory/search":
            response = {
                "ok": True,
                "results": [
                    {"content": "Relevant prior decision", "source": "memory", "file": "decision.md"},
                    {"content": "Current architecture fact", "source": "claude-code", "file": "claude-code-architecture.md"},
                    {"content": "Relevant prior decision", "source": "memory", "file": "decision.md"},
                    {"content": "", "source": "memory"},
                    {"content": 42, "source": "memory"},
                ],
            }
        elif self.path == "/memory/append-once":
            response = {
                "ok": True,
                "status": "stored",
                "event_id": payload.get("event_id"),
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


class ClaudeCodeHookLifecycleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if NODE is None:
            raise unittest.SkipTest("Node.js is required to execute Claude Code hooks")
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), FakeMemoryHandler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.memory_url = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=2)

    def setUp(self) -> None:
        FakeMemoryHandler.requests = []
        FakeMemoryHandler.mode = "ok"

    def run_hook(self, hook: str, event: dict[str, Any]) -> subprocess.CompletedProcess[str]:
        env = os.environ.copy()
        env["FLOSSWARE_MEMORY_URL"] = self.memory_url
        env["FLOSSWARE_MEMORY_TIMEOUT_MS"] = "300"
        return subprocess.run(
            [NODE, str(HOOKS / hook)],
            input=json.dumps(event),
            text=True,
            capture_output=True,
            timeout=5,
            env=env,
            check=False,
        )

    def test_prompt_hook_returns_deduplicated_memory_and_knowledge_context(self) -> None:
        result = self.run_hook(
            "memory-search-on-prompt.js",
            {"hook_event_name": "UserPromptSubmit", "session_id": "session-1", "prompt": "How did we decide the service boundary?"},
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        output = json.loads(result.stdout)
        hook_output = output["hookSpecificOutput"]
        self.assertEqual(hook_output["hookEventName"], "UserPromptSubmit")
        context = hook_output["additionalContext"]
        self.assertIn("### Relevant Memory (what happened)", context)
        self.assertIn("### Relevant Knowledge (what is currently known)", context)
        self.assertEqual(context.count("Relevant prior decision"), 1)
        self.assertIn("Current architecture fact", context)
        self.assertEqual(hook_output["contextRetrieval"]["memoryCount"], 1)
        self.assertEqual(hook_output["contextRetrieval"]["knowledgeCount"], 1)
        self.assertEqual(FakeMemoryHandler.requests[0][0], "/memory/search")
        self.assertEqual(FakeMemoryHandler.requests[0][1]["query"], "How did we decide the service boundary?")

    def test_prompt_hook_fails_open_when_memory_is_unavailable(self) -> None:
        FakeMemoryHandler.mode = "unavailable"
        result = self.run_hook("memory-search-on-prompt.js", {"prompt": "continue"})
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")
        self.assertIn("Memory search unavailable", result.stderr)

    def test_prompt_hook_does_not_query_for_empty_prompt(self) -> None:
        result = self.run_hook("memory-search-on-prompt.js", {"prompt": "  "})
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")
        self.assertEqual(FakeMemoryHandler.requests, [])

    def test_session_end_capture_is_stable_and_does_not_send_transcript(self) -> None:
        event = {
            "hook_event_name": "SessionEnd",
            "session_id": "stable-session-id",
            "transcript_path": "/private/transcript.jsonl",
            "cwd": "/private/project",
            "reason": "user_exit",
        }
        first = self.run_hook("session-end-memory-capture.js", event)
        second = self.run_hook("session-end-memory-capture.js", event)
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertEqual(len(FakeMemoryHandler.requests), 2)
        path1, request1 = FakeMemoryHandler.requests[0]
        path2, request2 = FakeMemoryHandler.requests[1]
        self.assertEqual(path1, "/memory/append-once")
        self.assertEqual(path2, "/memory/append-once")
        self.assertEqual(request1, request2)
        self.assertEqual(request1["name"], "session_events")
        self.assertEqual(request1["event_id"], request2["event_id"])
        expected_digest = hashlib.sha256(b"stable-session-id").hexdigest()
        self.assertEqual(request1["event_id"], f"claude-code:session-end:{expected_digest}")
        serialized = json.dumps(request1)
        self.assertNotIn("transcript_path", serialized)
        self.assertNotIn("private/transcript", serialized)
        self.assertNotIn("private/project", serialized)

    def test_session_end_capture_fails_open_but_reports_missing_session_id(self) -> None:
        missing_id = self.run_hook("session-end-memory-capture.js", {"hook_event_name": "SessionEnd"})
        self.assertEqual(missing_id.returncode, 0)
        self.assertIn("no session_id", missing_id.stderr)
        self.assertEqual(FakeMemoryHandler.requests, [])

        FakeMemoryHandler.mode = "unavailable"
        unavailable = self.run_hook(
            "session-end-memory-capture.js",
            {"hook_event_name": "SessionEnd", "session_id": "session-down"},
        )
        self.assertEqual(unavailable.returncode, 0)
        self.assertIn("Memory capture unavailable", unavailable.stderr)

    def test_default_settings_register_only_the_canonical_safe_lifecycle_hooks(self) -> None:
        settings = json.loads(SETTINGS.read_text(encoding="utf-8"))
        hooks = settings["hooks"]
        prompt_commands = [
            hook.get("command", "")
            for group in hooks.get("UserPromptSubmit", [])
            for hook in group.get("hooks", [])
        ]
        end_commands = [
            hook.get("command", "")
            for group in hooks.get("SessionEnd", [])
            for hook in group.get("hooks", [])
        ]
        self.assertTrue(any("memory-search-on-prompt.js" in command for command in prompt_commands))
        self.assertTrue(any("session-end-memory-capture.js" in command for command in end_commands))
        # Generic lifecycle events do not prove quality. Never wire them directly
        # to Thompson/GA mutation or to legacy historical-outcome replay.
        self.assertNotIn("Stop", hooks)
        self.assertNotIn("PostToolUse", hooks)
        all_commands = "\n".join(prompt_commands + end_commands)
        self.assertNotIn("post-task-analysis.js", all_commands)
        self.assertNotIn("post-workflow-learning.js", all_commands)
        self.assertNotIn("session-end-comprehensive-capture.sh", all_commands)


if __name__ == "__main__":
    unittest.main()
