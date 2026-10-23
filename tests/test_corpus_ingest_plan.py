import tempfile
import unittest
from pathlib import Path

from tools.corpus_ingest_plan import inventory


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
