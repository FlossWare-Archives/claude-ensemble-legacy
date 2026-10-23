"""Tests for safe Claude Code source discovery and archiving."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import tempfile
import unittest
import sys
from pathlib import Path


SCRIPT = Path(__file__).with_name("claude-code-source-sync.py")
SPEC = importlib.util.spec_from_file_location("claude_code_source_sync", SCRIPT)
assert SPEC and SPEC.loader
sync = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = sync
SPEC.loader.exec_module(sync)


class ClaudeCodeSourceSyncTests(unittest.TestCase):
    def test_discovery_labels_global_project_and_auto_memory_sources(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            claude_home = root / ".claude"
            project = root / "work" / "sample"
            (claude_home / "rules").mkdir(parents=True)
            (claude_home / "CLAUDE.md").write_text("# Global\n", encoding="utf-8")
            (claude_home / "rules" / "style.md").write_text("# Style\n", encoding="utf-8")
            (project / "nested" / ".claude" / "rules").mkdir(parents=True)
            (project / "CLAUDE.md").write_text("# Project\n", encoding="utf-8")
            (project / "nested" / "AGENTS.md").write_text("# Nested\n", encoding="utf-8")
            (project / "nested" / ".claude" / "rules" / "local.md").write_text("# Rule\n", encoding="utf-8")
            encoded = sync.encode_project_root(project.resolve())
            memory = claude_home / "projects" / encoded / "memory"
            memory.mkdir(parents=True)
            (memory / "MEMORY.md").write_text("# Auto memory\n", encoding="utf-8")

            sources, scopes, unmapped = sync.discover_sources(claude_home, [project])
            by_kind = {(item.kind, item.path.name): item for item in sources}
            self.assertIn(("claude-md", "CLAUDE.md"), by_kind)
            self.assertIn(("claude-rule", "style.md"), by_kind)
            self.assertIn(("agents-md", "AGENTS.md"), by_kind)
            self.assertIn(("auto-memory", "MEMORY.md"), by_kind)
            self.assertTrue(any(item.kind == "claude-md" and item.scope_type == "global" for item in sources))
            self.assertEqual(by_kind[("auto-memory", "MEMORY.md")].project_root, str(project.resolve()))
            self.assertEqual(unmapped, [])
            self.assertTrue(scopes)

    def test_unmapped_auto_memory_is_reported_and_not_imported(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            claude_home = root / ".claude"
            memory = claude_home / "projects" / "unknown-project" / "memory"
            memory.mkdir(parents=True)
            (memory / "MEMORY.md").write_text("# Private project note\n", encoding="utf-8")

            sources, _, unmapped = sync.discover_sources(claude_home, [])
            self.assertFalse(any(item.kind == "auto-memory" for item in sources))
            self.assertEqual(len(unmapped), 1)
            self.assertIn("prevent cross-project leakage", unmapped[0]["reason"])

    def test_colliding_encoded_project_keys_are_excluded_as_ambiguous(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            claude_home = root / ".claude"
            first = root / "a-b" / "c"
            second = root / "a" / "b-c"
            first.mkdir(parents=True)
            second.mkdir(parents=True)
            self.assertEqual(sync.encode_project_root(first.resolve()), sync.encode_project_root(second.resolve()))

            memory = claude_home / "projects" / sync.encode_project_root(first.resolve()) / "memory"
            memory.mkdir(parents=True)
            (memory / "MEMORY.md").write_text("# Ambiguous\n", encoding="utf-8")

            sources, _, unmapped = sync.discover_sources(claude_home, [first, second])
            self.assertFalse(any(item.kind == "auto-memory" for item in sources))
            self.assertEqual(len(unmapped), 1)
            self.assertIn("attribution is ambiguous", unmapped[0]["reason"])

    def test_source_archive_keeps_content_addressed_revisions_and_provenance(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source_path = root / "project" / "CLAUDE.md"
            source_path.parent.mkdir()
            source_path.write_text("# Version one\n", encoding="utf-8")
            source, _, _ = sync.discover_sources(root / ".claude", [source_path.parent])
            item = next(item for item in source if item.path == source_path.resolve())
            archive = root / "shared" / "data" / "memory-sources"

            first = sync.source_record(item)
            revision_one = sync.persist_source_copy(first, item, archive)
            self.assertEqual(revision_one.read_text(encoding="utf-8"), "# Version one\n")
            manifest_path = revision_one.parent.parent / "source.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(manifest["original_path"], str(source_path.resolve()))
            self.assertEqual(manifest["latest_sha256"], hashlib.sha256(b"# Version one\n").hexdigest())

            source_path.write_text("# Version two\n", encoding="utf-8")
            second = sync.source_record(item)
            revision_two = sync.persist_source_copy(second, item, archive)
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            self.assertNotEqual(revision_one, revision_two)
            self.assertTrue(revision_one.is_file())
            self.assertTrue(revision_two.is_file())
            self.assertEqual(len(manifest["revisions"]), 2)
            self.assertEqual(manifest["latest_sha256"], second["sha256"])


if __name__ == "__main__":
    unittest.main()
