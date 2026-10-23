import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

MODULE_PATH = Path(__file__).resolve().parents[1] / "tools" / "claude-transcript-import.py"
SPEC = importlib.util.spec_from_file_location("claude_transcript_import", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(MODULE)
iter_records = MODULE.iter_records
normalize_record = MODULE.normalize_record
transcript_identity = MODULE.transcript_identity
normalized_markdown = MODULE.normalized_markdown
discover = MODULE.discover
archive_transcript = MODULE.archive_transcript


class TranscriptImportTest(unittest.TestCase):
    def test_identity_hashes_source_without_emitting_content(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "session.jsonl"
            path.write_text(json.dumps({"type": "user", "message": {"role": "user", "content": "private"}}) + "\n")
            identity = transcript_identity(path)
            self.assertEqual(identity["source_type"], "claude-code-transcript")
            self.assertEqual(len(identity["content_sha256"]), 64)
            self.assertNotIn("private", json.dumps(identity))

    def test_normalizes_text_with_line_provenance(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "session.jsonl"
            item = normalize_record({"sessionId": "s1", "timestamp": "t", "message": {"role": "user", "content": "hello"}}, path, 3)
            self.assertEqual(item["text"], "hello")
            self.assertEqual(item["line_number"], 3)
            self.assertEqual(item["session_id"], "s1")

    def test_normalized_markdown_keeps_roles_and_tool_events(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "session.jsonl"
            records = [
                {"sessionId": "s1", "timestamp": "2026-10-23T10:00:00Z", "message": {"role": "assistant", "content": [{"type": "tool_use", "name": "Read", "input": {"file_path": "/tmp/a"}}]}},
                {"sessionId": "s1", "timestamp": "2026-10-23T10:00:01Z", "message": {"role": "user", "content": "hello"}},
            ]
            path.write_text("\n".join(json.dumps(item) for item in records) + "\n")
            markdown, identity = normalized_markdown(path)
            self.assertIn("assistant", markdown)
            self.assertIn("Tool call: Read", markdown)
            self.assertIn("source line 2", markdown)
            self.assertEqual(identity["events"], 2)
            self.assertEqual(identity["session_ids"], ["s1"])

    def test_discovery_requires_unambiguous_explicit_project_mapping(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "projects"
            project = Path(directory) / "repo"
            transcript_dir = root / str(project).replace("/", "-")
            transcript_dir.mkdir(parents=True)
            project.mkdir()
            (transcript_dir / "session.jsonl").write_text(json.dumps({"message": {"role": "user", "content": "hello"}}) + "\n")
            mapped, unmapped = discover(root, [project])
            self.assertEqual(len(mapped), 1)
            self.assertEqual(mapped[0]["project_root"], project.resolve())
            self.assertEqual(unmapped, [])
            mapped, unmapped = discover(root, [])
            self.assertEqual(mapped, [])
            self.assertEqual(len(unmapped), 1)

    def test_archive_preserves_original_and_is_content_addressed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "session.jsonl"
            raw = json.dumps({"message": {"role": "user", "content": "private"}}) + "\n"
            source.write_text(raw)
            identity = transcript_identity(source, root)
            destination = root / "archive"
            first = archive_transcript(source, identity, destination)
            second = archive_transcript(source, identity, destination)
            self.assertEqual(first, second)
            self.assertEqual(first.read_text(), raw)
            self.assertEqual(source.read_text(), raw)
            self.assertEqual(first.stat().st_mode & 0o777, 0o600)

    def test_rejects_malformed_jsonl_with_line(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "session.jsonl"
            path.write_text("{broken}\n")
            with self.assertRaisesRegex(ValueError, r":1: invalid JSONL"):
                list(iter_records(path))


if __name__ == "__main__":
    unittest.main()
