"""Regression tests for prompt-hook diagnostics in scripts/ce-diagnose.py."""
from __future__ import annotations

import importlib.util
import io
import json
import tempfile
import unittest
from pathlib import Path
from contextlib import redirect_stdout
from unittest.mock import patch


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "ce-diagnose.py"
SPEC = importlib.util.spec_from_file_location("ce_diagnose", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
ce_diagnose = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ce_diagnose)


class InstalledHookSmokeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.hook_dir = Path(self.temp.name)
        (self.hook_dir / "memory-search-on-prompt.js").write_text("// fixture")
        self.correlation_id = "test-correlation"

    def run_smoke(self, stdout: str, stderr: str = "", *, require_context: bool = False, require_knowledge: bool = False):
        completed = type("Completed", (), {
            "returncode": 0,
            "stdout": stdout,
            "stderr": stderr,
        })()
        with (
            patch.object(ce_diagnose.shutil, "which", return_value="/usr/bin/node"),
            patch.object(ce_diagnose.subprocess, "run", return_value=completed),
        ):
            return ce_diagnose.installed_hook_smoke(
                self.hook_dir, self.correlation_id, 1.0,
                require_context=require_context,
                require_knowledge=require_knowledge,
            )[0]

    def test_empty_retrieval_is_warning_not_false_success(self) -> None:
        diagnostic = {
            "event": "completed",
            "memoryStatus": "ok",
            "memoryResultCount": 0,
            "graphStatus": "ok",
            "graphCount": 0,
            "contextEmitted": False,
        }
        result = self.run_smoke(
            "",
            "CE_HOOK_DIAGNOSTICS " + json.dumps(diagnostic) + "\n",
        )
        self.assertEqual(result["status"], "warn")
        self.assertIn("emitted no context", result["detail"])
        self.assertIn("results=0", result["detail"])

    def test_required_context_fails_when_retrieval_is_empty(self) -> None:
        result = self.run_smoke("", require_context=True)
        self.assertEqual(result["status"], "fail")

    def test_context_payload_reports_retrieval_counts(self) -> None:
        payload = {
            "hookSpecificOutput": {
                "hookEventName": "UserPromptSubmit",
                "additionalContext": "Relevant Claude Ensemble Context",
                "contextRetrieval": {
                    "memoryCount": 2,
                    "knowledgeCount": 1,
                    "graphCount": 3,
                },
            },
        }
        result = self.run_smoke(json.dumps(payload), require_context=True)
        self.assertEqual(result["status"], "pass")
        self.assertIn("Memory=2", result["detail"])
        self.assertIn("Knowledge=1", result["detail"])
        self.assertIn("Graph=3", result["detail"])

    def test_prompt_context_acceptance_fails_when_knowledge_is_missing(self) -> None:
        payload = {
            "hookSpecificOutput": {
                "hookEventName": "UserPromptSubmit",
                "additionalContext": "Relevant Claude Ensemble Context with an operational event",
                "contextRetrieval": {
                    "memoryCount": 9,
                    "knowledgeCount": 0,
                    "graphCount": 0,
                },
            },
        }
        result = self.run_smoke(json.dumps(payload), require_context=True, require_knowledge=True)
        self.assertEqual(result["status"], "fail")
        self.assertIn("no Knowledge documents", result["detail"])
        self.assertIn("knowledge-sync --root /path/to/project", result["remediation"])

    def test_prompt_context_acceptance_passes_with_knowledge(self) -> None:
        payload = {
            "hookSpecificOutput": {
                "hookEventName": "UserPromptSubmit",
                "additionalContext": "Relevant Claude Ensemble Context",
                "contextRetrieval": {
                    "memoryCount": 9,
                    "knowledgeCount": 1,
                    "graphCount": 0,
                },
            },
        }
        result = self.run_smoke(json.dumps(payload), require_context=True, require_knowledge=True)
        self.assertEqual(result["status"], "pass")
        self.assertIn("Knowledge=1", result["detail"])



    def test_each_json_step_contains_correlation_and_bounded_evidence(self):
        output = io.StringIO()
        with (
            patch("sys.argv", ["ce-diagnose.py", "--service", "gateway", "--json"]),
            patch.object(
                ce_diagnose,
                "http_request",
                return_value=("pass", "HTTP 200; ok=true", {"ok": True}, 3),
            ),
            redirect_stdout(output),
        ):
            exit_code = ce_diagnose.main()

        report = json.loads(output.getvalue())
        self.assertEqual(0, exit_code)
        self.assertEqual(2, len(report["results"]))
        for item in report["results"]:
            self.assertEqual(report["correlation_id"], item["correlation_id"])
            self.assertEqual(item["detail"], item["evidence"])
            self.assertEqual("pass", item["status"])
            self.assertGreaterEqual(item["duration_ms"], 0)
            self.assertTrue(item["endpoint"])
            self.assertIn("remediation", item)

    def test_deep_memory_operation_failure_is_reported_at_exact_step(self):
        output = io.StringIO()
        responses = iter([
            ("pass", "HTTP 200; ok=true", {"ok": True}, 2),
            ("fail", "HTTP 503; dependency_unavailable", {"ok": False}, 4),
        ])
        with (
            patch("sys.argv", ["ce-diagnose.py", "--service", "memory", "--deep", "--json"]),
            patch.object(ce_diagnose, "http_request", side_effect=lambda *args, **kwargs: next(responses)),
            redirect_stdout(output),
        ):
            exit_code = ce_diagnose.main()

        report = json.loads(output.getvalue())
        self.assertEqual(1, exit_code)
        self.assertFalse(report["ok"])
        self.assertEqual(["memory.health.via_gateway", "memory.read.via_gateway"], [item["step"] for item in report["results"]])
        self.assertEqual(["pass", "fail"], [item["status"] for item in report["results"]])
        self.assertTrue(all(item["correlation_id"] == report["correlation_id"] for item in report["results"]))
        self.assertEqual(1, report["failed"])

    def test_pipeline_failure_is_reported_and_fails_process(self):
        output = io.StringIO()
        failed = type("Completed", (), {"returncode": 1, "stdout": "", "stderr": "injected failure"})()
        with (
            patch("sys.argv", ["ce-diagnose.py", "--service", "gateway", "--pipeline-test", "--json"]),
            patch.object(ce_diagnose, "http_request", return_value=("pass", "HTTP 200; ok=true", {"ok": True}, 1)),
            patch.object(ce_diagnose.subprocess, "run", return_value=failed),
            redirect_stdout(output),
        ):
            exit_code = ce_diagnose.main()

        report = json.loads(output.getvalue())
        pipeline = next(item for item in report["results"] if item["step"] == "pipeline.learning_to_thompson")
        self.assertEqual(1, exit_code)
        self.assertEqual("fail", pipeline["status"])
        self.assertIn("exit 1", pipeline["detail"])
        self.assertEqual(report["correlation_id"], pipeline["correlation_id"])

    def test_core_service_selector_runs_thompson_probe(self):
        output = io.StringIO()
        with (
            patch("sys.argv", ["ce-diagnose.py", "--service", "thompson", "--json"]),
            patch.object(ce_diagnose, "http_request", return_value=("pass", "HTTP 200; ok=true", {"ok": True}, 1)),
            redirect_stdout(output),
        ):
            exit_code = ce_diagnose.main()

        report = json.loads(output.getvalue())
        self.assertEqual(0, exit_code)
        self.assertEqual(2, len(report["results"]))
        self.assertEqual(0, report["skipped"])
        self.assertEqual(["thompson.health", "thompson.ping"], [item["step"] for item in report["results"]])
        self.assertTrue(all(item["status"] == "pass" for item in report["results"]))


    def test_non_json_http_response_reports_headers_without_leaking_body(self):
        class FakeHeaders(dict):
            def get(self, key, default=None):
                return super().get(key, default)

        class FakeResponse:
            status = 200
            headers = FakeHeaders({
                "Content-Type": "text/html; charset=utf-8",
                "Server": "example-proxy",
            })
            def __enter__(self):
                return self
            def __exit__(self, *_args):
                return False
            def read(self, _limit):
                return b"<html>secret-token=do-not-print</html>"

        with patch.object(ce_diagnose.urllib.request, "urlopen", return_value=FakeResponse()):
            status, detail, payload, _elapsed = ce_diagnose.http_request(
                "http://127.0.0.1:8080", "/api/v1/decision/query/problems",
                "test-correlation",
            )

        self.assertEqual("fail", status)
        self.assertIsNone(payload)
        self.assertIn("HTTP 200; response was not JSON", detail)
        self.assertIn("Content-Type=text/html; charset=utf-8", detail)
        self.assertIn("Server=example-proxy", detail)
        self.assertNotIn("secret-token", detail)

    def test_oversized_response_is_not_misreported_as_malformed_json(self):
        class FakeHeaders(dict):
            def get(self, key, default=None):
                return super().get(key, default)

        class FakeResponse:
            status = 200
            headers = FakeHeaders({
                "Content-Type": "application/json",
                "Server": "ClaudeEnsembleREST/1",
                "Content-Length": str(150_000),
            })
            def __enter__(self):
                return self
            def __exit__(self, *_args):
                return False
            def read(self, limit):
                return b"x" * limit

        with patch.object(ce_diagnose.urllib.request, "urlopen", return_value=FakeResponse()):
            status, detail, payload, _elapsed = ce_diagnose.http_request(
                "http://127.0.0.1:8080", "/api/v1/decision/query/problems",
                "test-correlation",
            )

        self.assertEqual("fail", status)
        self.assertIsNone(payload)
        self.assertIn("response exceeded diagnostic body limit", detail)
        self.assertIn("Content-Length=150000", detail)
        self.assertNotIn("response was not JSON", detail)

if __name__ == "__main__":
    unittest.main()
