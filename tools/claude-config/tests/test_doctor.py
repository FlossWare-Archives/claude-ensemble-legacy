"""Tests for read-only CE doctor diagnostics."""
from __future__ import annotations

import json
import importlib.util
import io
import subprocess
import sys
import tempfile
import threading
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

DOCTOR_PATH = Path(__file__).resolve().parents[1] / "lib" / "doctor.py"
SPEC = importlib.util.spec_from_file_location("ce_doctor", DOCTOR_PATH)
doctor = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = doctor
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
    def test_main_uses_injected_home_and_repository_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            home = root / "alternate-home"
            claude_home = home / ".claude"
            (claude_home / "hooks").mkdir(parents=True)
            (claude_home / ".flossware-claude-config").mkdir()
            (claude_home / "settings.json").write_text('{"hooks": {}}', encoding="utf-8")
            (claude_home / "hooks" / "memory-search-on-prompt.js").write_text("// prompt", encoding="utf-8")
            (claude_home / "hooks" / "session-end-memory-capture.js").write_text("// session", encoding="utf-8")
            (claude_home / ".flossware-claude-config" / "manifest.json").write_text("{}", encoding="utf-8")
            repo_root = root / "alternate-repo"
            validator = repo_root / "tools" / "claude-config" / "lib" / "json_tool.py"
            validator.parent.mkdir(parents=True)
            validator.write_text("# test validator", encoding="utf-8")
            config = doctor.DoctorConfig(
                home=home,
                repo_root=repo_root,
                environ={
                    "ENSEMBLE_GATEWAY_URL": "http://injected.invalid",
                    "ENSEMBLE_CLAUDE_CODE_CONFIG_DIR": str(claude_home),
                },
            )
            completed = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
            output = io.StringIO()
            health_urls = []

            def fake_health(url):
                health_urls.append(url)
                return "pass", "test health"

            with (
                patch("sys.argv", ["doctor", "--json"]),
                patch.object(doctor.shutil, "which", side_effect=[None, None]),
                patch.object(doctor, "run_bounded", return_value=(completed, None)) as run_bounded,
                patch.object(doctor, "http_health", side_effect=fake_health),
                redirect_stdout(output),
            ):
                exit_code = doctor.main(config)

            report = json.loads(output.getvalue())
            checks = {item["code"]: item for item in report["checks"]}
            self.assertEqual(1, exit_code)  # Missing Claude CLI is reported without touching the host profile.
            self.assertEqual(str(claude_home), checks["CLAUDE_HOME"]["detail"])
            self.assertEqual("pass", checks["HOOK_REGISTRATION"]["status"])
            self.assertEqual("fail", checks["CLAUDE_PROVIDER_POLICY"]["status"])
            self.assertIn("Claude Code executable not found", checks["CLAUDE_PROVIDER_POLICY"]["detail"])
            self.assertEqual(str(validator), run_bounded.call_args.args[0][1])
            self.assertIn("http://injected.invalid/api/v1/health", health_urls)
            self.assertIn("http://127.0.0.1:8767/health", health_urls)

    def test_main_skips_all_profile_file_checks_without_explicit_ce_profile(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            home = root / "alternate-home"
            default_profile = home / ".claude"
            default_profile.mkdir(parents=True)
            (default_profile / "settings.json").write_text(
                '{"workProfile": "must not be read"}', encoding="utf-8"
            )
            config = doctor.DoctorConfig(
                home=home,
                repo_root=root,
                environ={},
            )
            output = io.StringIO()
            with (
                patch("sys.argv", ["doctor", "--json"]),
                patch.object(doctor.shutil, "which", return_value=None),
                patch.object(doctor, "http_health", return_value=("pass", "test health")),
                redirect_stdout(output),
            ):
                exit_code = doctor.main(config)

            report = json.loads(output.getvalue())
            checks = {item["code"]: item for item in report["checks"]}
            for code in (
                "CLAUDE_HOME", "SETTINGS_MISSING", "PROMPT_HOOK_MISSING",
                "SESSION_HOOK_MISSING", "MANIFEST_MISSING", "SETTINGS_JSON",
                "HOOK_REGISTRATION",
            ):
                self.assertEqual("skip", checks[code]["status"], code)
                self.assertIn("default Claude profile was not inspected", checks[code]["detail"])
            self.assertEqual("skip", checks["CLAUDE_PROVIDER_POLICY"]["status"])
            self.assertEqual(1, exit_code)  # Only the missing CLI fails.

    def test_explicit_profile_missing_managed_files_are_failures(self):
        with tempfile.TemporaryDirectory() as tmp:
            profile = Path(tmp) / "acceptance-profile"
            profile.mkdir()
            config = doctor.DoctorConfig(
                home=Path(tmp),
                repo_root=Path(tmp),
                environ={"ENSEMBLE_CLAUDE_CODE_CONFIG_DIR": str(profile)},
            )
            output = io.StringIO()
            with (
                patch("sys.argv", ["doctor", "--json"]),
                patch.object(doctor.shutil, "which", return_value=None),
                patch.object(doctor, "http_health", return_value=("pass", "test health")),
                redirect_stdout(output),
            ):
                exit_code = doctor.main(config)
            report = json.loads(output.getvalue())
            checks = {item["code"]: item for item in report["checks"]}
            for code in ("SETTINGS_MISSING", "PROMPT_HOOK_MISSING", "SESSION_HOOK_MISSING", "MANIFEST_MISSING"):
                self.assertEqual("fail", checks[code]["status"], code)
            self.assertEqual(1, exit_code)

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

    def test_provider_check_defaults_to_vertex(self):
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
            status, detail = doctor.provider_health(str(executable), str(profile))
            self.assertEqual("pass", status, detail)
            self.assertIn("expected provider 'vertex'", detail)

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
                "#!/usr/bin/env python3\nimport json\nprint(json.dumps({'loggedIn': True, 'apiProvider': 'anthropic'}))\n",
                encoding="utf-8",
            )
            executable.chmod(0o755)
            status, detail = doctor.provider_health(str(executable), str(profile), "vertex")
            self.assertEqual("fail", status)
            self.assertIn("expected 'vertex'", detail)

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


class DoctorRedactionAndTimeoutTests(unittest.TestCase):
    def test_provider_check_never_echoes_auth_payload_secrets(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            profile = root / "ce-profile"
            profile.mkdir()
            executable = root / "fake-claude"
            executable.write_text(
                "#!/usr/bin/env python3\n"
                "import json\n"
                "print(json.dumps({'loggedIn': True, 'apiProvider': 'vertex', "
                "'accessToken': 'do-not-print-this-token'}))\n",
                encoding="utf-8",
            )
            executable.chmod(0o755)

            status, detail = doctor.provider_health(
                str(executable), str(profile), "vertex"
            )

            self.assertEqual("pass", status)
            self.assertIn("expected provider 'vertex'", detail)
            self.assertNotIn("do-not-print-this-token", detail)

    def test_bounded_subprocess_reports_timeout_without_echoing_output(self):
        timeout = subprocess.TimeoutExpired(
            cmd=["claude", "auth", "status"],
            timeout=0.25,
            output="secret-output-must-not-appear",
        )
        with patch.object(doctor.subprocess, "run", side_effect=timeout) as run:
            result, error = doctor.run_bounded(
                ["claude", "auth", "status"], timeout=0.25
            )

        self.assertIsNone(result)
        self.assertEqual("TimeoutExpired", error)
        self.assertEqual(0.25, run.call_args.kwargs["timeout"])
        self.assertNotIn("secret-output-must-not-appear", error)


if __name__ == "__main__":
    unittest.main()
