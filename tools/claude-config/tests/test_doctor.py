"""Tests for read-only CE doctor diagnostics."""
from __future__ import annotations

import json
import importlib.util
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

DOCTOR_PATH = Path(__file__).resolve().parents[1] / "lib" / "doctor.py"
SPEC = importlib.util.spec_from_file_location("ce_doctor", DOCTOR_PATH)
doctor = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(doctor)


class HealthHandler(BaseHTTPRequestHandler):
    payload = {"ok": True}
    status_code = 200

    def do_GET(self):
        body = json.dumps(self.payload).encode("utf-8")
        self.send_response(type(self).status_code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):
        pass


class DoctorHealthTests(unittest.TestCase):
    def setUp(self):
        HealthHandler.payload = {"ok": True}
        HealthHandler.status_code = 200
        self.server = HTTPServer(("127.0.0.1", 0), HealthHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.server.server_port}/health"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=1)

    def test_provider_check_does_not_inspect_default_profile(self):
        status, detail = doctor.provider_health("/usr/bin/claude", None)
        self.assertEqual("skip", status)
        self.assertIn("default profile was not inspected", detail)

    def test_provider_check_accepts_first_party_anthropic_profile(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            profile = root / "ce-profile"
            profile.mkdir()
            executable = root / "fake-claude"
            executable.write_text(
                "#!/usr/bin/env python3\nimport json\nprint(json.dumps({'loggedIn': True, 'apiProvider': 'firstParty'}))\n",
                encoding="utf-8",
            )
            executable.chmod(0o755)
            status, detail = doctor.provider_health(str(executable), str(profile), "anthropic")
            self.assertEqual("pass", status, detail)

    def test_provider_check_rejects_blank_expected_provider(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            profile = root / "ce-profile"
            profile.mkdir()
            executable = root / "fake-claude"
            executable.write_text(
                "#!/usr/bin/env python3\nimport json\nprint(json.dumps({'loggedIn': True, 'apiProvider': 'firstParty'}))\n",
                encoding="utf-8",
            )
            executable.chmod(0o755)
            status, detail = doctor.provider_health(str(executable), str(profile), "")
            self.assertEqual("fail", status)
            self.assertIn("expected provider is blank", detail)

    def test_provider_check_rejects_unauthenticated_profile(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            profile = root / "ce-profile"
            profile.mkdir()
            executable = root / "fake-claude"
            executable.write_text(
                "#!/usr/bin/env python3\nimport json\nprint(json.dumps({'loggedIn': False, 'apiProvider': 'firstParty'}))\n",
                encoding="utf-8",
            )
            executable.chmod(0o755)
            status, detail = doctor.provider_health(str(executable), str(profile), "anthropic")
            self.assertEqual("fail", status)
            self.assertIn("not authenticated", detail)

    def test_provider_check_rejects_mismatched_provider(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            profile = root / "ce-profile"
            profile.mkdir()
            executable = root / "fake-claude"
            executable.write_text(
                "#!/usr/bin/env python3\nimport json\nprint(json.dumps({'loggedIn': True, 'apiProvider': 'vertex'}))\n",
                encoding="utf-8",
            )
            executable.chmod(0o755)
            status, detail = doctor.provider_health(str(executable), str(profile), "anthropic")
            self.assertEqual("fail", status)
            self.assertIn("expected 'anthropic'", detail)

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

    def test_health_endpoint_rejects_oversized_response(self):
        HealthHandler.payload = "x" * (64 * 1024)
        status, detail = doctor.http_health(self.url)
        self.assertEqual("fail", status)
        self.assertIn("exceeded the 64 KiB limit", detail)

    def test_http_error_is_reported_as_failure(self):
        HealthHandler.status_code = 503
        status, detail = doctor.http_health(self.url)
        self.assertEqual("fail", status)
        self.assertIn("HTTP 503", detail)

    def test_unreachable_endpoint_is_warning_not_exception(self):
        self.server.shutdown()
        self.server.server_close()
        self.assertEqual("warn", doctor.http_health(self.url, timeout=0.1)[0])


if __name__ == "__main__":
    unittest.main()
