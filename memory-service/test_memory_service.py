#!/usr/bin/env python3
"""Tests for the memory service security boundary."""

import json
import math
import os
import socket
import stat
import subprocess
import sys
import tempfile
import threading
import urllib.error
import urllib.request
import time
import unittest
from unittest.mock import patch
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SERVICE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(SERVICE_DIR))
from execution.context import ExecutionContext, ExecutionResult, ExecutionStatus  # noqa: E402
from memory_client import MemoryClient  # noqa: E402

from hybrid_retrieval import HybridRetrievalIndex  # noqa: E402


# Keep service tests offline and deterministic. Real model download/inference is
# covered by installation/runtime acceptance; focused tests verify hybrid ranking.
def _test_embeddings(self, texts):
    if not self.embedding_model_name:
        return None
    vectors = [[float(len(text.split()) or 1), 1.0] for text in texts]
    return [
        [component / math.sqrt(sum(value * value for value in vector)) for component in vector]
        for vector in vectors
    ]



# Keep the deterministic embedding substitute scoped to this test module;
# do not alter production behavior during pytest collection or later modules.
_embedding_patch = None
_model_patch = None


def setUpModule():
    global _embedding_patch, _model_patch
    _embedding_patch = patch.object(HybridRetrievalIndex, "_embed", _test_embeddings)
    _model_patch = patch.object(HybridRetrievalIndex, "_model", return_value=object())
    _embedding_patch.start()
    _model_patch.start()


def tearDownModule():
    if _embedding_patch is not None:
        _embedding_patch.stop()
    if _model_patch is not None:
        _model_patch.stop()


SERVICE = Path(__file__).with_name("memory_service.py")


def send_request(socket_path: Path, request: dict) -> dict:
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
        sock.settimeout(2)
        sock.connect(str(socket_path))
        sock.sendall((json.dumps(request) + "\n").encode("utf-8"))
        response = b""
        while b"\n" not in response:
            chunk = sock.recv(4096)
            if not chunk:
                break
            response += chunk
        return json.loads(response.decode("utf-8").strip())


class MemoryClientContextTest(unittest.TestCase):

    def test_append_accepts_canonical_context_without_duplicate_schema(self):
        client = MemoryClient(socket_path=Path("/does/not/exist"))
        captured = {}

        def send_request(request):
            captured.update(request=request)
            return {"ok": True}

        client._send_request = send_request
        context = ExecutionContext(
            request_id="request-1",
            execution_id="execution-1",
            parent_execution_id="parent-1",
            objective="objective",
            artifact="artifact",
            requirements=("requirement",),
            evidence=("evidence",),
            constraints=("constraint",),
            lineage=("parent-1", "execution-1"),
            stage="review",
            worker_id="worker-1",
        )

        self.assertTrue(client.append("context", {"result": "actual"}, context=context))
        self.assertEqual(captured["request"]["entry"]["result"], "actual")
        self.assertEqual(
            captured["request"]["entry"]["execution_context"],
            context.to_dict(),
        )

        restored = ExecutionContext.from_dict(captured["request"]["entry"]["execution_context"])
        self.assertEqual(restored, context)


class MemorySearchRegressionTest(unittest.TestCase):

    def test_operational_learning_artifact_is_findable_by_identifiers(self):
        from memory_service import MemoryStore

        run_id = "integration-test-a51b9cf7-a58f-45bf-ad81-56999e1730e5"
        event_id = (
            "learning-artifact:integration.test.learning-memory:" + run_id
        )
        artifact_type = "integration.test.learning-memory"
        document_name = "operational-learning.artifact-601d3c51a987cc5ffcbca47f"
        content = (
            "# Operational Event: learning.artifact\n\n"
            f"**Event ID:** {event_id}\n\n"
            + json.dumps(
                {
                    "event_id": event_id,
                    "event_type": "learning.artifact",
                    "payload": {
                        "artifact_type": artifact_type,
                        "run_id": run_id,
                    },
                },
                indent=2,
            )
            + "\n"
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            store = MemoryStore(Path(temp_dir))
            self.assertTrue(store.write_file(document_name, content))

            for query in (run_id, event_id, artifact_type, "learning artifact"):
                with self.subTest(query=query):
                    results = store.search_semantic(query)
                    self.assertTrue(results, f"search returned no results for {query}")
                    self.assertEqual(results[0]["file"], document_name)
                    self.assertIn(run_id, store.read_file(results[0]["file"]))

    def test_warmup_reports_embedding_failure_as_incomplete(self):
        from memory_service import MemoryStore

        with tempfile.TemporaryDirectory() as temp_dir:
            store = MemoryStore(Path(temp_dir))
            (Path(temp_dir) / "deployment.md").write_text(
                "# Deployment\n\nThe deployment root is local.", encoding="utf-8"
            )
            with patch.object(
                store._hybrid_index, "_embed",
                side_effect=RuntimeError("sentence-transformers is unavailable"),
            ):
                report = store.warm_search_index_report(max_passes=1)
            self.assertFalse(report["complete"])
            self.assertIn("deployment.md", report["failed_files"])
            self.assertIn("sentence-transformers is unavailable", report["failed_files"]["deployment.md"])

    def test_empty_corpus_model_load_failure_never_reports_ready(self):
        from memory_service import MemoryService

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            service = MemoryService(root / "memory.sock", root / "memory")
            with patch.object(
                service.store._hybrid_index,
                "_model",
                side_effect=RuntimeError("local model is unavailable"),
            ):
                service._warm_search_index()
            status = service.search_index_status()
            self.assertEqual(status["state"], "failed")
            self.assertIn("local model is unavailable", status["error"])

    def test_prose_search_does_not_rebuild_or_reembed_documents(self):
        from memory_service import MemoryStore

        with tempfile.TemporaryDirectory() as temp_dir:
            store = MemoryStore(Path(temp_dir))
            store.write_file("deployment", "# Deployment\n\nThe default deployment root is local.")
            self.assertIn(frozenset({"deployment"}), store._hybrid_index._corpus_cache)
            with patch.object(store, "_indexed_chunks", side_effect=AssertionError("prompt search rebuilt a document")):
                with patch.object(store._hybrid_index, "replace_document", side_effect=AssertionError("prompt search wrote the hybrid index")):
                    results = store.search_semantic("What is the default deployment root?", source="memory")
            self.assertTrue(results)
            self.assertEqual(results[0]["file"], "deployment")

    def test_exact_identifier_match_ranks_ahead_of_related_prose(self):
        from memory_service import MemoryStore

        run_id = "integration-test-unique-1234567890"
        with tempfile.TemporaryDirectory() as temp_dir:
            store = MemoryStore(Path(temp_dir))
            store.write_file(
                "related-learning-notes",
                "# Learning notes\n\nA learning artifact records a task outcome.",
            )
            store.write_file(
                "operational-learning-artifact",
                f"# Operational Event\n\nStored run identifier: {run_id}\n",
            )

            results = store.search_semantic(run_id)
            self.assertTrue(results)
            self.assertEqual(results[0]["file"], "operational-learning-artifact")
            self.assertEqual(results[0]["score"], 1.0)

    def test_unknown_identifier_returns_no_results(self):
        from memory_service import MemoryStore

        with tempfile.TemporaryDirectory() as temp_dir:
            store = MemoryStore(Path(temp_dir))
            store.write_file(
                "known-learning-artifact",
                "# Learning artifact\n\nrun_id: known-run-123456",
            )
            results = store.search_semantic("missing-run-987654321")
            self.assertEqual(results, [])

    def test_rest_search_returns_matching_document_content(self):
        from memory_service import MemoryService, create_http_server

        run_id = "integration-test-rest-search-1234567890"
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            service = MemoryService(root / "memory.sock", root / "memory")
            document_name = "operational-learning-artifact"
            content = (
                "# Operational Event: learning.artifact\n\n"
                f'{{"artifact_type":"integration.test.learning-memory","run_id":"{run_id}"}}\n'
            )
            service.store.write_file(document_name, content)
            service._warm_search_index()
            server = create_http_server(service, "127.0.0.1", 0)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                request = urllib.request.Request(
                    f"http://127.0.0.1:{server.server_address[1]}/memory/search-semantic",
                    data=json.dumps({"query": run_id, "top_k": 5}).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urllib.request.urlopen(request, timeout=3) as response:
                    result = json.loads(response.read().decode("utf-8"))

                self.assertTrue(result["ok"])
                self.assertTrue(result["results"])
                self.assertEqual(result["results"][0]["file"], document_name)
                self.assertIn(run_id, result["results"][0]["content"])
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=3)

    def test_rest_search_can_filter_memory_and_durable_knowledge(self):
        from memory_service import MemoryService, create_http_server

        query = "What is the distinction between Memory and Knowledge, and does SessionEnd establish task quality?"
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            service = MemoryService(root / "memory.sock", root / "memory")
            for index in range(12):
                service.store.write_file(
                    f"operational-learning-artifact-{index}",
                    "# Operational Event\n\nMemory event from SessionEnd collaboration.result; task quality remains unknown.",
                )
            knowledge_path = root / "project" / "CLAUDE.md"
            knowledge_path.parent.mkdir()
            knowledge_content = (
                "# Lifecycle Contract\n\n"
                "Memory is episodic experience; Knowledge is durable current facts. "
                "SessionEnd captures metadata only and does not establish task quality."
            )
            import hashlib
            service.store.ingest_claude_markdown(
                str(knowledge_path),
                knowledge_content,
                hashlib.sha256(knowledge_content.encode("utf-8")).hexdigest(),
                {"scope": str(knowledge_path.parent)},
            )
            distractor_path = root / "other-project" / "CLAUDE.md"
            distractor_path.parent.mkdir()
            distractor_content = (
                "# Collaboration Event Contract\n\n"
                "Collaboration events record operational task metadata and memory state. "
                "Knowledge is maintained separately from transient collaboration results."
            )
            service.store.ingest_claude_markdown(
                str(distractor_path),
                distractor_content,
                hashlib.sha256(distractor_content.encode("utf-8")).hexdigest(),
                {"scope": str(distractor_path.parent)},
            )
            service._warm_search_index()
            server = create_http_server(service, "127.0.0.1", 0)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                def search(source):
                    request = urllib.request.Request(
                        f"http://127.0.0.1:{server.server_address[1]}/memory/search",
                        data=json.dumps({"query": query, "limit": 10, "source": source}).encode("utf-8"),
                        headers={"Content-Type": "application/json"},
                        method="POST",
                    )
                    with urllib.request.urlopen(request, timeout=3) as response:
                        return json.loads(response.read().decode("utf-8"))

                memory = search("memory")
                knowledge = search("claude-code")
                self.assertTrue(memory["ok"])
                self.assertTrue(knowledge["ok"])
                self.assertTrue(memory["results"])
                self.assertTrue(knowledge["results"])
                self.assertTrue(all(not item["file"].startswith("claude-code-") for item in memory["results"]))
                self.assertTrue(all(item["file"].startswith("claude-code-") for item in knowledge["results"]))
                self.assertIn("does not establish task quality", knowledge["results"][0]["content"])
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=3)

    def test_knowledge_search_loads_ingest_index_once(self):
        from memory_service import MemoryStore

        with tempfile.TemporaryDirectory() as temp_dir:
            store = MemoryStore(Path(temp_dir))
            import hashlib

            for index in range(4):
                source_path = Path(temp_dir) / f"project-{index}" / "CLAUDE.md"
                content = (
                    f"# Durable Knowledge {index}\n\n"
                    "Architecture decisions and project preferences are durable knowledge."
                )
                store.ingest_claude_markdown(
                    str(source_path),
                    content,
                    hashlib.sha256(content.encode("utf-8")).hexdigest(),
                    {"scope": str(source_path.parent)},
                )

            with patch.object(
                store, "_load_ingest_index", wraps=store._load_ingest_index
            ) as load_index:
                results = store.search_semantic(
                    "architecture decisions durable knowledge",
                    source="claude-code",
                )

            self.assertTrue(results)
            self.assertTrue(all(item["file"].startswith("claude-code-") for item in results))
            self.assertEqual(load_index.call_count, 1)

    def test_identifier_prefix_does_not_match_longer_identifier(self):
        from memory_service import MemoryStore

        with tempfile.TemporaryDirectory() as temp_dir:
            store = MemoryStore(Path(temp_dir))
            store.write_file(
                "known-run",
                "# Stored run\n\nrun_id: known-run-123456",
            )
            self.assertEqual(store.search_semantic("known-run-123"), [])
            self.assertEqual(store.search_semantic("run-123456"), [])

    def test_run_id_matches_inside_composite_event_id(self):
        from memory_service import MemoryStore

        run_id = "integration-test-a51b9cf7-a58f-45bf-ad81-56999e1730e5"
        with tempfile.TemporaryDirectory() as temp_dir:
            store = MemoryStore(Path(temp_dir))
            store.write_file(
                "composite-event",
                "# Event\n\nevent_id: learning-artifact:integration.test.learning-memory:"
                + run_id
                + "\n",
            )
            results = store.search_semantic(run_id)
            self.assertTrue(results)
            self.assertEqual(results[0]["file"], "composite-event")

    def test_unicode_exact_query_is_searchable(self):
        from memory_service import MemoryStore

        with tempfile.TemporaryDirectory() as temp_dir:
            store = MemoryStore(Path(temp_dir))
            store.write_file(
                "unicode-notes",
                "# Архитектура\n\nОбсуждаем архитектура распределённой системы.",
            )
            results = store.search_semantic("архитектура")
            self.assertTrue(results)
            self.assertEqual(results[0]["file"], "unicode-notes")

    def test_accented_prose_is_searchable(self):
        from memory_service import MemoryStore

        with tempfile.TemporaryDirectory() as temp_dir:
            store = MemoryStore(Path(temp_dir))
            store.write_file(
                "accented-notes",
                "# Café\n\nThe café serves excellent coffee and pastries.",
            )
            results = store.search_semantic("café")
            self.assertTrue(results)
            self.assertEqual(results[0]["file"], "accented-notes")

    def test_trailing_period_is_stripped_before_dotted_identifier_classification(self):
        from memory_service import MemoryStore

        with tempfile.TemporaryDirectory() as temp_dir:
            store = MemoryStore(Path(temp_dir))
            store.write_file(
                "artifact-type-notes",
                "# Artifact types\n\nlearning.artifact is a valid type.",
            )
            results = store.search_semantic("learning.artifact.")
            self.assertTrue(results)
            self.assertEqual(results[0]["file"], "artifact-type-notes")

    def test_vectorizer_preserves_structured_identifiers(self):
        from memory_service import MemoryStore

        with tempfile.TemporaryDirectory() as temp_dir:
            store = MemoryStore(Path(temp_dir))
            run_id = "integration-test-a51b9cf7-a58f-45bf-ad81-56999e1730e5"
            self.assertIn(run_id, store.vectorize_text(run_id))

    def test_repeated_searches_do_not_revectorize_unchanged_files(self):
        """Regression: prompt-time fan-out re-scanned the whole corpus per query.

        Every search re-read, re-chunked and re-vectorized every file, so N
        concurrent hook searches took ~N times as long and exceeded the hook's
        timeout ("This operation was aborted") on large stores.
        """
        from memory_service import MemoryStore

        with tempfile.TemporaryDirectory() as temp_dir:
            store = MemoryStore(Path(temp_dir))
            for index in range(20):
                store.write_file(f"event-{index}", f"# Event {index}\n\nretrieval contract note {index}\n")
            store.search_semantic("retrieval contract note")  # builds the index
            with patch.object(store, "vectorize_text", wraps=store.vectorize_text) as vectorize, \
                    patch.object(store, "chunk_document", wraps=store.chunk_document) as chunk:
                results = store.search_semantic("retrieval contract note")
            self.assertEqual(len(results), 10)
            # Only the query is vectorized; stored chunks come from the index.
            self.assertEqual(vectorize.call_count, 1)
            chunk.assert_not_called()

    def test_search_index_sees_edits_additions_and_deletions(self):
        """Canonical writes update the index; exact identifiers still prune deletions."""
        from memory_service import MemoryStore

        with tempfile.TemporaryDirectory() as temp_dir:
            store = MemoryStore(Path(temp_dir))
            store.write_file("alpha", "# Alpha\n\nevent-1234\n")
            self.assertEqual([r["file"] for r in store.search_semantic("event-1234")], ["alpha"])
            store.write_file("alpha", "# Alpha\n\nevent-5678 changed content\n")
            self.assertEqual(store.search_semantic("event-1234"), [])
            store.write_file("beta", "# Beta\n\nevent-1234\n")
            self.assertEqual([r["file"] for r in store.search_semantic("event-1234")], ["beta"])
            (Path(temp_dir) / "beta.md").unlink()
            self.assertEqual(store.search_semantic("event-1234"), [])
            self.assertNotIn("beta", store._search_index)

    def test_warmup_reconciles_hybrid_after_identifier_refresh_of_external_edit(self):
        from memory_service import MemoryStore

        with tempfile.TemporaryDirectory() as temp_dir:
            store = MemoryStore(Path(temp_dir))
            store.write_file(
                "revision-note",
                "# Revision\n\nartifact_id: revision-1234\nold deployment content",
            )
            path = Path(temp_dir) / "revision-note.md"
            path.write_text(
                "# Revision\n\nartifact_id: revision-5678\nnew deployment content",
                encoding="utf-8",
            )

            # Exact identifier lookup updates only the lexical cache by design.
            self.assertEqual(store.search_semantic("revision-1234"), [])
            report = store.warm_search_index_report()
            self.assertTrue(report["complete"], report)

            with store._hybrid_index._connect() as db:
                rows = db.execute(
                    "SELECT signature, content FROM chunks WHERE file=?",
                    ("revision-note",),
                ).fetchall()
            self.assertTrue(rows)
            self.assertTrue(all("revision-5678" in row["content"] for row in rows))
            self.assertFalse(any("revision-1234" in row["content"] for row in rows))
            results = store.search_semantic("new deployment content")
            self.assertTrue(any(result["file"] == "revision-note" for result in results))

    def test_failed_indexing_is_durable_and_identical_ingest_retries(self):
        from memory_service import MemoryService, MemoryStore
        import hashlib

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            store = MemoryStore(root / "memory")
            content = "# Durable write\n\nretryable index marker"
            with patch.object(
                store._hybrid_index, "replace_document",
                side_effect=RuntimeError("temporary SQLite failure"),
            ):
                self.assertTrue(store.write_file("durable-note", content))
            pending = store.pending_index_status()
            self.assertEqual(pending["files"], ["durable-note"])
            self.assertIn("temporary SQLite failure", pending["errors"]["durable-note"])

            # A repeat write is the bounded recovery path for a durable memory.
            self.assertTrue(store.write_file("durable-note", content))
            self.assertEqual(store.pending_index_status()["count"], 0)

            service = MemoryService(root / "memory.sock", root / "memory")
            source_path = str(root / "project" / "CLAUDE.md")
            scope = str(root / "project")
            digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
            with patch.object(
                service.store._hybrid_index, "replace_document",
                side_effect=RuntimeError("temporary ingest index failure"),
            ):
                first = service.store.ingest_claude_markdown(
                    source_path, content, digest, {"scope": scope}
                )
            self.assertEqual(first["status"], "ingested")
            self.assertEqual(service.store.pending_index_status()["count"], 1)
            with service._index_status_lock:
                service._index_state = "ready"
            self.assertEqual(service.search_index_status()["state"], "degraded")

            # The unchanged-ingest fast path must retry indexing, not return early.
            second = service.store.ingest_claude_markdown(
                source_path, content, digest, {"scope": scope}
            )
            self.assertEqual(second["status"], "unchanged")
            self.assertEqual(service.store.pending_index_status()["count"], 0)
            self.assertEqual(service.search_index_status()["state"], "ready")

    def test_malformed_pending_ledger_recovers_after_full_reconciliation(self):
        from memory_service import MemoryService

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            memory_dir = root / "memory"
            memory_dir.mkdir()
            (memory_dir / "note.md").write_text(
                "# Note\n\nA durable note for index recovery.", encoding="utf-8"
            )
            (memory_dir / ".hybrid-index-pending.json").write_text(
                "{ malformed json", encoding="utf-8"
            )
            service = MemoryService(root / "memory.sock", memory_dir)
            service._warm_search_index()
            self.assertEqual(service.search_index_status()["state"], "ready")
            self.assertEqual(service.store.pending_index_status()["count"], 0)
            self.assertEqual(
                json.loads((memory_dir / ".hybrid-index-pending.json").read_text(encoding="utf-8")),
                {},
            )

    def test_persisted_pending_ledger_error_recovers_after_full_reconciliation(self):
        from memory_service import MemoryService

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            memory_dir = root / "memory"
            memory_dir.mkdir()
            (memory_dir / "note.md").write_text(
                "# Note\n\nA durable note for index recovery.", encoding="utf-8"
            )
            (memory_dir / ".hybrid-index-pending.json").write_text(
                json.dumps({"__ledger_error__": {"error": "previous read failure"}}),
                encoding="utf-8",
            )
            service = MemoryService(root / "memory.sock", memory_dir)
            service._warm_search_index()
            self.assertEqual(service.search_index_status()["state"], "ready")
            self.assertEqual(service.store.pending_index_status()["count"], 0)
            self.assertEqual(
                json.loads((memory_dir / ".hybrid-index-pending.json").read_text(encoding="utf-8")),
                {},
            )

    def test_pending_ledger_rejects_traversal_keys_and_reconciles_safely(self):
        from memory_service import MemoryService, MemoryStore

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            memory_dir = root / "memory"
            memory_dir.mkdir()
            outside = root / "outside.md"
            outside.write_text("must not be touched", encoding="utf-8")
            (memory_dir / "note.md").write_text(
                "# Note\\n\\nSafe reconciliation target.", encoding="utf-8"
            )
            ledger = memory_dir / ".hybrid-index-pending.json"
            ledger.write_text(
                json.dumps({"../outside": {"signature": "x", "error": "untrusted"}}),
                encoding="utf-8",
            )

            store = MemoryStore(memory_dir)
            status = store.pending_index_status()
            self.assertEqual(status["files"], [])
            self.assertIn("__ledger_error__", status["errors"])
            with self.assertRaises(ValueError):
                store._clear_index_pending("../outside", "x")
            with self.assertRaises(ValueError):
                store._mark_index_pending(outside, RuntimeError("must reject outside path"))
            self.assertEqual(outside.read_text(encoding="utf-8"), "must not be touched")

            service = MemoryService(root / "memory.sock", memory_dir)
            service._warm_search_index()
            self.assertEqual(service.search_index_status()["state"], "ready")
            self.assertEqual(service.store.pending_index_status()["count"], 0)
            self.assertEqual(json.loads(ledger.read_text(encoding="utf-8")), {})
            self.assertEqual(outside.read_text(encoding="utf-8"), "must not be touched")

    def test_pending_ledger_rejects_non_object_records_and_recovers(self):
        from memory_service import MemoryService

        for malformed_record in (None, [], "not-an-object"):
            with self.subTest(record=malformed_record), tempfile.TemporaryDirectory() as temp_dir:
                root = Path(temp_dir)
                memory_dir = root / "memory"
                memory_dir.mkdir()
                (memory_dir / "note.md").write_text(
                    "# Note\\n\\nSafe reconciliation target.", encoding="utf-8"
                )
                ledger = memory_dir / ".hybrid-index-pending.json"
                ledger.write_text(
                    json.dumps({"note": malformed_record}), encoding="utf-8"
                )

                service = MemoryService(root / "memory.sock", memory_dir)
                status = service.store.pending_index_status()
                self.assertEqual(status["files"], [])
                self.assertIn("__ledger_error__", status["errors"])

                service._warm_search_index()
                self.assertEqual(service.search_index_status()["state"], "ready")
                self.assertEqual(service.store.pending_index_status()["count"], 0)
                self.assertEqual(json.loads(ledger.read_text(encoding="utf-8")), {})

    def test_pending_index_clear_cannot_remove_a_newer_revision(self):
        from memory_service import MemoryStore

        with tempfile.TemporaryDirectory() as temp_dir:
            store = MemoryStore(Path(temp_dir))
            store.write_file("revision-note", "# Note\n\nrevision alpha")
            path = Path(temp_dir) / "revision-note.md"
            stale_signature = repr(store._file_signature(path.stat()))
            path.write_text("# Note\n\nrevision beta", encoding="utf-8")
            store._mark_index_pending(path, RuntimeError("new revision failed indexing"))
            current_signature = repr(store._file_signature(path.stat()))

            self.assertNotEqual(stale_signature, current_signature)
            self.assertFalse(store._clear_index_pending("revision-note", stale_signature))
            pending = store.pending_index_status()
            self.assertIn("revision-note", pending["files"])
            self.assertEqual(
                pending["errors"]["revision-note"],
                "new revision failed indexing",
            )

    def test_concurrent_newer_write_stays_pending_when_older_index_finishes(self):
        from memory_service import MemoryStore

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            store = MemoryStore(root)
            store.write_file("race-note", "# Note\n\ninitial revision")
            path = root / "race-note.md"
            path.write_text("# Note\n\nolder revision", encoding="utf-8")

            indexed_older_revision = threading.Event()
            release_older_index = threading.Event()
            original_indexed_chunks = store._indexed_chunks
            first_call = True

            def pause_after_index(md_file, *, update_hybrid=True):
                nonlocal first_call
                result = original_indexed_chunks(md_file, update_hybrid=update_hybrid)
                if first_call and update_hybrid:
                    first_call = False
                    indexed_older_revision.set()
                    if not release_older_index.wait(timeout=5):
                        raise TimeoutError("test did not release older index operation")
                return result

            with patch.object(store, "_indexed_chunks", side_effect=pause_after_index):
                worker = threading.Thread(target=store._index_written_file, args=(path,))
                worker.start()
                self.assertTrue(indexed_older_revision.wait(timeout=5))
                path.write_text("# Note\n\nnewer revision", encoding="utf-8")
                store._mark_index_pending(path, RuntimeError("newer revision indexing failed"))
                newer_signature = repr(store._file_signature(path.stat()))
                release_older_index.set()
                worker.join(timeout=5)
                self.assertFalse(worker.is_alive(), "older index operation did not finish")

            pending = store.pending_index_status()
            self.assertIn("race-note", pending["files"])
            self.assertEqual(
                json.loads((root / ".hybrid-index-pending.json").read_text(encoding="utf-8"))["race-note"]["signature"],
                newer_signature,
            )
            self.assertEqual(
                pending["errors"]["race-note"],
                "file changed during hybrid indexing; retry required",
            )

    def test_pending_ledger_write_failure_keeps_readiness_degraded(self):
        from memory_service import MemoryService

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            service = MemoryService(root / "memory.sock", root / "memory")
            path = root / "memory" / "note.md"
            path.write_text("# Note\n\nledger failure", encoding="utf-8")
            with patch.object(
                service.store, "_write_pending_index_records",
                side_effect=OSError("read-only ledger"),
            ):
                service.store._mark_index_pending(path, RuntimeError("SQLite unavailable"))
            pending = service.store.pending_index_status()
            self.assertIn("note", pending["files"])
            self.assertIn("__ledger_error__", pending["errors"])
            with service._index_status_lock:
                service._index_state = "ready"
            self.assertEqual(service.search_index_status()["state"], "degraded")

    def test_search_index_detects_same_size_rewrite_with_restored_mtime(self):
        """Exact identifier lookup detects out-of-band same-size rewrites."""
        from memory_service import MemoryStore

        with tempfile.TemporaryDirectory() as temp_dir:
            store = MemoryStore(Path(temp_dir))
            store.write_file("note", "# Note\n\nevent-1234\n")
            path = Path(temp_dir) / "note.md"
            original = path.stat()
            self.assertEqual([r["file"] for r in store.search_semantic("event-1234")], ["note"])
            path.write_text("# Note\n\nevent-5678\n", encoding="utf-8")
            os.utime(path, ns=(original.st_atime_ns, original.st_mtime_ns))
            self.assertEqual(path.stat().st_size, original.st_size)
            self.assertEqual(path.stat().st_mtime_ns, original.st_mtime_ns)
            self.assertEqual(store.search_semantic("event-1234"), [])
            self.assertEqual([r["file"] for r in store.search_semantic("event-5678")], ["note"])

    def test_write_during_index_build_never_leaves_stale_results(self):
        """A write between stat and read must not leave a stale identifier result."""
        from memory_service import MemoryStore

        with tempfile.TemporaryDirectory() as temp_dir:
            store = MemoryStore(Path(temp_dir))
            store.write_file("note", "# Note\n\nevent-1234\n")
            path = Path(temp_dir) / "note.md"
            # Force the exact-identifier path to rebuild this revision.
            store._search_index.pop("note", None)
            real_chunk = store.chunk_document
            state = {"rewritten": False}

            def chunk_after_concurrent_write(name, *args, **kwargs):
                if not state["rewritten"]:
                    state["rewritten"] = True
                    path.write_text("# Note\n\nevent-5678 with extra words\n", encoding="utf-8")
                return real_chunk(name, *args, **kwargs)

            with patch.object(store, "chunk_document", side_effect=chunk_after_concurrent_write):
                store.search_semantic("event-1234")
            self.assertEqual(store.search_semantic("event-1234"), [])
            self.assertEqual([r["file"] for r in store.search_semantic("event-5678")], ["note"])

    def test_concurrent_searches_stay_within_prompt_hook_budget(self):
        from memory_service import MemoryStore

        with tempfile.TemporaryDirectory() as temp_dir:
            store = MemoryStore(Path(temp_dir))
            for index in range(200):
                body = "\n".join(f"## Section {part}\n" + "memory retrieval hook contract timeout " * 60 for part in range(4))
                store.write_file(f"event-{index:04d}", f"# Event {index}\n\n{body}")
            store.warm_search_index()
            durations = []

            def run():
                started = time.monotonic()
                store.search_semantic("what is the memory retrieval hook contract", 10, "memory")
                durations.append(time.monotonic() - started)

            threads = [threading.Thread(target=run) for _ in range(6)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
            self.assertEqual(len(durations), 6)
            # The hook allows 1500ms per request; require a wide safety margin.
            self.assertLess(max(durations), 0.75)

    def test_concurrent_searches_use_warm_hybrid_index_without_revectorizing_files(self):
        from memory_service import MemoryStore

        with tempfile.TemporaryDirectory() as temp_dir:
            store = MemoryStore(Path(temp_dir))
            file_count = 24
            worker_count = 6
            for index in range(file_count):
                store.write_file(
                    f"cold-event-{index:03d}",
                    f"# Event {index}\n\ncold cache single flight retrieval marker {index}\n",
                )

            barrier = threading.Barrier(worker_count)
            failures = []

            def run():
                try:
                    barrier.wait(timeout=3)
                    store.search_semantic("cold cache single flight retrieval marker", 10, "memory")
                except Exception as exc:
                    failures.append(exc)

            with patch.object(store, "vectorize_text", wraps=store.vectorize_text) as vectorize:
                threads = [threading.Thread(target=run) for _ in range(worker_count)]
                for thread in threads:
                    thread.start()
                for thread in threads:
                    thread.join(timeout=5)

            self.assertTrue(all(not thread.is_alive() for thread in threads), "search thread did not finish")
            self.assertEqual(failures, [])
            # Hybrid search encodes each query once and reads stored vectors from SQLite.
            self.assertEqual(vectorize.call_count, worker_count)
            self.assertEqual(len(store._search_index), file_count)

    def test_rest_search_accepts_limit_and_top_k_alias(self):
        from memory_service import MemoryService, create_http_server

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            service = MemoryService(root / "memory.sock", root / "memory")
            for index in range(5):
                service.store.write_file(f"note-{index}", f"# Note {index}\n\nretrieval contract detail {index}\n")
            service._warm_search_index()
            server = create_http_server(service, "127.0.0.1", 0)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                def search(path, payload):
                    request = urllib.request.Request(
                        f"http://127.0.0.1:{server.server_address[1]}{path}",
                        data=json.dumps(payload).encode("utf-8"),
                        headers={"Content-Type": "application/json"},
                        method="POST",
                    )
                    with urllib.request.urlopen(request, timeout=3) as response:
                        return json.loads(response.read().decode("utf-8"))

                for path in ("/memory/search", "/memory/search-semantic"):
                    self.assertEqual(len(search(path, {"query": "retrieval contract", "limit": 2})["results"]), 2)
                    # top_k was previously overwritten by the default limit of 10.
                    self.assertEqual(len(search(path, {"query": "retrieval contract", "top_k": 3})["results"]), 3)
                    self.assertEqual(len(search(path, {"query": "retrieval contract", "limit": 1, "top_k": 4})["results"]), 1)
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=3)


class MemoryServiceIdempotencyTest(unittest.TestCase):

    def test_append_once_deduplicates_retries_and_rejects_key_reuse(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            from memory_service import MemoryService

            memory = MemoryService(root / "memory.sock", root / "memory")
            first = json.loads(memory._process_request(json.dumps({
                "op": "append_once",
                "name": "claude_code_events",
                "event_id": "session-123:SessionEnd",
                "entry": {"event": "SessionEnd", "session_id": "session-123"},
            })))
            duplicate = json.loads(memory._process_request(json.dumps({
                "op": "append_once",
                "name": "claude_code_events",
                "event_id": "session-123:SessionEnd",
                "entry": {"event": "SessionEnd", "session_id": "session-123"},
            })))
            conflict = json.loads(memory._process_request(json.dumps({
                "op": "append_once",
                "name": "claude_code_events",
                "event_id": "session-123:SessionEnd",
                "entry": {"event": "SessionEnd", "session_id": "different-session"},
            })))

            self.assertEqual(first["status"], "stored")
            self.assertEqual(duplicate["status"], "duplicate")
            self.assertFalse(conflict["ok"])
            self.assertIn("different payload", conflict["error"])
            entries = memory.store.read_entries("claude_code_events")
            self.assertEqual(len(entries), 1)
            self.assertEqual(entries[0]["event_id"], "session-123:SessionEnd")

    def test_append_once_requires_stable_event_id(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            from memory_service import MemoryService

            memory = MemoryService(Path(temp_dir) / "memory.sock", Path(temp_dir) / "memory")
            response = json.loads(memory._process_request(json.dumps({
                "op": "append_once", "name": "claude_code_events", "entry": {"event": "SessionEnd"}
            })))
            self.assertFalse(response["ok"])
            self.assertIn("event_id", response["error"])

    def test_append_once_rejects_malformed_and_non_object_jsonl_records(self):
        from memory_service import MemoryStore

        cases = (
            ("malformed", "{not-json\n", "malformed record at line 1"),
            ("array", "[1, 2]\n", "is not a JSON object"),
            ("null", "null\n", "is not a JSON object"),
        )
        for label, content, message in cases:
            with self.subTest(record=label), tempfile.TemporaryDirectory() as temp_dir:
                store = MemoryStore(Path(temp_dir))
                (Path(temp_dir) / "events.jsonl").write_text(content, encoding="utf-8")
                with self.assertRaisesRegex(RuntimeError, message):
                    store.append_entry_once("events", "event-1", {"event": "SessionEnd"})
                self.assertEqual((Path(temp_dir) / "events.jsonl").read_text(encoding="utf-8"), content)

    def test_append_once_rejects_existing_event_without_payload_digest(self):
        from memory_service import MemoryStore

        with tempfile.TemporaryDirectory() as temp_dir:
            store = MemoryStore(Path(temp_dir))
            path = Path(temp_dir) / "events.jsonl"
            path.write_text(
                json.dumps({"event_id": "event-1", "event": "SessionEnd"}) + "\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "without a verifiable payload digest"):
                store.append_entry_once("events", "event-1", {"event": "SessionEnd"})
            self.assertEqual(len(path.read_text(encoding="utf-8").splitlines()), 1)

    def test_append_once_rejects_empty_entry(self):
        from memory_service import MemoryStore

        with tempfile.TemporaryDirectory() as temp_dir:
            store = MemoryStore(Path(temp_dir))
            with self.assertRaisesRegex(ValueError, "non-empty JSON object"):
                store.append_entry_once("events", "event-1", {})

    def test_append_once_retries_fsync_failure_before_duplicate_ack(self):
        from unittest.mock import patch
        from memory_service import MemoryStore

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            store = MemoryStore(root)
            entry = {"event": "SessionEnd", "session_id": "session-1"}
            with patch("memory_service.os.fsync", side_effect=[OSError("sync failed"), None]) as sync:
                with self.assertRaisesRegex(OSError, "sync failed"):
                    store.append_entry_once("events", "event-1", entry)
                result = store.append_entry_once("events", "event-1", entry)
            self.assertEqual(result["status"], "duplicate")
            self.assertEqual(sync.call_count, 2)
            records = [json.loads(line) for line in (root / "events.jsonl").read_text(encoding="utf-8").splitlines()]
            self.assertEqual(len(records), 1)
            # A newly constructed store can safely retry after a service restart.
            restarted = MemoryStore(root)
            self.assertEqual(restarted.append_entry_once("events", "event-1", entry)["status"], "duplicate")
            self.assertEqual(len((root / "events.jsonl").read_text(encoding="utf-8").splitlines()), 1)

    def test_append_once_preserves_payload_timestamp_and_reserves_digest(self):
        from memory_service import MemoryStore

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            store = MemoryStore(root)
            entry = {"event": "SessionEnd", "timestamp": "2026-10-08T23:00:00Z"}
            result = store.append_entry_once("events", "event-1", entry)
            self.assertEqual(result["status"], "stored")
            record = json.loads((root / "events.jsonl").read_text(encoding="utf-8").strip())
            self.assertEqual(record["timestamp"], entry["timestamp"])
            self.assertIn("captured_at", record)
            self.assertEqual(store.append_entry_once("events", "event-1", entry)["status"], "duplicate")
            for field in ("payload_sha256", "captured_at"):
                with self.subTest(reserved_field=field):
                    with self.assertRaisesRegex(ValueError, f"{field} is reserved"):
                        store.append_entry_once("events", "event-2", {
                            "event": "SessionEnd", field: "caller-value"
                        })



class MemoryServiceRestIdempotencyTest(unittest.TestCase):

    def test_append_once_rest_endpoint_maps_validation_conflict_and_corruption(self):
        from memory_service import MemoryService, create_http_server

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            service = MemoryService(root / "memory.sock", root / "memory")
            server = create_http_server(service, "127.0.0.1", 0)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                url = f"http://127.0.0.1:{server.server_port}/memory/append-once"

                def post(payload):
                    request = urllib.request.Request(
                        url,
                        data=json.dumps(payload).encode("utf-8"),
                        headers={"Content-Type": "application/json"},
                        method="POST",
                    )
                    try:
                        with urllib.request.urlopen(request, timeout=2) as response:
                            return response.status, json.loads(response.read().decode("utf-8"))
                    except urllib.error.HTTPError as response:
                        return response.code, json.loads(response.read().decode("utf-8"))

                base = {
                    "name": "events",
                    "event_id": "event-1",
                    "entry": {"event": "SessionEnd", "session_id": "session-1"},
                }
                self.assertEqual(post(base)[0], 200)
                conflict = dict(base)
                conflict["entry"] = {"event": "SessionEnd", "session_id": "different"}
                status, payload = post(conflict)
                self.assertEqual(status, 409)
                self.assertFalse(payload["ok"])
                self.assertIn("different payload", payload["error"])

                status, payload = post({"name": "events", "entry": {"event": "SessionEnd"}})
                self.assertEqual(status, 400)
                self.assertFalse(payload["ok"])

                (root / "memory" / "corrupt.jsonl").write_text("{broken\n", encoding="utf-8")
                status, payload = post({
                    "name": "corrupt",
                    "event_id": "event-corrupt",
                    "entry": {"event": "SessionEnd"},
                })
                self.assertEqual(status, 500)
                self.assertFalse(payload["ok"])
                self.assertIn("malformed record", payload["error"])
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)

    def test_append_once_rest_endpoint_acknowledges_stored_and_duplicate(self):
        from memory_service import MemoryService, create_http_server

        with tempfile.TemporaryDirectory() as temp_dir:
            service = MemoryService(Path(temp_dir) / "memory.sock", Path(temp_dir) / "memory")
            server = create_http_server(service, "127.0.0.1", 0)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                url = f"http://127.0.0.1:{server.server_port}/memory/append-once"
                payload = {
                    "name": "claude_code_events",
                    "event_id": "session-456:SessionEnd",
                    "entry": {"event": "SessionEnd", "session_id": "session-456"},
                }
                results = []
                for _ in range(2):
                    request = urllib.request.Request(
                        url,
                        data=json.dumps(payload).encode("utf-8"),
                        headers={"Content-Type": "application/json"},
                        method="POST",
                    )
                    with urllib.request.urlopen(request, timeout=2) as response:
                        results.append(json.loads(response.read().decode("utf-8")))
                self.assertEqual(results[0]["status"], "stored")
                self.assertEqual(results[1]["status"], "duplicate")
                self.assertEqual(len(service.store.read_entries("claude_code_events")), 1)
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)

    def test_installed_session_end_hook_round_trips_to_durable_memory_once(self):
        """Exercise the installed Node hook through HTTP and durable Memory storage."""
        from memory_service import MemoryService, create_http_server

        repo_root = Path(__file__).resolve().parents[1]
        hook_source = repo_root / "hooks" / "session-end-memory-capture.js"
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            # A copied hook under a path containing spaces mirrors ~/.claude/hooks
            # and verifies the deployed .js file works outside the repository's
            # package scope.
            hook_deploy = root / "installed hooks with spaces" / hook_source.name
            hook_deploy.parent.mkdir(parents=True)
            hook_deploy.write_bytes(hook_source.read_bytes())

            service = MemoryService(root / "memory.sock", root / "memory")
            server = create_http_server(service, "127.0.0.1", 0)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                env = {
                    **os.environ,
                    "FLOSSWARE_MEMORY_URL": f"http://127.0.0.1:{server.server_port}",
                }
                event = json.dumps({"session_id": "round-trip-session-001"})
                results = [
                    subprocess.run(
                        ["node", str(hook_deploy)],
                        input=event,
                        text=True,
                        capture_output=True,
                        timeout=10,
                        env=env,
                        check=False,
                    )
                    for _ in range(2)
                ]

                for result in results:
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertNotIn("Memory capture unavailable:", result.stderr)

                # The second execution must be acknowledged as a duplicate,
                # not persisted as a second session event.
                entries = service.store.read_entries("session_events")
                self.assertEqual(len(entries), 1)
                self.assertEqual(entries[0]["event_type"], "claude_code.session_end")
                self.assertEqual(entries[0]["hook_event_name"], "SessionEnd")
                self.assertEqual(entries[0]["session_id"], "round-trip-session-001")
                self.assertEqual(entries[0]["source"], "claude-code")
                self.assertTrue(entries[0]["event_id"].startswith("claude-code:session-end:"))
                self.assertTrue(entries[0].get("payload_sha256"))
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)


class MemoryServiceRestStatusTest(unittest.TestCase):

    def test_generic_memory_routes_map_failure_to_non_2xx_status(self):
        from memory_service import MemoryService, create_http_server

        with tempfile.TemporaryDirectory() as temp_dir:
            service = MemoryService(Path(temp_dir) / "memory.sock", Path(temp_dir) / "memory")
            server = create_http_server(service, "127.0.0.1", 0)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                base = f"http://127.0.0.1:{server.server_port}"

                def post(path, payload):
                    request = urllib.request.Request(
                        base + path,
                        data=json.dumps(payload).encode("utf-8"),
                        headers={"Content-Type": "application/json"},
                        method="POST",
                    )
                    try:
                        with urllib.request.urlopen(request, timeout=2) as response:
                            return response.status, json.loads(response.read().decode("utf-8"))
                    except urllib.error.HTTPError as response:
                        return response.code, json.loads(response.read().decode("utf-8"))

                status, body = post("/memory/write", {"name": "../escape", "content": "no"})
                self.assertEqual(status, 400)
                self.assertFalse(body["ok"])
                self.assertEqual(body["error_type"], "validation")

                status, body = post("/memory/read", {"name": "missing"})
                self.assertEqual(status, 404)
                self.assertFalse(body["ok"])
                self.assertEqual(body["error_type"], "not_found")

                with patch.object(service.store, "write_file", return_value=False):
                    status, body = post("/memory/write", {"name": "write-failure", "content": "x"})
                self.assertEqual(status, 500)
                self.assertFalse(body["ok"])
                self.assertEqual(body["error_type"], "internal")

                with patch.object(service.store, "read_file", side_effect=OSError("disk failure")):
                    status, body = post("/memory/read", {"name": "read-failure"})
                self.assertEqual(status, 500)
                self.assertFalse(body["ok"])
                self.assertEqual(body["error_type"], "internal")

                status, body = post("/memory/write", {"name": "valid", "content": "stored"})
                self.assertEqual(status, 200)
                self.assertTrue(body["ok"])
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)

    def test_semantic_search_enrichment_read_failure_returns_500(self):
        from memory_service import MemoryService, create_http_server

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            service = MemoryService(root / "memory.sock", root / "memory")
            service._warm_search_index()
            server = create_http_server(service, "127.0.0.1", 0)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                with patch.object(
                    service.store,
                    "search_semantic",
                    return_value=[{"file": "enrichment-failure", "section": "body", "score": 1.0}],
                ), patch.object(
                    service.store,
                    "read_file",
                    side_effect=OSError("disk failure during enrichment"),
                ):
                    request = urllib.request.Request(
                        "http://127.0.0.1:%d/memory/search-semantic" % server.server_port,
                        data=json.dumps({"query": "needle", "top_k": 5}).encode("utf-8"),
                        headers={"Content-Type": "application/json"},
                        method="POST",
                    )
                    with self.assertRaises(urllib.error.HTTPError) as raised:
                        urllib.request.urlopen(request, timeout=2)
                    self.assertEqual(raised.exception.code, 500)
                    body = json.loads(raised.exception.read().decode("utf-8"))
                    self.assertFalse(body["ok"])
                    self.assertIn("disk failure during enrichment", body["error"])
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)



class MemoryServiceSecurityTest(unittest.TestCase):

    def test_private_runtime_directory_socket_permissions_and_name_validation(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            home = root / "home"
            runtime = root / "runtime"
            (home / ".claude").mkdir(parents=True)
            runtime.mkdir()

            env = os.environ.copy()
            env["HOME"] = str(home)
            env["XDG_RUNTIME_DIR"] = str(runtime)
            env["PYTHONUNBUFFERED"] = "1"
            env["ENSEMBLE_MEMORY_DIR"] = str(root / "memory")
            env["ENSEMBLE_LOG_DIR"] = str(root / "logs")
            env["ENSEMBLE_MEMORY_HTTP_PORT"] = "0"

            # The subprocess has its own interpreter, so module-scoped mocks in
            # this test process do not apply. Supply a deterministic local model
            # double instead of allowing a test to download real weights.
            fake_package = root / "fake_modules" / "sentence_transformers"
            fake_package.mkdir(parents=True)
            (fake_package / "__init__.py").write_text(
                "import math\n"
                "class _Vector(list):\n"
                "    def astype(self, dtype): return self\n"
                "    def tolist(self): return list(self)\n"
                "class SentenceTransformer:\n"
                "    def __init__(self, model_name, device='cpu'): pass\n"
                "    def encode(self, texts, **kwargs):\n"
                "        vectors = []\n"
                "        for text in texts:\n"
                "            count = float(len(str(text).split()) or 1)\n"
                "            norm = math.sqrt(count * count + 1.0)\n"
                "            vectors.append(_Vector([count / norm, 1.0 / norm]))\n"
                "        return vectors\n",
                encoding="utf-8",
            )
            env["PYTHONPATH"] = os.pathsep.join(
                filter(None, [str(root / "fake_modules"), env.get("PYTHONPATH", "")])
            )

            process = subprocess.Popen(
                [sys.executable, str(SERVICE)],
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )

            socket_path = runtime / "claude-ensemble" / "memory.sock"
            try:
                deadline = time.monotonic() + 5
                while time.monotonic() < deadline and not socket_path.exists():
                    time.sleep(0.05)

                self.assertTrue(socket_path.exists(), "memory socket was not created")

                runtime_mode = stat.S_IMODE(socket_path.parent.stat().st_mode)
                socket_mode = stat.S_IMODE(socket_path.stat().st_mode)
                self.assertEqual(runtime_mode, 0o700)
                self.assertEqual(socket_mode, 0o600)

                valid = send_request(
                    socket_path,
                    {"op": "write", "name": "project_test-1.v2", "content": "ok"},
                )
                self.assertEqual(valid, {"ok": True})

                read = send_request(
                    socket_path, {"op": "read", "name": "project_test-1.v2"}
                )
                self.assertEqual(read["content"], "ok")

                for invalid_name in ("../escape", "foo/bar", "/absolute", ".."):
                    response = send_request(
                        socket_path,
                        {"op": "write", "name": invalid_name, "content": "nope"},
                    )
                    self.assertFalse(response["ok"])
                    self.assertIn("Invalid memory name", response["error"])

                    entries_response = send_request(
                        socket_path,
                        {"op": "entries", "name": invalid_name},
                    )
                    self.assertFalse(entries_response["ok"])
                    self.assertIn("Invalid memory name", entries_response["error"])

                outside = home / "escape.md"
                self.assertFalse(outside.exists())
            finally:
                process.terminate()
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=3)


class MemoryServiceContextTest(unittest.TestCase):

    def test_execution_aware_retrieval_returns_related_prior_context(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            home = root / "home"
            runtime = root / "runtime"
            (home / ".claude").mkdir(parents=True)
            runtime.mkdir()

            env = os.environ.copy()
            env["HOME"] = str(home)
            env["XDG_RUNTIME_DIR"] = str(runtime)
            env["PYTHONUNBUFFERED"] = "1"
            env["ENSEMBLE_MEMORY_DIR"] = str(root / "memory")
            env["ENSEMBLE_LOG_DIR"] = str(root / "logs")
            env["ENSEMBLE_MEMORY_HTTP_PORT"] = "0"

            process = subprocess.Popen(
                [sys.executable, str(SERVICE)],
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            socket_path = runtime / "claude-ensemble" / "memory.sock"
            try:
                deadline = time.monotonic() + 5
                while time.monotonic() < deadline and not socket_path.exists():
                    time.sleep(0.05)
                self.assertTrue(socket_path.exists(), "memory socket was not created")

                grandparent = ExecutionContext(
                    request_id="request-1",
                    execution_id="grandparent",
                    objective="solve",
                    lineage=("grandparent",),
                    stage="root",
                )
                parent = grandparent.child(
                    execution_id="parent",
                    stage="solve",
                    worker_id="worker-1",
                )
                sibling = grandparent.child(
                    execution_id="sibling",
                    stage="solve",
                    worker_id="worker-2",
                )
                separate_root = ExecutionContext(
                    request_id="request-1",
                    execution_id="separate-root",
                    objective="solve",
                    lineage=("separate-root",),
                    stage="other",
                )
                child = parent.child(
                    execution_id="child",
                    stage="review",
                    worker_id="reviewer-1",
                )
                descendant = child.child(
                    execution_id="grandchild",
                    stage="future",
                    worker_id="future-worker",
                )
                reused_id = ExecutionContext(
                    request_id="request-2",
                    execution_id="parent",
                    objective="other",
                    lineage=("parent",),
                )
                cross_request_shared_lineage = ExecutionContext(
                    request_id="request-2",
                    execution_id="other-child",
                    objective="other",
                    lineage=("parent", "other-child"),
                )
                unrelated = ExecutionContext(
                    request_id="request-2",
                    execution_id="unrelated",
                    objective="other",
                    lineage=("unrelated",),
                )

                for context, result in (
                    (grandparent, "grandparent result"),
                    (parent, "parent result"),
                    (sibling, "sibling result"),
                    (separate_root, "separate root result"),
                    (descendant, "descendant result"),
                    (reused_id, "reused id result"),
                    (
                        cross_request_shared_lineage,
                        "cross-request shared lineage result",
                    ),
                    (unrelated, "unrelated result"),
                ):
                    response = send_request(
                        socket_path,
                        {
                            "op": "append",
                            "name": "execution-context",
                            "entry": {
                                "result": result,
                                "execution_context": context.to_dict(),
                            },
                        },
                    )
                    self.assertEqual(response, {"ok": True})

                response = send_request(
                    socket_path,
                    {
                        "op": "retrieve",
                        "name": "execution-context",
                        "context": child.to_dict(),
                        "limit": 10,
                    },
                )
                self.assertTrue(response["ok"])
                results = response["results"]
                self.assertEqual(
                    [item["relation"] for item in results],
                    ["parent", "ancestor", "same-request", "related-lineage"],
                )
                self.assertEqual(results[0]["record"]["result"], "parent result")
                self.assertEqual(results[1]["record"]["result"], "grandparent result")
                self.assertEqual(results[2]["record"]["result"], "separate root result")
                self.assertEqual(results[3]["record"]["result"], "sibling result")
                self.assertEqual(results[3]["relation"], "related-lineage")
                self.assertFalse(results[0]["authoritative"])
                retrieved_values = [item["record"]["result"] for item in results]
                self.assertNotIn("descendant result", retrieved_values)
                self.assertNotIn("reused id result", retrieved_values)
                self.assertNotIn(
                    "cross-request shared lineage result", retrieved_values
                )
                self.assertNotIn("unrelated result", retrieved_values)

                limited = send_request(
                    socket_path,
                    {
                        "op": "retrieve",
                        "name": "execution-context",
                        "context": child.to_dict(),
                        "limit": 1,
                    },
                )
                self.assertTrue(limited["ok"])
                self.assertEqual(len(limited["results"]), 1)
                self.assertEqual(limited["results"][0]["relation"], "parent")
            finally:
                process.terminate()
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=3)

    def test_execution_aware_retrieval_rejects_invalid_requests(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            home = root / "home"
            runtime = root / "runtime"
            (home / ".claude").mkdir(parents=True)
            runtime.mkdir()

            env = os.environ.copy()
            env["HOME"] = str(home)
            env["XDG_RUNTIME_DIR"] = str(runtime)
            env["PYTHONUNBUFFERED"] = "1"
            env["ENSEMBLE_MEMORY_DIR"] = str(root / "memory")
            env["ENSEMBLE_LOG_DIR"] = str(root / "logs")
            env["ENSEMBLE_MEMORY_HTTP_PORT"] = "0"

            process = subprocess.Popen(
                [sys.executable, str(SERVICE)],
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            socket_path = runtime / "claude-ensemble" / "memory.sock"
            try:
                deadline = time.monotonic() + 5
                while time.monotonic() < deadline and not socket_path.exists():
                    time.sleep(0.05)
                self.assertTrue(socket_path.exists(), "memory socket was not created")

                context = ExecutionContext(
                    request_id="request-1",
                    execution_id="child",
                    objective="test retrieval",
                    parent_execution_id="parent",
                    lineage=("parent", "child"),
                )
                invalid_requests = [
                    {"name": "../escape", "context": context.to_dict(), "limit": 10},
                    {"name": "execution-context", "context": {}, "limit": 10},
                    {"name": "execution-context", "context": context.to_dict(), "limit": 0},
                    {"name": "execution-context", "context": context.to_dict(), "limit": 101},
                    {"name": "execution-context", "context": context.to_dict(), "limit": "10"},
                    {"name": "execution-context", "context": context.to_dict(), "limit": True},
                    {"name": "execution-context", "context": context.to_dict(), "limit": -1},
                ]
                for request in invalid_requests:
                    response = send_request(
                        socket_path,
                        {"op": "retrieve", **request},
                    )
                    self.assertFalse(response["ok"])
                    self.assertIn("error", response)

                invalid_record = send_request(
                    socket_path,
                    {
                        "op": "append",
                        "name": "execution-context",
                        "entry": {
                            "result": "bad context",
                            "execution_context": {"not": "a valid context"},
                        },
                    },
                )
                self.assertEqual(invalid_record, {"ok": True})

                valid = send_request(
                    socket_path,
                    {
                        "op": "retrieve",
                        "name": "execution-context",
                        "context": context.to_dict(),
                        "limit": 10,
                    },
                )
                self.assertEqual(valid, {"ok": True, "results": []})
            finally:
                process.terminate()
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=3)

    def test_canonical_execution_context_round_trip(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            home = root / "home"
            runtime = root / "runtime"
            (home / ".claude").mkdir(parents=True)
            runtime.mkdir()

            env = os.environ.copy()
            env["HOME"] = str(home)
            env["XDG_RUNTIME_DIR"] = str(runtime)
            env["PYTHONUNBUFFERED"] = "1"
            env["ENSEMBLE_MEMORY_DIR"] = str(root / "memory")
            env["ENSEMBLE_LOG_DIR"] = str(root / "logs")
            env["ENSEMBLE_MEMORY_HTTP_PORT"] = "0"

            process = subprocess.Popen(
                [sys.executable, str(SERVICE)],
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            socket_path = runtime / "claude-ensemble" / "memory.sock"
            try:
                deadline = time.monotonic() + 5
                while time.monotonic() < deadline and not socket_path.exists():
                    time.sleep(0.05)
                self.assertTrue(socket_path.exists(), "memory socket was not created")

                result = ExecutionResult(
                    execution_id="parent.worker",
                    node_type="model",
                    status=ExecutionStatus.SUCCESS,
                    output="prior",
                )
                parent = ExecutionContext(
                    request_id="request-1",
                    execution_id="parent",
                    objective="solve",
                    artifact={"name": "artifact"},
                    requirements=("requirement",),
                    evidence=("evidence",),
                    constraints=("constraint",),
                    prior_results=(result,),
                    lineage=("parent",),
                    stage="solve",
                    worker_id="worker-1",
                )
                child = parent.child(
                    execution_id="child",
                    stage="review",
                    worker_id="reviewer-1",
                )

                legacy_response = send_request(
                    socket_path,
                    {
                        "op": "append",
                        "name": "execution-context",
                        "entry": {"result": "legacy"},
                    },
                )
                self.assertEqual(legacy_response, {"ok": True})

                response = send_request(
                    socket_path,
                    {
                        "op": "append",
                        "name": "execution-context",
                        "entry": {
                            "artifact": child.artifact,
                            "result": child.prior_results[0].output,
                            "execution_context": child.to_dict(),
                        },
                    },
                )
                self.assertEqual(response, {"ok": True})

                entries = send_request(
                    socket_path,
                    {"op": "entries", "name": "execution-context"},
                )
                self.assertEqual(entries["ok"], True)
                self.assertEqual(len(entries["entries"]), 2)
                self.assertEqual(entries["entries"][0]["result"], "legacy")
                self.assertNotIn("execution_context", entries["entries"][0])
                self.assertEqual(entries["entries"][1]["artifact"], {"name": "artifact"})
                self.assertEqual(entries["entries"][1]["result"], "prior")
                restored = ExecutionContext.from_dict(entries["entries"][1]["execution_context"])

                self.assertEqual(restored.request_id, "request-1")
                self.assertEqual(restored.execution_id, "child")
                self.assertEqual(restored.parent_execution_id, "parent")
                self.assertEqual(restored.lineage, ("parent", "child"))
                self.assertEqual(restored.stage, "review")
                self.assertEqual(restored.worker_id, "reviewer-1")
                self.assertEqual(restored.objective, "solve")
                self.assertEqual(restored.artifact, {"name": "artifact"})
                self.assertEqual(restored.requirements, ("requirement",))
                self.assertEqual(restored.evidence, ("evidence",))
                self.assertEqual(restored.constraints, ("constraint",))
                self.assertEqual(restored.prior_results[0].execution_id, "parent.worker")
            finally:
                process.terminate()
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=3)


class MemorySchemaVersionTest(unittest.TestCase):

    def test_current_context_includes_schema_version(self):
        context = ExecutionContext(
            request_id="request-1",
            execution_id="execution-1",
            objective="objective",
        )
        serialized = context.to_dict()
        self.assertEqual(serialized["schema_version"], 1)

    def test_legacy_context_without_schema_version_is_supported(self):
        legacy = {
            "request_id": "request-1",
            "execution_id": "execution-1",
            "objective": "objective",
            "legacy_field": "ignored",
        }
        restored = ExecutionContext.from_dict(legacy)
        self.assertEqual(restored.request_id, "request-1")
        self.assertEqual(restored.execution_id, "execution-1")
        self.assertEqual(restored.objective, "objective")
        self.assertNotIn("legacy_field", restored.to_dict())
        self.assertEqual(restored.to_dict()["schema_version"], 1)

    def test_unknown_fields_are_ignored_for_supported_schema_version(self):
        value = ExecutionContext(
            request_id="request-1",
            execution_id="execution-1",
            objective="objective",
        ).to_dict()
        value["future_optional_field"] = {"ignored": True}
        restored = ExecutionContext.from_dict(value)
        self.assertEqual(restored.execution_id, "execution-1")
        self.assertNotIn("future_optional_field", restored.to_dict())

    def test_future_schema_version_is_rejected(self):
        value = ExecutionContext(
            request_id="request-1",
            execution_id="execution-1",
            objective="objective",
        ).to_dict()
        value["schema_version"] = 2
        with self.assertRaisesRegex(ValueError, "newer than supported"):
            ExecutionContext.from_dict(value)

    def test_invalid_schema_version_is_rejected(self):
        value = ExecutionContext(
            request_id="request-1",
            execution_id="execution-1",
            objective="objective",
        ).to_dict()
        for schema_version in (True, "1", -1):
            value["schema_version"] = schema_version
            with self.subTest(schema_version=schema_version):
                with self.assertRaises(ValueError):
                    ExecutionContext.from_dict(value)


class MemoryIndexReadinessTest(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        from memory_service import MemoryService, create_http_server

        self.service = MemoryService(root / "memory.sock", root / "memory")
        self.server = create_http_server(self.service, "127.0.0.1", 0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.temp_dir.cleanup()

    def get_json(self, path):
        try:
            with urllib.request.urlopen(
                f"http://127.0.0.1:{self.server.server_port}{path}", timeout=2
            ) as response:
                return response.status, json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read().decode("utf-8"))

    def post_json(self, path, payload):
        request = urllib.request.Request(
            f"http://127.0.0.1:{self.server.server_port}{path}",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=2) as response:
                return response.status, json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read().decode("utf-8"))

    def test_liveness_is_independent_of_index_warmup(self):
        status, health = self.get_json("/health")
        self.assertEqual(status, 200)
        self.assertTrue(health["ok"])

        status, readiness = self.get_json("/ready")
        self.assertEqual(status, 503)
        self.assertFalse(readiness["ready"])
        self.assertEqual(readiness["search_index"]["state"], "not_started")

        status, search = self.post_json(
            "/memory/search-semantic", {"query": "must not cold-build before startup"}
        )
        self.assertEqual(status, 503)
        self.assertFalse(search["retryable"])
        self.assertEqual(search["search_index"]["state"], "not_started")

        self.service._warm_search_index()

        status, readiness = self.get_json("/ready")
        self.assertEqual(status, 200)
        self.assertTrue(readiness["ready"])
        self.assertEqual(readiness["search_index"]["state"], "ready")

    def test_warmup_timeout_reports_not_ready_without_failing_liveness(self):
        started = threading.Event()
        release = threading.Event()
        # Keep the initial state assertion well clear of the deadline; then
        # move the recorded start time across it deterministically.
        self.service._index_warmup_timeout_seconds = 30.0

        def slow_warmup_report(max_passes=3):
            started.set()
            release.wait(timeout=5)
            return {
                "pass": 1,
                "expected_files": [],
                "indexed_files": [],
                "failed_files": {},
                "added_files": [],
                "removed_files": [],
                "complete": True,
            }

        self.service.store.warm_search_index_report = slow_warmup_report
        worker = threading.Thread(target=self.service._warm_search_index, daemon=True)
        worker.start()
        try:
            self.assertTrue(started.wait(timeout=1))

            status, search = self.post_json(
                "/memory/search-semantic", {"query": "first prompt during warm-up"}
            )
            self.assertEqual(status, 503)
            self.assertTrue(search["retryable"])
            self.assertEqual(search["search_index"]["state"], "warming")

            with self.service._index_status_lock:
                self.service._index_started_at = time.monotonic() - 31.0
            status, readiness = self.get_json("/ready")
            self.assertEqual(status, 503)
            self.assertEqual(readiness["search_index"]["state"], "timed_out")

            status, search = self.post_json(
                "/memory/search-semantic", {"query": "first prompt after restart"}
            )
            self.assertEqual(status, 503)
            self.assertTrue(search["retryable"])
            self.assertEqual(search["error_code"], "index_not_ready")

            status, health = self.get_json("/health")
            self.assertEqual(status, 200)
            self.assertTrue(health["ok"])
        finally:
            release.set()
            worker.join(timeout=2)

        self.assertFalse(worker.is_alive())
        status, readiness = self.get_json("/ready")
        self.assertEqual(status, 200)
        self.assertTrue(readiness["ready"])

    def test_non_finite_warmup_timeout_is_rejected(self):
        for value in ("nan", "inf", "-inf", "0", "-1"):
            with self.subTest(value=value), patch.dict(
                os.environ, {"ENSEMBLE_MEMORY_WARMUP_TIMEOUT_SECONDS": value}
            ):
                from memory_service import MemoryService

                with self.assertRaisesRegex(ValueError, "finite positive"):
                    MemoryService(
                        Path(self.temp_dir.name) / "invalid.sock",
                        Path(self.temp_dir.name) / ("memory-" + value.replace("-", "minus")),
                    )

    def test_index_report_tracks_per_file_failures_and_directory_mutation(self):
        memory_dir = self.service.store.memory_dir
        memory_dir.mkdir(parents=True, exist_ok=True)
        (memory_dir / "one.md").write_text("one", encoding="utf-8")
        (memory_dir / "two.md").write_text("two", encoding="utf-8")

        original = self.service.store._indexed_chunks

        def fail_one(path):
            if path.name == "one.md":
                raise OSError("simulated unreadable file")
            return original(path)

        self.service.store._indexed_chunks = fail_one
        report = self.service.store.warm_search_index_report(max_passes=1)
        self.assertFalse(report["complete"])
        self.assertIn("one.md", report["failed_files"])
        self.assertIn("two.md", report["indexed_files"])

    def test_index_report_detects_addition_during_failed_pass(self):
        memory_dir = self.service.store.memory_dir
        memory_dir.mkdir(parents=True, exist_ok=True)
        (memory_dir / "one.md").write_text("one", encoding="utf-8")
        (memory_dir / "two.md").write_text("two", encoding="utf-8")
        original = self.service.store._indexed_chunks

        def mutate_and_fail(path):
            if path.name == "one.md":
                (memory_dir / "three.md").write_text("three", encoding="utf-8")
                raise OSError("simulated unreadable file")
            return original(path)

        self.service.store._indexed_chunks = mutate_and_fail
        report = self.service.store.warm_search_index_report(max_passes=1)
        self.assertFalse(report["complete"])
        self.assertIn("one.md", report["failed_files"])
        self.assertIn("three.md", report["added_files"])

    def test_index_report_detects_removal_during_pass(self):
        memory_dir = self.service.store.memory_dir
        memory_dir.mkdir(parents=True, exist_ok=True)
        (memory_dir / "one.md").write_text("one", encoding="utf-8")
        (memory_dir / "two.md").write_text("two", encoding="utf-8")
        original = self.service.store._indexed_chunks

        def remove_during_index(path):
            if path.name == "one.md":
                (memory_dir / "two.md").unlink()
            return original(path)

        self.service.store._indexed_chunks = remove_during_index
        report = self.service.store.warm_search_index_report(max_passes=1)
        self.assertFalse(report["complete"])
        self.assertIn("two.md", report["removed_files"])

    def test_exhausted_warmup_retries_return_non_retryable_search_error(self):
        calls = []
        def incomplete_report(max_passes=3):
            calls.append(max_passes)
            return {
                "pass": len(calls),
                "expected_files": ["broken.md"],
                "indexed_files": [],
                "failed_files": {"broken.md": "simulated read error"},
                "added_files": [],
                "removed_files": [],
                "complete": False,
            }

        self.service.store.warm_search_index_report = incomplete_report
        self.service._warm_search_index()
        self.assertEqual(len(calls), 3)
        self.assertEqual(self.service.search_index_status()["state"], "degraded")

        status, search = self.post_json(
            "/memory/search-semantic", {"query": "must not retry terminal failure"}
        )
        self.assertEqual(status, 503)
        self.assertFalse(search["retryable"])
        self.assertEqual(search["search_index"]["state"], "degraded")

    def test_warmup_recovers_after_transient_index_failure(self):
        calls = []
        original = self.service.store.warm_search_index_report

        def transient_failure(max_passes=3):
            calls.append(max_passes)
            if len(calls) == 1:
                return {
                    "pass": 1,
                    "expected_files": [],
                    "indexed_files": [],
                    "failed_files": {"temporary.md": "temporary read error"},
                    "added_files": [],
                    "removed_files": [],
                    "complete": False,
                }
            return original(max_passes=max_passes)

        self.service.store.warm_search_index_report = transient_failure
        self.service._warm_search_index()
        self.assertEqual(len(calls), 2)
        self.assertEqual(self.service.search_index_status()["state"], "ready")


if __name__ == "__main__":
    unittest.main()
