import os
import unittest
from pathlib import Path
from unittest.mock import patch

from shared import corpus_paths


class CorpusPathsTest(unittest.TestCase):
    def test_defaults_share_install_data_root(self):
        with patch.dict(os.environ, {}, clear=True):
            root = Path.home() / ".FlossWare" / "claude-ensemble" / "shared" / "data"
            self.assertEqual(corpus_paths.corpus_data_root(), root)
            self.assertEqual(corpus_paths.memory_index_dir(), root / "memory")
            self.assertEqual(corpus_paths.memory_sources_dir(), root / "memory-sources")
            self.assertEqual(corpus_paths.knowledge_dir(), root / "knowledge")
            self.assertEqual(corpus_paths.graph_store_path(), root / "graph.json")

    def test_relative_override_is_rejected(self):
        with patch.dict(os.environ, {"ENSEMBLE_MEMORY_DIR": "relative/path"}):
            with self.assertRaises(ValueError):
                corpus_paths.memory_index_dir()


if __name__ == "__main__":
    unittest.main()
