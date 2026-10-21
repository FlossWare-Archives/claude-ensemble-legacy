"""Unit tests for the correlated CE diagnostic runner."""
from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import socket
import sys
import time
import tempfile
import threading
import unittest
from unittest.mock import patch
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

SCRIPT = Path(__file__).resolve().with_name("ce-diagnose.py")
SPEC = importlib.util.spec_from_file_location("ce_diagnose", SCRIPT)
diagnose = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(diagnose)

class ProbeHandler(BaseHTTPRequestHandler):
    payload = {"ok": True}
    received_correlation = None

    def do_GET(self):
        type(self).received_correlation = self.headers.get("X-Correlation-ID")
        raw = json.dumps(type(self).payload).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, *_args):
        pass


class DiagnoseTests(unittest.TestCase):
    def test_default_hook_dir_respects_ce_profile_isolation(self):
        with patch.dict("os.environ", {}, clear=True):
            self.assertEqual(
                str(Path.home() / ".FlossWare" / "claude-ensemble" / "shared" / "claude-code" / "hooks"),
                diagnose.default_hook_dir(),
            )
        with patch.dict("os.environ", {"ENSEMBLE_CLAUDE_CODE_CONFIG_DIR": "~/ce-profile"}, clear=True):
            self.assertEqual(str(Path.home() / "ce-profile" / "hooks"), diagnose.default_hook_dir())
        with patch.dict("os.environ", {"CLAUDE_HOOK_DIR": "~/explicit-hooks"}, clear=True):
            self.assertEqual(str(Path.home() / "explicit-hooks"), diagnose.default_hook_dir())

    def setUp(self):
        ProbeHandler.payload = {"ok": True}
        self.httpd = HTTPServer(("127.0.0.1", 0), ProbeHandler)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.httpd.server_port}"

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=1)

    def test_http_probe_propagates_correlation_and_requires_ack(self):
        correlation_id = "diagnostic-test-correlation"
        status, _detail, payload, _elapsed = diagnose.http_request(
            self.base, "/health", correlation_id
        )
        self.assertEqual("pass", status)
        self.assertEqual({"ok": True}, payload)
        self.assertEqual(correlation_id, ProbeHandler.received_correlation)

    def test_http_probe_rejects_negative_ack(self):
        ProbeHandler.payload = {"ok": False, "error": "no"}
        status, detail, _payload, _elapsed = diagnose.http_request(
            self.base, "/health", "negative-test"
        )
        self.assertEqual("fail", status)
        self.assertIn("ok=true", detail)
        ProbeHandler.payload = {"ok": True}

    def test_unreachable_http_endpoint_is_a_failure_result(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        status, _detail, _payload, elapsed = diagnose.http_request(
            self.base, "/health", "timeout-test", timeout=0.1
        )
        self.assertEqual("fail", status)
        self.assertGreaterEqual(elapsed, 0)

    def test_pipeline_test_mode_reports_isolated_result(self):
        stdout = io.StringIO()
        completed = __import__("subprocess").CompletedProcess(
            args=["python", "test_ce_pipeline.py"], returncode=0, stdout="", stderr=""
        )
        with patch.object(sys, "argv", ["ce-diagnose.py", "--service", "gateway", "--pipeline-test", "--json"]), \
             patch.object(diagnose, "http_request", return_value=("pass", "HTTP 200; ok=true", {"ok": True}, 1)), \
             patch.object(diagnose, "socket_ping", return_value=("pass", "Unix-socket ping acknowledged", 1)), \
             patch.object(diagnose.subprocess, "run", return_value=completed), \
             contextlib.redirect_stdout(stdout):
            exit_code = diagnose.main()
        result = json.loads(stdout.getvalue())
        pipeline = next(item for item in result["results"] if item["step"] == "pipeline.learning_to_thompson")
        self.assertEqual(0, exit_code)
        self.assertEqual("pipeline-test", result["mode"])
        self.assertEqual("pass", pipeline["status"])
        self.assertIn("temporary", result["side_effects"])

    def test_unix_socket_ping_requires_positive_ack(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "probe.sock")
            server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            server.bind(path)
            server.listen(1)

            def serve():
                conn, _ = server.accept()
                with conn:
                    data = conn.recv(4096)
                    request = json.loads(data.decode().strip())
                    self.assertEqual("ping", request["action"])
                    conn.sendall(b'{"ok":true,"message":"pong"}\n')
                server.close()

            worker = threading.Thread(target=serve, daemon=True)
            worker.start()
            status, detail, _elapsed = diagnose.socket_ping(path, "thompson", "socket-test")
            worker.join(timeout=1)
            self.assertEqual("pass", status)
            self.assertIn("acknowledged", detail)


    def test_installed_hook_smoke_runs_prompt_and_opt_in_session_end_scripts(self):
        with tempfile.TemporaryDirectory() as tmp:
            hook_dir = Path(tmp)
            (hook_dir / "memory-search-on-prompt.js").write_text(
                "process.stdin.resume(); process.stdin.on('end', () => "
                "process.stdout.write(JSON.stringify({hookSpecificOutput: "
                "{hookEventName: 'UserPromptSubmit', additionalContext: 'mock context'}})));",
                encoding="utf-8",
            )
            (hook_dir / "session-end-memory-capture.js").write_text(
                "process.stdin.resume(); process.stdin.on('end', () => {});",
                encoding="utf-8",
            )
            results = diagnose.installed_hook_smoke(
                hook_dir, "smoke-test-correlation", timeout=1.0,
                include_session_end=True,
            )
        self.assertEqual(
            ["hooks.user_prompt_submit", "hooks.session_end"],
            [item["step"] for item in results],
        )
        self.assertEqual(["pass", "pass"], [item["status"] for item in results])



    def test_installed_hook_smoke_caps_graph_timeout_to_hook_limit(self):
        with tempfile.TemporaryDirectory() as tmp:
            hook_dir = Path(tmp)
            (hook_dir / "memory-search-on-prompt.js").write_text("// mocked by subprocess", encoding="utf-8")
            captured = {}
            completed = __import__("subprocess").CompletedProcess(
                args=["node", "memory-search-on-prompt.js"],
                returncode=0,
                stdout=json.dumps({"hookSpecificOutput": {
                    "hookEventName": "UserPromptSubmit",
                    "additionalContext": "mock context",
                }}),
                stderr="",
            )

            def fake_run(*_args, **kwargs):
                captured.update(kwargs["env"])
                return completed

            with patch.object(diagnose.shutil, "which", return_value="/usr/bin/node"), \
                 patch.object(diagnose.subprocess, "run", side_effect=fake_run):
                results = diagnose.installed_hook_smoke(
                    hook_dir, "timeout-cap-test", timeout=10.0,
                )

        self.assertEqual("10000", captured["FLOSSWARE_MEMORY_TIMEOUT_MS"])
        self.assertEqual("5000", captured["FLOSSWARE_GRAPH_TIMEOUT_MS"])
        self.assertEqual("pass", results[0]["status"])



if __name__ == "__main__":
    unittest.main()
