"""Regression tests for strict JSONL record framing in MemoryStore."""

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).parent))

from memory_service import MemoryStore  # noqa: E402


class MemoryJsonlFramingTest(unittest.TestCase):
    def test_append_once_rejects_unterminated_final_record_without_mutating_file(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            path = root / "events.jsonl"
            existing = {
                "event_id": "prior-event",
                "payload_sha256": "0" * 64,
                "event": "prior",
            }
            original = json.dumps(existing, sort_keys=True).encode("utf-8")
            path.write_bytes(original)
            store = MemoryStore(root)

            with self.assertRaisesRegex(RuntimeError, "unterminated final record"):
                store.append_entry_once("events", "new-event", {"event": "new"})

            self.assertEqual(path.read_bytes(), original)

    def test_append_once_appends_after_a_properly_terminated_record(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            path = root / "events.jsonl"
            existing = {
                "event_id": "prior-event",
                "payload_sha256": "0" * 64,
                "event": "prior",
            }
            path.write_text(json.dumps(existing, sort_keys=True) + "\n", encoding="utf-8")
            store = MemoryStore(root)

            result = store.append_entry_once("events", "new-event", {"event": "new"})

            self.assertEqual(result["status"], "stored")
            records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
            self.assertEqual(len(records), 2)
            self.assertEqual([record["event_id"] for record in records], ["prior-event", "new-event"])
            self.assertEqual(store.append_entry_once("events", "new-event", {"event": "new"})["status"], "duplicate")
