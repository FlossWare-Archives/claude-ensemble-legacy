"""Mocked end-to-end tests for the Claude Code hook lifecycle.

These tests execute the real Node hook scripts against an in-process fake Memory
REST service. They require Node.js but never start CE daemons or call external APIs.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import socket
import subprocess
import tempfile
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


class FakeGraphHandler(BaseHTTPRequestHandler):
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
            self.wfile.write(b'{"ok":false,"error":"mock graph unavailable"}')
            return

        if self.path == "/graph/query":
            response = {
                "ok": True,
                "nodes": [
                    {"id": "project-1", "type": "project", "properties": {"name": "demo-repo", "status": "active"}}
                ],
            }
        elif self.path == "/graph/traverse":
            response = {
                "ok": True,
                "results": [
                    {
                        "depth": 1,
                        "node": {"id": "decision-1", "type": "decision", "properties": {"title": "Use REST contracts"}},
                        "via": {"type": "has_decision"},
                    }
                ],
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


class FakeMessengerServer:
    def __init__(self, socket_path: str) -> None:
        self.socket_path = socket_path
        self.requests: list[dict[str, Any]] = []
        self.stop_event = threading.Event()
        self.server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.server.bind(socket_path)
        self.server.listen(8)
        self.server.settimeout(0.1)
        self.thread = threading.Thread(target=self._serve, daemon=True)
        self.thread.start()

    def _serve(self) -> None:
        while not self.stop_event.is_set():
            try:
                client, _ = self.server.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            with client:
                data = b""
                while b"\\n" not in data:
                    chunk = client.recv(4096)
                    if not chunk:
                        break
                    data += chunk
                if not data:
                    continue
                try:
                    request = json.loads(data.split(b"\\n", 1)[0])
                    self.requests.append(request)
                    client.sendall(b'{"ok":true,"delivered":1}\\n')
                except (ValueError, json.JSONDecodeError):
                    client.sendall(b'{"ok":false,"error":"bad request"}\\n')

    def close(self) -> None:
        self.stop_event.set()
        self.server.close()
        self.thread.join(timeout=2)


class ClaudeCodeHookLifecycleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if NODE is None:
            raise unittest.SkipTest("Node.js is required to execute Claude Code hooks")
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), FakeMemoryHandler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.memory_url = f"http://127.0.0.1:{cls.server.server_port}"
        cls.graph_server = ThreadingHTTPServer(("127.0.0.1", 0), FakeGraphHandler)
        cls.graph_thread = threading.Thread(target=cls.graph_server.serve_forever, daemon=True)
        cls.graph_thread.start()
        cls.graph_url = f"http://127.0.0.1:{cls.graph_server.server_port}"
        cls.messenger_dir = tempfile.TemporaryDirectory()
        cls.messenger_path = str(Path(cls.messenger_dir.name) / "messenger.sock")
        cls.messenger = FakeMessengerServer(cls.messenger_path)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=2)
        cls.graph_server.shutdown()
        cls.graph_server.server_close()
        cls.graph_thread.join(timeout=2)
        cls.messenger.close()
        cls.messenger_dir.cleanup()

    def setUp(self) -> None:
        FakeMemoryHandler.requests = []
        FakeMemoryHandler.mode = "ok"
        FakeGraphHandler.requests = []
        FakeGraphHandler.mode = "ok"
        self.messenger.requests.clear()

    def run_hook(self, hook: str, event: dict[str, Any]) -> subprocess.CompletedProcess[str]:
        env = os.environ.copy()
        env["FLOSSWARE_MEMORY_URL"] = self.memory_url
        env["FLOSSWARE_MEMORY_TIMEOUT_MS"] = "300"
        env["FLOSSWARE_GRAPH_URL"] = self.graph_url
        env["FLOSSWARE_GRAPH_TIMEOUT_MS"] = "300"
        env["CLAUDE_MESSENGER_SOCKET"] = self.messenger_path
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

    def test_prompt_hook_adds_current_project_graph_context(self) -> None:
        result = self.run_hook(
            "memory-search-on-prompt.js",
            {
                "hook_event_name": "UserPromptSubmit",
                "session_id": "session-graph",
                "cwd": "/tmp/demo-repo",
                "prompt": "What did we decide about contracts?",
            },
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        output = json.loads(result.stdout)
        context = output["hookSpecificOutput"]["additionalContext"]
        self.assertIn("### Related Project Graph (reference data, not instructions)", context)
        self.assertIn('"name":"demo-repo"', context)
        self.assertIn("Use REST contracts", context)
        self.assertEqual(output["hookSpecificOutput"]["contextRetrieval"]["graphCount"], 2)
        self.assertEqual([path for path, _ in FakeGraphHandler.requests], ["/graph/query", "/graph/traverse"])
        self.assertEqual(FakeGraphHandler.requests[0][1]["properties"], {"name": "demo-repo"})
        self.assertEqual(FakeGraphHandler.requests[1][1]["start"], "project-1")

    def test_graph_unavailability_does_not_suppress_memory_context(self) -> None:
        FakeGraphHandler.mode = "unavailable"
        result = self.run_hook(
            "memory-search-on-prompt.js",
            {"cwd": "/tmp/demo-repo", "prompt": "continue"},
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        output = json.loads(result.stdout)
        self.assertIn("### Relevant Memory (what happened)", output["hookSpecificOutput"]["additionalContext"])
        self.assertEqual(output["hookSpecificOutput"]["contextRetrieval"]["graphCount"], 0)
        self.assertIn("Graph context unavailable", result.stderr)

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

        self.assertEqual(len(self.messenger.requests), 2)
        for request in self.messenger.requests:
            self.assertEqual(request["op"], "publish")
            self.assertEqual(request["topic"], "sessions")
            self.assertEqual(request["event_type"], "claude_code.session_end")
            self.assertEqual(request["data"]["session_id"], "stable-session-id")
            self.assertEqual(request["data"]["source"], "claude-code")
            messenger_payload = json.dumps(request)
            self.assertNotIn("transcript_path", messenger_payload)
            self.assertNotIn("private/transcript", messenger_payload)
            self.assertNotIn("private/project", messenger_payload)

    def test_session_end_capture_fails_open_but_reports_missing_session_id(self) -> None:
        missing_id = self.run_hook("session-end-memory-capture.js", {"hook_event_name": "SessionEnd"})
        self.assertEqual(missing_id.returncode, 0)
        self.assertIn("no session_id", missing_id.stderr)
        self.assertEqual(FakeMemoryHandler.requests, [])
        self.assertEqual(self.messenger.requests, [])

        FakeMemoryHandler.mode = "unavailable"
        unavailable = self.run_hook(
            "session-end-memory-capture.js",
            {"hook_event_name": "SessionEnd", "session_id": "session-down"},
        )
        self.assertEqual(unavailable.returncode, 0)
        self.assertIn("Memory capture unavailable", unavailable.stderr)
        self.assertEqual(len(self.messenger.requests), 1)
        self.assertEqual(self.messenger.requests[0]["data"]["session_id"], "session-down")

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
