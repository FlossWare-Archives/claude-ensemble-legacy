"""Mocked end-to-end tests for the Claude Code hook lifecycle.

These tests execute the real Node hook scripts against fake Memory and Graph REST
the four core services. They require Node.js but never start CE
daemons or call external APIs.
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import os
import shutil
import socket
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

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=2)
        cls.graph_server.shutdown()
        cls.graph_server.server_close()
        cls.graph_thread.join(timeout=2)

    @staticmethod
    def _free_ports(count: int) -> list[int]:
        # Keep all sockets bound until every distinct port has been allocated.
        # Calling a single-port helper repeatedly can hand back the same port.
        with contextlib.ExitStack() as stack:
            listeners = [stack.enter_context(socket.socket()) for _ in range(count)]
            for listener in listeners:
                listener.bind(("127.0.0.1", 0))
            return [int(listener.getsockname()[1]) for listener in listeners]

    def setUp(self) -> None:
        FakeMemoryHandler.requests = []
        FakeMemoryHandler.mode = "ok"
        FakeGraphHandler.requests = []
        FakeGraphHandler.mode = "ok"

    def run_hook(self, hook: str, event: dict[str, Any]) -> subprocess.CompletedProcess[str]:
        env = os.environ.copy()
        env["FLOSSWARE_MEMORY_URL"] = self.memory_url
        env["FLOSSWARE_MEMORY_TIMEOUT_MS"] = "1500"
        env["FLOSSWARE_GRAPH_URL"] = self.graph_url
        env["FLOSSWARE_GRAPH_TIMEOUT_MS"] = "300"
        env["CLAUDE_MESSENGER_SOCKET"] = "/nonexistent/claude-messenger.sock"
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
        # Memory and Knowledge searches run concurrently, so request arrival
        # order is not deterministic; assert on the requests by source instead.
        search_requests = [payload for path, payload in FakeMemoryHandler.requests if path == "/memory/search"]
        memory_requests = [payload for payload in search_requests if payload.get("source") == "memory"]
        knowledge_requests = [payload for payload in search_requests if payload.get("source") == "claude-code"]
        prompt = "How did we decide the service boundary?"
        self.assertTrue(memory_requests)
        self.assertTrue(knowledge_requests)
        self.assertIn(prompt, [payload["query"] for payload in memory_requests])
        self.assertIn(prompt, [payload["query"] for payload in knowledge_requests])
        for payload in search_requests:
            self.assertEqual(payload["limit"], 10)
        self.assertEqual(FakeGraphHandler.requests, [])

    def test_real_memory_service_rest_write_search_and_hook_context_injection(self) -> None:
        """Phase 4 End-to-End Context Test with real MemoryService daemon."""
        with tempfile.TemporaryDirectory(prefix="ce-memory-rest-test-") as temp_dir:
            temp_path = Path(temp_dir)
            memory_dir = temp_path / "memory"
            socket_path = temp_path / "memory.sock"

            sys.path.insert(0, str(ROOT / "memory-service"))
            from memory_service import MemoryService, create_http_server

            service = MemoryService(socket_path, memory_dir)
            http_server = create_http_server(service, "127.0.0.1", 0)
            service.http_server = http_server
            service.http_port = http_server.server_port
            http_thread = threading.Thread(target=http_server.serve_forever, daemon=True)
            http_thread.start()

            try:
                base_url = f"http://127.0.0.1:{service.http_port}"

                # Step A: Seed memory via REST write interface
                import urllib.request
                write_req = urllib.request.Request(
                    f"{base_url}/memory/write",
                    data=json.dumps({"name": "decision_001", "content": "# Decision 001\nWe use REST contracts for Memory retrieval."}).encode(),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urllib.request.urlopen(write_req) as resp:
                    write_resp = json.loads(resp.read().decode())
                self.assertTrue(write_resp.get("ok"))

                # Seed Knowledge document via REST ingest interface
                knowledge_content = "Claude Ensemble memory hook communicates over HTTP REST."
                knowledge_sha = hashlib.sha256(knowledge_content.encode()).hexdigest()
                ingest_req = urllib.request.Request(
                    f"{base_url}/memory/ingest",
                    data=json.dumps({
                        "path": "/tmp/project/ARCH.md",
                        "content": knowledge_content,
                        "sha256": knowledge_sha,
                        "metadata": {"scope": "/tmp/project"},
                    }).encode(),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urllib.request.urlopen(ingest_req) as resp:
                    ingest_resp = json.loads(resp.read().decode())
                self.assertTrue(ingest_resp.get("ok"))

                # The HTTP factory intentionally does not imply readiness. Warm the
                # index after seeding and before asserting positive search behavior.
                service._warm_search_index()

                # Step B: Retrieve via REST search interface directly
                search_req = urllib.request.Request(
                    f"{base_url}/memory/search",
                    data=json.dumps({"query": "REST contracts", "limit": 10, "source": "memory"}).encode(),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urllib.request.urlopen(search_req) as resp:
                    search_resp = json.loads(resp.read().decode())
                self.assertTrue(search_resp.get("ok"))
                self.assertGreater(len(search_resp.get("results", [])), 0)

                # Step C & D: Invoke UserPromptSubmit hook with prompt that retrieves seeded records
                env = os.environ.copy()
                env["FLOSSWARE_MEMORY_URL"] = base_url
                env["FLOSSWARE_MEMORY_TIMEOUT_MS"] = "2000"
                env["FLOSSWARE_GRAPH_URL"] = self.graph_url

                hook_proc = subprocess.run(
                    [NODE, str(HOOKS / "memory-search-on-prompt.js")],
                    input=json.dumps({
                        "hook_event_name": "UserPromptSubmit",
                        "session_id": "real-memory-test-session",
                        "prompt": "What do we use for Memory retrieval in REST contracts?",
                    }),
                    text=True,
                    capture_output=True,
                    timeout=5,
                    env=env,
                    check=False,
                )
                self.assertEqual(hook_proc.returncode, 0, hook_proc.stderr)
                hook_json = json.loads(hook_proc.stdout)
                additional_context = hook_json["hookSpecificOutput"]["additionalContext"]

                # Step E & F: Verify emitted context includes seeded memory & knowledge
                self.assertIn("We use REST contracts for Memory retrieval.", additional_context)
                self.assertIn("Claude Ensemble memory hook communicates over HTTP REST", additional_context)
                self.assertIn("### Relevant Memory (what happened)", additional_context)
                self.assertIn("### Relevant Knowledge (what is currently known)", additional_context)

            finally:
                http_server.shutdown()
                http_server.server_close()

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

        self.assertNotIn("Messenger", first.stderr + second.stderr, "SessionEnd must not depend on Messenger")

    def test_session_end_capture_fails_open_but_reports_missing_session_id(self) -> None:
        missing_id = self.run_hook("session-end-memory-capture.js", {"hook_event_name": "SessionEnd"})
        self.assertEqual(missing_id.returncode, 0)
        self.assertIn("no session_id", missing_id.stderr)
        self.assertEqual(FakeMemoryHandler.requests, [])
        self.assertNotIn("Messenger", missing_id.stderr)

        FakeMemoryHandler.mode = "unavailable"
        unavailable = self.run_hook(
            "session-end-memory-capture.js",
            {"hook_event_name": "SessionEnd", "session_id": "session-down"},
        )
        self.assertEqual(unavailable.returncode, 0)
        self.assertIn("Memory capture unavailable", unavailable.stderr)
        self.assertNotIn("Messenger", unavailable.stderr, "Memory failure must not trigger a Messenger dependency")

    def test_default_settings_register_only_the_canonical_safe_lifecycle_hooks(self) -> None:
        self.assertFalse((HOOKS / "workflow-completion-hook.js").exists())
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


    def test_end_to_end_prompt_outcome_and_session_end_workflow(self) -> None:
        """Exercise production hooks and real Learning/Thompson handlers in one lifecycle."""
        session_id = "e2e-session-contract-001"

        # Stage 1: the actual UserPromptSubmit hook retrieves Memory, Knowledge, and Graph.
        prompt = self.run_hook(
            "memory-search-on-prompt.js",
            {
                "hook_event_name": "UserPromptSubmit",
                "session_id": session_id,
                "cwd": "/tmp/demo-repo",
                "prompt": "Implement the service boundary using the existing decision",
            },
        )
        self.assertEqual(prompt.returncode, 0, prompt.stderr)
        prompt_payload = json.loads(prompt.stdout)
        context = prompt_payload["hookSpecificOutput"]["additionalContext"]
        self.assertIn("Relevant prior decision", context)
        self.assertIn("Current architecture fact", context)
        self.assertIn("Use REST contracts", context)

        # Stage 2: invoke the real Learning request handler and real Thompson service.
        # All state, logs, and sockets are isolated in a temporary directory.
        with tempfile.TemporaryDirectory(prefix="ce-workflow-e2e-") as temp_dir:
            root = Path(temp_dir)
            env = os.environ.copy()
            thompson_port, learning_port = self._free_ports(2)
            env.update({
                "HOME": str(root / "home"),
                "ENSEMBLE_LOG_DIR": str(root / "logs"),
                "ENSEMBLE_LEARNING_DIR": str(root / "learning"),
                "ENSEMBLE_RUNTIME_DIR": str(root / "runtime"),
                "ENSEMBLE_THOMPSON_SOCKET": str(root / "thompson.sock"),
                "ENSEMBLE_THOMPSON_HTTP_PORT": str(thompson_port),
                "ENSEMBLE_LEARNING_HTTP_PORT": str(learning_port),
                "ENSEMBLE_THOMPSON_STATE_FILE": str(root / "thompson-state.json"),
            })
            script = r'''
import json, os, socket, sys, tempfile, threading, time, urllib.request
from pathlib import Path

repo = Path(sys.argv[1])
root = Path(sys.argv[2])
sys.path.insert(0, str(repo))
sys.path.insert(0, str(repo / "learning-service"))
sys.path.insert(0, str(repo / "thompson-service"))

from learning_service import LearningService
from thompson_service import ThompsonService
from shared.thompson_client import ThompsonClient

class IsolatedMemory:
    def __init__(self): self.events = []
    def write_event(self, **event):
        self.events.append(event)
        return True

socket_path = root / "thompson.sock"
state_path = root / "thompson-state.json"
thompson_url = "http://127.0.0.1:" + os.environ["ENSEMBLE_THOMPSON_HTTP_PORT"]
learning_url = "http://127.0.0.1:" + os.environ["ENSEMBLE_LEARNING_HTTP_PORT"]
thompson = ThompsonService(socket_path, state_path)
worker = threading.Thread(target=thompson.start, daemon=True)
worker.start()
deadline = time.monotonic() + 5
while time.monotonic() < deadline:
    try:
        with urllib.request.urlopen(thompson_url + "/health", timeout=0.2) as response:
            if response.status == 200:
                break
    except Exception:
        time.sleep(0.02)
else:
    raise AssertionError(
        f"Thompson REST service failed to become ready at {thompson_url}/health"
    )

memory = IsolatedMemory()
learning = LearningService(root / "learning.sock", root / "learning", memory)
learning.thompson_client = ThompsonClient(service_url=thompson_url, timeout=1)
learning_worker = threading.Thread(target=learning.start, daemon=True)
learning_worker.start()
deadline = time.monotonic() + 5
while time.monotonic() < deadline:
    try:
        with urllib.request.urlopen(learning_url + "/health", timeout=0.2) as response:
            if response.status == 200:
                break
    except Exception:
        time.sleep(0.02)
else:
    learning.stop()
    thompson.stop()
    raise AssertionError(
        f"Learning REST service failed to become ready at {learning_url}/health"
    )
task_id = sys.argv[3]
request = {
    "op": "process_outcome", "task_id": task_id,
    "task_type": "claude-code-session", "model": "e2e-test-model",
    "rating": 5, "tokens": 17, "cost": 0.0,
}
try:
    http_request = urllib.request.Request(
        learning_url + "/learning",
        data=json.dumps(request).encode(),
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(http_request, timeout=5) as response:
        result = json.loads(response.read().decode())
    assert result.get("ok") is True, result
    assert result.get("thompson") is True, result
    assert result.get("checkpoint_advanced") is True, result
    assert learning.system.is_processed(task_id), "outcome checkpoint missing"
    stats = thompson.state.get_state()["models"].get("e2e-test-model")
    assert stats and stats["calls"] == 1 and stats["successes"] == 1, stats
    print(json.dumps({"ok": True, "task_id": task_id, "learning": result,
                      "thompson": stats, "memory_events": len(memory.events)}))
finally:
    learning.stop()
    thompson.stop()
    learning_worker.join(timeout=2)
    worker.join(timeout=2)
'''
            completed = subprocess.run(
                [sys.executable, "-c", script, str(ROOT), str(root), session_id],
                env=env, capture_output=True, text=True, timeout=15, check=False,
            )
            self.assertEqual(
                0, completed.returncode,
                msg=f"real Learning/Thompson path failed\nstdout:\n{completed.stdout}\nstderr:\n{completed.stderr}",
            )
            learning_result = json.loads(completed.stdout.strip().splitlines()[-1])
            self.assertEqual(session_id, learning_result["task_id"])
            self.assertEqual(1, learning_result["thompson"]["calls"])

        # Stage 3: SessionEnd captures only the same stable ID; it must not leak
        # transcript/project paths or independently award a learning outcome.
        end = self.run_hook(
            "session-end-memory-capture.js",
            {
                "hook_event_name": "SessionEnd",
                "session_id": session_id,
                "transcript_path": "/private/transcript.jsonl",
                "cwd": "/private/project",
                "reason": "user_exit",
            },
        )
        self.assertEqual(end.returncode, 0, end.stderr)
        capture = [payload for path, payload in FakeMemoryHandler.requests if path == "/memory/append-once"]
        self.assertEqual(1, len(capture))
        self.assertEqual(session_id, capture[0]["entry"]["session_id"])
        self.assertEqual("claude_code.session_end", capture[0]["entry"]["event_type"])
        self.assertNotIn("transcript_path", json.dumps(capture[0]))
        self.assertNotIn("/private/", json.dumps(capture[0]))
        self.assertNotIn("Messenger", end.stderr, "SessionEnd must not publish through Messenger")
        # SessionEnd is capture/event delivery only. Thompson was updated exactly
        # once by the explicit, evidence-bearing process_outcome request above.
        self.assertEqual(1, learning_result["thompson"]["calls"])



if __name__ == "__main__":
    unittest.main()
