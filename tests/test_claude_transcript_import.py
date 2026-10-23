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

    def test_rejects_malformed_jsonl_with_line(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "session.jsonl"
            path.write_text("{broken}\n")
            with self.assertRaisesRegex(ValueError, r":1: invalid JSONL"):
                list(iter_records(path))


if __name__ == "__main__":
    unittest.main()
