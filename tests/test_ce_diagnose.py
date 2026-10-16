"""Regression tests for prompt-hook diagnostics in scripts/ce-diagnose.py."""
from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
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


if __name__ == "__main__":
    unittest.main()
