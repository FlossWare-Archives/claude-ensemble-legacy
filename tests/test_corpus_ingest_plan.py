import importlib.util
import tempfile
import unittest
from pathlib import Path

MODULE_PATH = Path(__file__).resolve().parents[1] / "tools" / "corpus-ingest-plan.py"
SPEC = importlib.util.spec_from_file_location("corpus_ingest_plan", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(MODULE)
inventory = MODULE.inventory


class CorpusIngestPlanTest(unittest.TestCase):
    def test_inventory_is_deduplicated_and_contains_no_source_text(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "CLAUDE.md").write_text("private source text")
            (root / "session.jsonl").write_text('{"message":"private transcript"}\n')
            report = inventory([root, root])
            self.assertEqual(report["summary"]["sources"], 2)
            self.assertEqual(report["summary"]["markdown"], 1)
            self.assertEqual(report["summary"]["transcripts"], 1)
            self.assertNotIn("private source text", str(report))
            self.assertNotIn("private transcript", str(report))


if __name__ == "__main__":
    unittest.main()
