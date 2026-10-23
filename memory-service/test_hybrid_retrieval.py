import concurrent.futures
import gc
import math
import os
import resource
import tempfile
import time
import unittest
from unittest.mock import Mock, patch
from types import SimpleNamespace
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
        self.model_patch = patch.object(HybridRetrievalIndex, "_model", return_value=object())
        self.embedding_patch.start()
        self.model_patch.start()

    def tearDown(self):
        self.embedding_patch.stop()
        self.model_patch.stop()

    def test_model_is_shared_between_index_instances(self):
        self.model_patch.stop()
        model = object()
        constructor = Mock(return_value=model)
        try:
            with tempfile.TemporaryDirectory() as tmp:
                HybridRetrievalIndex._MODEL_CACHE.pop("cache-test-model", None)
                with patch.dict(sys.modules, {"fastembed": SimpleNamespace(TextEmbedding=constructor)}):
                    first = HybridRetrievalIndex(Path(tmp) / "one.sqlite3", embedding_model="cache-test-model")
                    second = HybridRetrievalIndex(Path(tmp) / "two.sqlite3", embedding_model="cache-test-model")
                    self.assertIs(first._model(), model)
                    self.assertIs(second._model(), model)
                    constructor.assert_called_once_with(model_name="cache-test-model")
        finally:
            HybridRetrievalIndex._MODEL_CACHE.pop("cache-test-model", None)
            self.model_patch.start()

    def test_warm_corpus_preloads_model_before_readiness(self):
        with tempfile.TemporaryDirectory() as tmp:
            index = HybridRetrievalIndex(Path(tmp) / "index.sqlite3")
            with patch.object(index, "_model") as preload:
                index.warm_corpus(set())
            preload.assert_called_once_with()

    def test_warm_search_encodes_query_without_reembedding_documents(self):
        with tempfile.TemporaryDirectory() as tmp:
            index = HybridRetrievalIndex(Path(tmp) / "index.sqlite3")
            index.replace_document("target", "rev1", [{
                "header": "Architecture",
                "heading_path": "Architecture",
                "content": "Local SQLite stores persistent memory embeddings.",
            }])
            index.warm_corpus({"target"})
            query = "Where are memory embeddings stored?"
            with patch.object(index, "_embed", wraps=index._embed) as embed:
                results = index.search(query, 3, {"target"})
            self.assertTrue(results)
            embed.assert_called_once_with([query])

    def test_chunks_respect_character_limit_and_track_oversized_paragraph_lines(self):
        notes = "# Notes\n\n" + "".join(
            f"entry-{number:04d} alpha beta gamma delta\n" for number in range(260)
        )
        text = notes + "\n# Next\n\n" + ("Unicode café 漢字 🚀. " * 180)
        chunks = chunk_markdown(text, max_chars=180, overlap_chars=24)
        self.assertTrue(chunks)
        self.assertTrue(all(len(chunk["content"]) <= 180 for chunk in chunks))
        self.assertTrue(any(chunk["heading_path"] == "Notes" for chunk in chunks))
        self.assertTrue(any(chunk["heading_path"] == "Next" for chunk in chunks))
        self.assertTrue(any("café" in chunk["content"] and "🚀" in chunk["content"] for chunk in chunks))
        for chunk in chunks:
            offset = text.find(chunk["content"])
            self.assertGreaterEqual(offset, 0)
            expected_start = text[:offset].count("\n") + 1
            expected_end = text[:offset + len(chunk["content"]) - 1].count("\n") + 1
            self.assertEqual(chunk["start_line"], expected_start)
            self.assertEqual(chunk["end_line"], expected_end)

    def test_real_local_model_acceptance(self):
        # This is a required integration test, not an opt-in smoke test. Use a
        # pre-provisioned local directory when configured; otherwise provision
        # the same default model used by CE runtime.
        model_path = (
            os.environ.get("ENSEMBLE_MEMORY_EMBEDDING_MODEL")
            or "sentence-transformers/all-MiniLM-L6-v2"
        )
        self.embedding_patch.stop()
        self.model_patch.stop()
        try:
            with tempfile.TemporaryDirectory() as tmp:
                database = Path(tmp) / "index.sqlite3"
                index = HybridRetrievalIndex(database, embedding_model=model_path)
                index.replace_document("target", "v1", [{
                    "header": "Persistence",
                    "heading_path": "Architecture > Persistence",
                    "content": (
                        "Dense representations are persisted locally in the embedded "
                        "SQLite database owned by the memory service."
                    ),
                }])
                index.replace_document("lexical-distractor", "v1", [{
                    "header": "Networking",
                    "heading_path": "Operations > Networking",
                    "content": (
                        "The service exposes a loopback HTTP endpoint and a private "
                        "Unix domain socket for client requests."
                    ),
                }])
                index.replace_document("model-distractor", "v1", [{
                    "header": "Inference",
                    "heading_path": "Architecture > Inference",
                    "content": (
                        "The local transformer executes CPU inference and loads model "
                        "weights from the host cache during startup."
                    ),
                }])
                query = "Where does memory keep semantic vectors after a restart?"
                result = index.search(query, 3, {"target", "lexical-distractor", "model-distractor"})
                self.assertTrue(result)
                self.assertEqual(
                    result[0]["file"], "target",
                    "the real model should rank the semantic persistence target above distractors",
                )
                with index._connect() as db:
                    stored = db.execute(
                        "SELECT embedding, embedding_model FROM chunks WHERE file='target'"
                    ).fetchone()
                    self.assertTrue(stored["embedding"])
                    self.assertEqual(stored["embedding_model"], model_path)

                # Add a meaningful local corpus in one batch, then exercise the
                # concurrent query fan-out that the prompt hook creates.
                index.replace_document(
                    "corpus-distractors",
                    "v1",
                    [
                        {
                            "header": f"Topic {number}",
                            "heading_path": f"Operations > Topic {number}",
                            "content": (
                                f"Topic {number} documents routine service operations, "
                                f"socket lifecycle, routing behavior, and deployment checks. "
                                f"Unique marker operation-{number:03d}."
                            ),
                        }
                        for number in range(48)
                    ],
                )
                all_files = {
                    "target", "lexical-distractor", "model-distractor",
                    "corpus-distractors",
                }
                # Drop the first model instance before simulating a process
                # restart, then warm a fresh index from persisted vectors.
                del index
                gc.collect()
                restarted = HybridRetrievalIndex(database, embedding_model=model_path)
                restarted.warm_corpus(all_files)
                after_restart = restarted.search(query, 3, all_files)
                self.assertTrue(after_restart)
                self.assertEqual(after_restart[0]["file"], "target")

                queries = [
                    query,
                    "where are vector representations kept after restarting memory",
                    "which embedded database persists semantic representations",
                    "what survives when the process is restarted",
                    "where are transformer weights loaded from",
                    "how does the service expose private client requests",
                ]
                durations = []

                def run_query(prompt):
                    started = time.monotonic()
                    hits = restarted.search(prompt, 5, all_files)
                    return time.monotonic() - started, hits

                # Use the restarted index to ensure this is not an in-memory
                # document-vector warm cache inherited from the first instance.
                with concurrent.futures.ThreadPoolExecutor(max_workers=len(queries)) as pool:
                    concurrent_results = list(pool.map(run_query, queries))
                durations = [elapsed for elapsed, _ in concurrent_results]
                self.assertEqual(concurrent_results[0][1][0]["file"], "target")
                peak_rss_kib = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
                latency_budget = float(os.environ.get(
                    "ENSEMBLE_MEMORY_ACCEPTANCE_LATENCY_SECONDS", "1.5"
                ))
                memory_budget_kib = int(os.environ.get(
                    "ENSEMBLE_MEMORY_ACCEPTANCE_MEMORY_KIB", str(512 * 1024)
                ))
                print(
                    "real-model acceptance metrics: "
                    f"queries={len(queries)}, max_query_seconds={max(durations):.3f}, "
                    f"peak_rss_kib={peak_rss_kib}, "
                    f"latency_budget_seconds={latency_budget:g}, "
                    f"memory_budget_kib={memory_budget_kib}"
                )
                self.assertLessEqual(
                    max(durations), latency_budget,
                    "concurrent real-model query fan-out exceeded the configured prompt budget",
                )
                self.assertLessEqual(
                    peak_rss_kib, memory_budget_kib,
                    "real-model acceptance process exceeded the configured Memory budget",
                )
        finally:
            self.embedding_patch.start()
            self.model_patch.start()

    def test_search_rejects_stale_model_identity_instead_of_truncating_vectors(self):
        with tempfile.TemporaryDirectory() as tmp:
            index = HybridRetrievalIndex(Path(tmp) / "index.sqlite3")
            index.replace_document("target", "rev1", [{
                "header": "Persistence",
                "heading_path": "Persistence",
                "content": "The service stores semantic vectors in SQLite.",
            }])
            with index._connect() as db:
                db.execute(
                    "UPDATE chunks SET embedding_model=? WHERE file=?",
                    ("previous-local-model", "target"),
                )
            with self.assertRaisesRegex(RuntimeError, "different model"):
                index.search("Where are semantic vectors stored?", 3, {"target"})

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

    def test_partial_document_embedding_backfill_repairs_later_chunks(self):
        class FakeEmbeddingIndex(HybridRetrievalIndex):
            def __init__(self, path, embedding_model):
                super().__init__(path, embedding_model=embedding_model)
                self.embed_calls = 0

            def _embed(self, texts):
                self.embed_calls += 1
                return [[1.0, 0.0] for _ in texts]

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "index.sqlite3"
            index = FakeEmbeddingIndex(path, embedding_model="test-model")
            chunks = [
                {"header": "One", "heading_path": "One", "content": "First chunk content."},
                {"header": "Two", "heading_path": "One > Two", "content": "Second chunk content."},
            ]
            index.replace_document("multi", "rev1", chunks)
            with index._connect() as db:
                db.execute(
                    "UPDATE chunks SET embedding=NULL WHERE file=? AND ordinal=1",
                    ("multi",),
                )
            calls_before = index.embed_calls
            index.replace_document("multi", "rev1", chunks)
            self.assertEqual(index.embed_calls, calls_before + 1)
            with index._connect() as db:
                rows = db.execute(
                    "SELECT embedding FROM chunks WHERE file=? ORDER BY ordinal", ("multi",)
                ).fetchall()
            self.assertEqual(len(rows), 2)
            self.assertTrue(all(row["embedding"] for row in rows))

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
