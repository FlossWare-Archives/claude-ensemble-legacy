from __future__ import annotations

import json
import unittest
from unittest.mock import patch
from urllib.error import URLError

from shared.operational_memory import OperationalMemoryWriter


class OperationalMemoryWriterTest(unittest.TestCase):
    def test_event_name_is_deterministic_and_safe(self):
        writer = OperationalMemoryWriter()
        first = writer._memory_name("learning.outcome", "task/one")
        second = writer._memory_name("learning.outcome", "task/one")
        self.assertEqual(first, second)
        self.assertTrue(first.startswith("operational-learning.outcome-"))
        self.assertRegex(first, r"^[A-Za-z0-9._-]+$")

    def test_document_preserves_full_payload(self):
        document = OperationalMemoryWriter._document(
            "task-1",
            "learning.outcome",
            "learning-service",
            {"model": "haiku", "cost": 0.005, "tokens": 1000},
        )
        self.assertIn("task-1", document)
        self.assertIn("learning.outcome", document)
        self.assertIn('"model": "haiku"', document)
        self.assertIn('"tokens": 1000', document)

    @patch.dict("os.environ", {"ENSEMBLE_MEMORY_GATEWAY_URL": ""})
    @patch("shared.operational_memory.urllib.request.urlopen")
    def test_write_event_posts_to_canonical_memory_endpoint(self, urlopen):
        class Response:
            def __enter__(self): return self
            def __exit__(self, *args): return False
            def read(self): return b'{"ok": true}'

        urlopen.return_value = Response()
        writer = OperationalMemoryWriter()

        self.assertTrue(writer.write_event("task-1", "learning.outcome", "learning-service", {"ok": True}))
        request = urlopen.call_args.args[0]
        self.assertEqual(request.full_url, "http://127.0.0.1:8767/memory/write")
        self.assertEqual(request.method, "POST")
        payload = json.loads(request.data.decode("utf-8"))
        self.assertIn("operational-learning.outcome-", payload["name"])
        self.assertIn("task-1", payload["content"])

    @patch("shared.operational_memory.urllib.request.urlopen", side_effect=URLError("offline"))
    def test_write_event_reports_memory_failure(self, _urlopen):
        writer = OperationalMemoryWriter(timeout=0.1)
        self.assertFalse(writer.write_event("task-1", "learning.outcome", "learning-service", {}))


    @patch("shared.operational_memory.urllib.request.urlopen")
    def test_write_event_handles_http_error_as_non_durable_failure(self, urlopen):
        from io import BytesIO
        from urllib.error import HTTPError

        urlopen.side_effect = HTTPError(
            "http://127.0.0.1:8767/memory/write", 503, "unavailable", {}, BytesIO(b'{"ok": false}')
        )
        writer = OperationalMemoryWriter(timeout=0.1)
        self.assertFalse(writer.write_event("task-503", "learning.outcome", "learning-service", {}))

    @patch("shared.operational_memory.urllib.request.urlopen")
    def test_required_thompson_state_http_error_raises(self, urlopen):
        from io import BytesIO
        from urllib.error import HTTPError
        from shared.operational_memory import OperationalMemoryPersistenceError

        urlopen.side_effect = HTTPError(
            "http://127.0.0.1:8767/memory/write", 500, "storage failure", {}, BytesIO(b'{"ok": false}')
        )
        writer = OperationalMemoryWriter(timeout=0.1)
        with self.assertRaises(OperationalMemoryPersistenceError):
            writer.write_event("state-500", "thompson.state", "thompson-service", {})


if __name__ == "__main__":
    unittest.main()
