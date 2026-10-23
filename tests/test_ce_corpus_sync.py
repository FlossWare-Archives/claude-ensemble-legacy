import importlib.util
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

MODULE_PATH = Path(__file__).resolve().parents[1] / "tools" / "ce-corpus-sync.py"
SPEC = importlib.util.spec_from_file_location("ce_corpus_sync", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(MODULE)


class CorpusSyncOrchestratorTest(unittest.TestCase):
    def _args(self, root):
        project = root / "project"
        project.mkdir()
        return {
            "repo_root": root,
            "claude_home": root / ".claude",
            "project_roots": [project],
            "memory_url": "http://127.0.0.1:8767",
            "memory_dir": root / "data" / "memory",
            "source_archive": root / "data" / "memory-sources",
            "transcript_archive": root / "data" / "transcripts",
            "timeout": 5.0,
        }

    def test_inventory_errors_prevent_any_apply(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            calls = []

            def fake_run(command):
                calls.append(command)
                if "claude-code-source-sync.py" in command[1]:
                    return 1, {"ok": False, "inventory_errors": [{"path": "/missing"}]}
                return 0, {"sources": [], "mapped_sources": 0, "unmapped": []}

            with patch.object(MODULE, "_run_json", side_effect=fake_run):
                result = MODULE.run_sync(**self._args(root), apply=True)

            self.assertFalse(result["ok"])
            self.assertEqual(len(calls), 2)
            self.assertTrue(result["preflight_errors"])

    def test_apply_runs_only_after_both_inventories_pass(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            calls = []

            def fake_run(command):
                calls.append(command)
                is_transcript = "claude-transcript-import.py" in command[1]
                applying = "--apply" in command
                if is_transcript and applying:
                    return 0, {"result": {"ok": True, "processed": 1}}
                if is_transcript:
                    return 0, {"sources": [], "mapped_sources": 1, "unmapped": []}
                if applying:
                    return 0, {"ok": True, "sync": {"ok": True}}
                return 0, {"ok": True, "source_count": 2, "scope_count": 1, "inventory_errors": []}

            with patch.object(MODULE, "_run_json", side_effect=fake_run):
                result = MODULE.run_sync(**self._args(root), apply=True)

            self.assertTrue(result["ok"])
            self.assertEqual(len(calls), 4)
            self.assertIn("--apply", calls[2])
            self.assertIn("--apply", calls[3])

    def test_dry_run_never_applies(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            calls = []

            def fake_run(command):
                calls.append(command)
                if "claude-transcript-import.py" in command[1]:
                    return 0, {"sources": [], "mapped_sources": 0, "unmapped": []}
                return 0, {"ok": True, "source_count": 0, "scope_count": 0, "inventory_errors": []}

            with patch.object(MODULE, "_run_json", side_effect=fake_run):
                result = MODULE.run_sync(**self._args(root), apply=False)

            self.assertTrue(result["ok"])
            self.assertEqual(len(calls), 2)
            self.assertTrue(all("--apply" not in command for command in calls))


if __name__ == "__main__":
    unittest.main()
