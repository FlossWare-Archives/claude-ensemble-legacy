"""Tests for read-only CE doctor diagnostics."""
from __future__ import annotations

import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.error import URLError

from tools.claude_config.lib import doctor


class HealthHandler(BaseHTTPRequestHandler):
    payload = {"ok": True}

    def do_GET(self):
        body = json.dumps(self.payload).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):
        pass


class DoctorHealthTests(unittest.TestCase):
    def setUp(self):
        self.server = HTTPServer(("127.0.0.1", 0), HealthHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.server.server_port}/health"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=1)

    def test_health_endpoint_requires_ok_true(self):
        HealthHandler.payload = {"ok": True}
        self.assertEqual(("pass", "HTTP 200; JSON ok=true"), doctor.http_health(self.url))

    def test_health_endpoint_rejects_negative_acknowledgement(self):
        HealthHandler.payload = {"ok": False, "error": "synthetic failure"}
        status, detail = doctor.http_health(self.url)
        self.assertEqual("fail", status)
        self.assertIn("ok=true", detail)

    def test_health_endpoint_rejects_non_json(self):
        HealthHandler.payload = ["not", "an", "object"]
        status, detail = doctor.http_health(self.url)
        self.assertEqual("fail", status)
        self.assertIn("ok=true", detail)

    def test_unreachable_endpoint_is_warning_not_exception(self):
        self.server.shutdown()
        self.server.server_close()
        self.assertEqual("warn", doctor.http_health(self.url, timeout=0.1)[0])


if __name__ == "__main__":
    unittest.main()
