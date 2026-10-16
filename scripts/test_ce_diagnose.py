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

MESSENGER_PATH = Path(__file__).resolve().parents[1] / "session-messaging" / "messenger_service.py"
MESSENGER_SPEC = importlib.util.spec_from_file_location("ce_test_messenger_service", MESSENGER_PATH)
messenger_service = importlib.util.module_from_spec(MESSENGER_SPEC)
assert MESSENGER_SPEC.loader is not None
MESSENGER_SPEC.loader.exec_module(messenger_service)


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
             patch.object(diagnose, "messenger_round_trip", return_value=("pass", "round trip passed", 1)), \
             patch.object(diagnose, "unit_status", return_value=("pass", "active", 1)), \
             patch.object(diagnose, "shutil_which_systemctl", return_value=True), \
             patch.object(diagnose.subprocess, "run", return_value=completed), \
             contextlib.redirect_stdout(stdout):
            exit_code = diagnose.main()
        result = json.loads(stdout.getvalue())
        pipeline = next(item for item in result["results"] if item["step"] == "pipeline.learning_to_thompson")
        self.assertEqual(0, exit_code)
        self.assertEqual("pipeline-test", result["mode"])
        self.assertEqual("pass", pipeline["status"])
        self.assertIn("temporary", result["side_effects"])

    def test_messenger_round_trip_uses_transient_unique_event(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "messenger.sock"
            server = messenger_service.MessengerServer(path)
            worker = threading.Thread(target=server.serve_forever, daemon=True)
            worker.start()
            deadline = time.monotonic() + 1.0
            while not path.exists() and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertTrue(path.exists(), "messenger socket did not appear")
            status, detail, _elapsed = diagnose.messenger_round_trip(
                str(path), "test-correlation", timeout=1.0
            )
            server.stop()
            worker.join(timeout=2)
            self.assertEqual("pass", status, detail)

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


if __name__ == "__main__":
    unittest.main()
