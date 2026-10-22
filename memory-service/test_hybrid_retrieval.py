import tempfile
import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from hybrid_retrieval import HybridRetrievalIndex, chunk_markdown


class HybridRetrievalTests(unittest.TestCase):
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
            lexical = HybridRetrievalIndex(path)
            lexical.replace_document("installation", "rev1", chunk)
            embedded = FakeEmbeddingIndex(path, embedding_model="test-model")
            embedded.replace_document("installation", "rev1", chunk)
            with embedded._connect() as db:
                row = db.execute("SELECT embedding, embedding_model FROM chunks WHERE file='installation'").fetchone()
            self.assertIsNotNone(row["embedding"])
            self.assertEqual(row["embedding_model"], "test-model")


    def test_force_rebuilds_unchanged_signature(self):
        with tempfile.TemporaryDirectory() as tmp:
            index = HybridRetrievalIndex(Path(tmp) / "index.sqlite3")
            index.replace_document("notes", "same-signature", [{
                "header": "Old", "heading_path": "Old", "content": "old indexed content"
            }])
            index.replace_document("notes", "same-signature", [{
                "header": "New", "heading_path": "New", "content": "new indexed content"
            }], force=True)
            with index._connect() as db:
                row = db.execute("SELECT header, content FROM chunks WHERE file='notes'").fetchone()
            self.assertEqual(row["header"], "New")
            self.assertEqual(row["content"], "new indexed content")

    def test_incremental_backfill_repairs_partial_embeddings(self):
        class FakeEmbeddingIndex(HybridRetrievalIndex):
            def _embed(self, texts):
                return [[float(i + 1), 1.0] for i, _ in enumerate(texts)]

        with tempfile.TemporaryDirectory() as tmp:
            index = FakeEmbeddingIndex(Path(tmp) / "index.sqlite3", embedding_model="test-model")
            chunks = [
                {"header": "One", "heading_path": "One", "content": "first chunk content"},
                {"header": "Two", "heading_path": "Two", "content": "second chunk content"},
            ]
            index.replace_document("notes", "same-signature", chunks)
            with index._connect() as db:
                db.execute(
                    "UPDATE chunks SET embedding=NULL WHERE file='notes' AND ordinal=1"
                )
            index.replace_document("notes", "same-signature", chunks)
            with index._connect() as db:
                rows = db.execute(
                    "SELECT embedding, embedding_model FROM chunks WHERE file='notes' ORDER BY ordinal"
                ).fetchall()
            self.assertEqual(len(rows), 2)
            self.assertTrue(all(row["embedding"] for row in rows))
            self.assertTrue(all(row["embedding_model"] == "test-model" for row in rows))


    def test_embedding_backfill_preserves_chunks_across_model_migration(self):
        class FakeEmbeddingIndex(HybridRetrievalIndex):
            def _embed(self, texts):
                return [[float(len(self.embedding_model_name)), 1.0] for _ in texts]

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "index.sqlite3"
            old = FakeEmbeddingIndex(path, embedding_model="old-model")
            chunks = [
                {"header": "Root", "heading_path": "Architecture > Root", "content": "keep this exact chunk"}
            ]
            old.replace_document("notes", "signature", chunks)
            new = FakeEmbeddingIndex(path, embedding_model="new-model")
            count = new.reembed_document("notes")
            with new._connect() as db:
                row = db.execute(
                    "SELECT header, heading_path, content, embedding_model, embedding FROM chunks WHERE file='notes'"
                ).fetchone()
            self.assertEqual(count, 1)
            self.assertEqual(row["header"], "Root")
            self.assertEqual(row["heading_path"], "Architecture > Root")
            self.assertEqual(row["content"], "keep this exact chunk")
            self.assertEqual(row["embedding_model"], "new-model")
            self.assertEqual(row["embedding"], "[9.0, 1.0]")



if __name__ == '__main__':
    unittest.main()
