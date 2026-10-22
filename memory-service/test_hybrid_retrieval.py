import math
import os
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from hybrid_retrieval import HybridRetrievalIndex, chunk_markdown

# Unit tests use deterministic local vectors. Real model download and inference are
# covered by install/runtime acceptance, not by every lexical unit test.
def _test_embeddings(self, texts):
    if not self.embedding_model_name:
        return None
    vectors = [[float(len(text.split()) or 1), 1.0] for text in texts]
    return [
        [component / math.sqrt(sum(value * value for value in vector)) for component in vector]
        for vector in vectors
    ]



class HybridRetrievalTests(unittest.TestCase):
    def setUp(self):
        self.embedding_patch = patch.object(HybridRetrievalIndex, "_embed", _test_embeddings)
        self.embedding_patch.start()

    def tearDown(self):
        self.embedding_patch.stop()

    def test_chunks_respect_character_limit_and_track_oversized_paragraph_lines(self):
        notes = "# Notes\\n\\n" + "".join(
            f"entry-{number:04d} alpha beta gamma delta\\n" for number in range(260)
        )
        text = notes + "\\n# Next\\n\\n" + ("Unicode café 漢字 🚀. " * 180)
        chunks = chunk_markdown(text, max_chars=180, overlap_chars=24)
        self.assertTrue(chunks)
        self.assertTrue(all(len(chunk["content"]) <= 180 for chunk in chunks))
        self.assertTrue(any(chunk["heading_path"] == "Notes" for chunk in chunks))
        self.assertTrue(any(chunk["heading_path"] == "Next" for chunk in chunks))
        self.assertTrue(any("café" in chunk["content"] and "🚀" in chunk["content"] for chunk in chunks))
        for chunk in chunks:
            offset = text.find(chunk["content"])
            self.assertGreaterEqual(offset, 0)
            expected_start = text[:offset].count("\\n") + 1
            expected_end = text[:offset + len(chunk["content"]) - 1].count("\\n") + 1
            self.assertEqual(chunk["start_line"], expected_start)
            self.assertEqual(chunk["end_line"], expected_end)

    @unittest.skipUnless(
        os.environ.get("ENSEMBLE_RUN_LOCAL_EMBEDDING_ACCEPTANCE") == "1",
        "set ENSEMBLE_RUN_LOCAL_EMBEDDING_ACCEPTANCE=1 with a local model directory",
    )
    def test_real_local_model_acceptance(self):
        model_path = os.environ.get("ENSEMBLE_MEMORY_EMBEDDING_MODEL")
        self.assertTrue(model_path and Path(model_path).is_dir(),
                        "ENSEMBLE_MEMORY_EMBEDDING_MODEL must point to a local model directory")
        self.embedding_patch.stop()
        try:
            with tempfile.TemporaryDirectory() as tmp:
                index = HybridRetrievalIndex(Path(tmp) / "index.sqlite3", embedding_model=model_path)
                index.replace_document("target", "v1", [{
                    "header": "Architecture",
                    "heading_path": "Architecture",
                    "content": "The isolated memory service stores vectors in local SQLite.",
                }])
                result = index.search("Where are the memory vectors stored?", 3, {"target"})
                self.assertTrue(result)
                self.assertEqual(result[0]["file"], "target")
                with index._connect() as db:
                    self.assertTrue(db.execute("SELECT embedding FROM chunks").fetchone()["embedding"])
        finally:
            self.embedding_patch.start()

    def test_heading_hierarchy_is_preserved(self):
        chunks = chunk_markdown('# Architecture\n\nIntro.\n\n## Install root\n\nThe default deployment root is ~/.FlossWare/claude-ensemble.\n')
        target = next(item for item in chunks if 'default deployment root' in item['content'])
        self.assertEqual(target['heading_path'], 'Architecture > Install root')

    def test_natural_language_query_finds_install_root_and_persists(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'index.sqlite3'
            index = HybridRetrievalIndex(path)
            index.replace_document('installation', 'rev1', [{'header': 'Install root', 'heading_path': 'Architecture > Install root', 'content': 'The default deployment root is ~/.FlossWare/claude-ensemble. Release code is immutable under releases/MAJOR.MINOR.'}])
            index.replace_document('unrelated', 'rev1', [{'header': 'Quick reference', 'heading_path': 'Quick reference', 'content': 'Claude Ensemble supports memory retrieval, model selection, and hooks.'}])
            results = index.search('What install root did we establish as the default for Claude Ensemble?', 5, {'installation', 'unrelated'})
            self.assertEqual(results[0]['file'], 'installation')
            reopened = HybridRetrievalIndex(path)
            self.assertEqual(reopened.search('default deployment root', 3, {'installation'})[0]['file'], 'installation')

    def test_exact_phrase_scores_above_related_content(self):
        with tempfile.TemporaryDirectory() as tmp:
            index = HybridRetrievalIndex(Path(tmp) / 'index.sqlite3')
            index.replace_document('exact', '1', [{'header': 'Path', 'heading_path': 'Path', 'content': 'The default path is /opt/claude-ensemble.'}])
            index.replace_document('related', '1', [{'header': 'Notes', 'heading_path': 'Notes', 'content': 'Claude Ensemble has a configurable installation location.'}])
            results = index.search('The default path is /opt/claude-ensemble.', 5, {'exact', 'related'})
            self.assertEqual(results[0]['file'], 'exact')


    def test_configured_embeddings_are_persisted_and_used_for_search(self):
        class FakeEmbeddingIndex(HybridRetrievalIndex):
            def _embed(self, texts):
                return [
                    [1.0, 0.0] if ("deployment root" in text.casefold() or "install root" in text.casefold()) else [0.0, 1.0]
                    for text in texts
                ]

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "index.sqlite3"
            index = FakeEmbeddingIndex(path, embedding_model="test-model")
            index.replace_document("installation", "rev1", [{
                "header": "Install root",
                "heading_path": "Architecture > Install root",
                "content": "The default deployment root is ~/.FlossWare/claude-ensemble.",
            }])
            index.replace_document("unrelated", "rev1", [{
                "header": "General",
                "heading_path": "General",
                "content": "Claude Ensemble has hooks and memory retrieval.",
            }])
            with index._connect() as db:
                row = db.execute("SELECT embedding, embedding_model FROM chunks WHERE file='installation'").fetchone()
            self.assertIsNotNone(row["embedding"])
            self.assertEqual(row["embedding_model"], "test-model")
            results = index.search("What install root did we establish as the default for Claude Ensemble?", 5, {"installation", "unrelated"})
            self.assertEqual(results[0]["file"], "installation")
            self.assertEqual(results[0]["retrieval"], "hybrid")

    def test_enabling_embeddings_backfills_existing_lexical_index(self):
        class FakeEmbeddingIndex(HybridRetrievalIndex):
            def _embed(self, texts):
                return [[1.0, 0.0] for _ in texts]

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "index.sqlite3"
            chunk = [{"header": "Root", "heading_path": "Root", "content": "The default deployment root is ~/.FlossWare/claude-ensemble."}]
            lexical = HybridRetrievalIndex(path, embedding_model="")
            lexical.replace_document("installation", "rev1", chunk)
            with lexical._connect() as db:
                legacy_row = db.execute("SELECT embedding, embedding_model FROM chunks WHERE file='installation'").fetchone()
            self.assertIsNone(legacy_row["embedding"])
            self.assertIsNone(legacy_row["embedding_model"])
            embedded = FakeEmbeddingIndex(path, embedding_model="test-model")
            embedded.replace_document("installation", "rev1", chunk)
            with embedded._connect() as db:
                row = db.execute("SELECT embedding, embedding_model FROM chunks WHERE file='installation'").fetchone()
            self.assertIsNotNone(row["embedding"])
            self.assertEqual(row["embedding_model"], "test-model")



if __name__ == '__main__':
    unittest.main()
