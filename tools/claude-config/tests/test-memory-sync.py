#!/usr/bin/env python3
import hashlib
import importlib.util
import py_compile
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

memory_spec = importlib.util.spec_from_file_location("ce_memory_service", ROOT / "memory-service" / "memory_service.py")
memory_module = importlib.util.module_from_spec(memory_spec)
memory_spec.loader.exec_module(memory_module)
sync_spec = importlib.util.spec_from_file_location("ce_memory_sync", ROOT / "tools" / "claude-config" / "lib" / "memory_sync.py")
sync_module = importlib.util.module_from_spec(sync_spec)
sync_module.__dict__["__file__"] = str(ROOT / "tools" / "claude-config" / "lib" / "memory_sync.py")
sync_spec.loader.exec_module(sync_module)


class MemoryIngestTests(unittest.TestCase):
    def ingest(self, store, path, content, scope):
        digest = hashlib.sha256(content.encode()).hexdigest()
        return store.ingest_claude_markdown(
            path, content, digest, {"project": "project-a", "scope": scope}
        )

    def test_new_changed_and_unchanged(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = memory_module.MemoryStore(Path(tmp))
            path = "/home/test/.claude/projects/project-a/memory/pattern.md"
            self.assertEqual(self.ingest(store, path, "first", "/home/test/.claude")["status"], "ingested")
            digest = hashlib.sha256(b"first").hexdigest()
            self.assertEqual(
                store.ingest_claude_markdown(
                    path, "first", digest, {"project": "project-a", "scope": "/home/test/.claude"}
                )["status"],
                "unchanged",
            )
            self.assertEqual(self.ingest(store, path, "second", "/home/test/.claude")["status"], "ingested")

    def test_duplicate_basenames_remain_distinct(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = memory_module.MemoryStore(Path(tmp))
            paths = [
                "/home/test/.claude/projects/project-a/memory/MEMORY.md",
                "/home/test/.claude/projects/project-b/memory/MEMORY.md",
            ]
            for path in paths:
                self.ingest(store, path, path, "/home/test/.claude")
            index = store.list_claude_ingest()
            self.assertEqual(len(index), 2)
            self.assertNotEqual(index[paths[0]]["document"], index[paths[1]]["document"])

    def test_reconcile_isolated_by_scope(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = memory_module.MemoryStore(Path(tmp))
            path_a = "/home/test/a/.claude/projects/project-a/memory/a.md"
            path_b = "/home/test/b/.claude/projects/project-b/memory/b.md"
            self.ingest(store, path_a, "alpha", "/home/test/a/.claude")
            self.ingest(store, path_b, "beta", "/home/test/b/.claude")

            result = store.reconcile_claude_markdown("claude-code", "/home/test/a/.claude", [])
            self.assertEqual(result["count"], 1)
            index = store.list_claude_ingest()
            self.assertNotIn(path_a, index)
            self.assertIn(path_b, index)

    def test_ingest_rejects_path_outside_scope(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = memory_module.MemoryStore(Path(tmp))
            path = "/home/test/b/.claude/projects/project-b/memory/outside.md"
            digest = hashlib.sha256(b"outside").hexdigest()
            with self.assertRaises(ValueError):
                store.ingest_claude_markdown(
                    path, "outside", digest, {"project": "project-b", "scope": "/home/test/a/.claude"}
                )

    def test_reconcile_empty_valid_scope_is_safe(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = memory_module.MemoryStore(Path(tmp))
            path = "/home/test/a/.claude/projects/project-a/memory/old.md"
            self.ingest(store, path, "old", "/home/test/a/.claude")
            result = store.reconcile_claude_markdown("claude-code", "/home/test/b/.claude", [])
            self.assertEqual(result["count"], 0)
            self.assertIn(path, store.list_claude_ingest())

    def test_reconcile_marks_deletion_stale_without_removing_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = memory_module.MemoryStore(Path(tmp))
            path = "/home/test/.claude/projects/project-a/memory/old.md"
            result = self.ingest(store, path, "old", "/home/test/.claude")
            self.assertEqual(
                store.reconcile_claude_markdown("claude-code", "/home/test/.claude", [])["count"], 1
            )
            self.assertEqual(store.list_claude_ingest(), {})
            self.assertTrue((Path(tmp) / f"{result['document']}.md").exists())
            results = store.search_semantic("old")
            self.assertFalse(any(item["file"] == result["document"] for item in results))
            results = store.search(["old"])
            self.assertFalse(any(item["file"] == result["document"] for item in results))

    def test_failed_existing_update_is_repaired_on_revert(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = memory_module.MemoryStore(Path(tmp))
            path = "/home/test/.claude/projects/project-a/memory/recover.md"
            self.ingest(store, path, "A", "/home/test/.claude")
            with mock.patch.object(store, "_save_ingest_index", side_effect=OSError("disk full")):
                with self.assertRaises(OSError):
                    self.ingest(store, path, "B", "/home/test/.claude")
            document = store.list_claude_ingest()[path]["document"]
            document_path = Path(tmp) / f"{document}.md"
            self.assertEqual(document_path.read_text(), "B")
            self.assertEqual(self.ingest(store, path, "A", "/home/test/.claude")["status"], "ingested")
            self.assertEqual(document_path.read_text(), "A")

    def test_index_save_failure_is_recoverable(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = memory_module.MemoryStore(Path(tmp))
            path = "/home/test/.claude/projects/project-a/memory/recover.md"
            with mock.patch.object(store, "_save_ingest_index", side_effect=OSError("disk full")):
                with self.assertRaises(OSError):
                    self.ingest(store, path, "new", "/home/test/.claude")
            self.assertEqual((Path(tmp) / f"claude-code-{hashlib.sha256(path.encode()).hexdigest()[:32]}.md").read_text(), "new")
            self.assertEqual(self.ingest(store, path, "new", "/home/test/.claude")["status"], "ingested")


class MemoryServiceRegressionTests(unittest.TestCase):
    def test_memory_service_compiles(self):
        source = ROOT / "memory-service" / "memory_service.py"
        py_compile.compile(str(source), doraise=True)

    def test_ping_operation(self):
        service = memory_module.MemoryService(Path("/tmp/ce-regression-socket"), Path("/tmp/ce-regression-memory"))
        response = memory_module.json.loads(service._process_request('{"op":"ping"}'))
        self.assertEqual(response, {"ok": True, "message": "pong"})

    def test_hybrid_search_operation(self):
        service = memory_module.MemoryService(Path("/tmp/ce-regression-socket"), Path("/tmp/ce-regression-memory"))
        with mock.patch.object(
            service.store,
            "search",
            return_value=[{"file": "doc", "section": "intro", "score": 0.5}],
        ):
            with mock.patch.object(
                service.store,
                "search_semantic",
                return_value=[{"file": "doc", "section": "intro", "score": 0.8}],
            ):
                response = memory_module.json.loads(
                    service._process_request('{"op":"search_hybrid","query":"test","top_k":1}')
                )
        self.assertTrue(response["ok"])
        self.assertEqual(response["results"][0]["file"], "doc")
        self.assertEqual(response["results"][0]["section"], "intro")
        self.assertAlmostEqual(response["results"][0]["score"], 0.68)


class SyncTests(unittest.TestCase):
    def make_root(self, tmp):
        root = Path(tmp) / ".claude"
        (root / "projects" / "project-a" / "memory").mkdir(parents=True)
        return root

    def test_unavailable_memory_is_reported_and_reconcile_failure_is_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self.make_root(tmp)
            (root / "projects" / "project-a" / "memory" / "one.md").write_text("one", encoding="utf-8")
            calls = []
            original = sync_module.post

            def offline(path, body, **kwargs):
                calls.append(path)
                raise OSError("offline")

            sync_module.post = offline
            try:
                self.assertEqual(sync_module.sync_once(root), 1)
                self.assertEqual(calls, ["/memory/ingest", "/memory/reconcile"])
            finally:
                sync_module.post = original

    def test_missing_root_does_not_reconcile(self):
        with tempfile.TemporaryDirectory() as tmp:
            calls = []
            original = sync_module.post
            sync_module.post = lambda *args, **kwargs: calls.append(args[0])
            try:
                self.assertEqual(sync_module.sync_once(Path(tmp) / "missing"), 1)
                self.assertEqual(calls, [])
            finally:
                sync_module.post = original

    def test_payload_includes_scope(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self.make_root(tmp)
            path = root / "projects" / "project-a" / "memory" / "one.md"
            path.write_text("one", encoding="utf-8")
            value = sync_module.payload(root, path)
            self.assertEqual(value["metadata"]["scope"], str(root.resolve()))

    def test_watcher_retries_failed_sync_without_new_change(self):
        previous = {"one": (1, 1)}
        current = {"one": (2, 1)}
        calls = []

        def fake_sync(_root):
            calls.append(True)
            return 1 if len(calls) == 1 else 0

        with mock.patch.object(sync_module, "snapshot", side_effect=[current, current]):
            with mock.patch.object(sync_module, "sync_once", side_effect=fake_sync):
                with mock.patch.object(sync_module.time, "sleep", side_effect=[None, None, KeyboardInterrupt]):
                    with mock.patch.object(sync_module.time, "monotonic", side_effect=[0.0, 1.0]):
                        with self.assertRaises(KeyboardInterrupt):
                            sync_module.polling_watch(Path("/tmp/root"), 0.01, previous)
        self.assertEqual(len(calls), 2)

    def test_project_knowledge_sync_indexes_docs_and_reconciles_selected_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "project"
            (root / "docs").mkdir(parents=True)
            (root / "node_modules" / "package").mkdir(parents=True)
            (root / "docs" / "lifecycle.md").write_text(
                "SessionEnd captures metadata and does not assess task quality.",
                encoding="utf-8",
            )
            (root / "node_modules" / "package" / "vendor.md").write_text(
                "Do not index vendored documentation.", encoding="utf-8"
            )
            calls = []

            def successful_post(path, body, **kwargs):
                calls.append((path, body))
                return {"ok": True, "status": "ingested", "count": 0}

            with mock.patch.object(sync_module, "post", side_effect=successful_post):
                self.assertEqual(sync_module.sync_knowledge_root(root), 0)

            ingest_calls = [(path, body) for path, body in calls if path == "/memory/ingest"]
            reconcile_calls = [(path, body) for path, body in calls if path == "/memory/reconcile"]
            self.assertEqual(len(ingest_calls), 1)
            self.assertEqual(ingest_calls[0][1]["metadata"]["scope"], str(root.resolve()))
            self.assertEqual(ingest_calls[0][1]["metadata"]["relative_path"], "docs/lifecycle.md")
            self.assertIn("does not assess task quality", ingest_calls[0][1]["content"])
            self.assertEqual(len(reconcile_calls), 1)
            self.assertEqual(reconcile_calls[0][1]["scope"], str(root.resolve()))
            self.assertEqual(reconcile_calls[0][1]["paths"], [str((root / "docs" / "lifecycle.md").resolve())])

    def test_project_knowledge_sync_does_not_reconcile_after_ingest_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "project"
            root.mkdir()
            (root / "guide.md").write_text("Durable project facts.", encoding="utf-8")
            calls = []

            def failing_post(path, body, **kwargs):
                calls.append(path)
                if path == "/memory/ingest":
                    raise OSError("service unavailable")
                return {"ok": True, "count": 0}

            with mock.patch.object(sync_module, "post", side_effect=failing_post):
                self.assertEqual(sync_module.sync_knowledge_root(root), 1)
            self.assertEqual(calls, ["/memory/ingest"])

    def test_watch_performs_initial_sync(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self.make_root(tmp)
            with mock.patch.object(sync_module, "sync_once", return_value=0) as sync:
                with mock.patch.object(sync_module, "snapshot", return_value={}):
                    with mock.patch.object(sync_module, "inotify_fds", return_value=None):
                        with mock.patch.object(sync_module, "polling_watch", return_value=0):
                            self.assertEqual(sync_module.watch(root, 0.01), 0)
            sync.assert_called_once_with(root)


if __name__ == "__main__":
    unittest.main()
