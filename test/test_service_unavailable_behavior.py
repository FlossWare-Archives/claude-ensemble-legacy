#!/usr/bin/env python3
"""Contract tests for clients when their backing service is unavailable."""

import sys
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "memory-service"))
sys.path.insert(0, str(ROOT / "session-messaging"))

from shared.thompson_client import ThompsonClient
from learning.learning_client import LearningClient
from memory_client import MemoryClient
from tools.alert_client import AlertClient
from messenger_client import MessengerClient
from shared.operational_memory import (
    OperationalMemoryPersistenceError,
    OperationalMemoryWriter,
)


class ServiceUnavailableBehaviorTests(unittest.TestCase):
    def missing_socket(self, root: str, name: str) -> Path:
        return Path(root) / name

    def test_thompson_uses_documented_model_fallback_but_reports_write_failure(self):
        with tempfile.TemporaryDirectory() as root:
            client = ThompsonClient(
                socket_path=self.missing_socket(root, "thompson.sock"),
                enable_circuit_breaker=False,
            )
            with self.assertLogs("shared.thompson_client", level="WARNING") as logs:
                self.assertFalse(client.ping())
                self.assertEqual(client.select_model("test"), "haiku")
                self.assertFalse(client.record_outcome("haiku", "test", True, 0.0))
            self.assertTrue(any("service unavailable" in item.lower() for item in logs.output))

    def test_learning_returns_failure_for_writes_and_report_reads(self):
        with tempfile.TemporaryDirectory() as root:
            client = LearningClient(self.missing_socket(root, "learning.sock"))
            with self.assertLogs("learning.learning_client", level="WARNING"):
                self.assertFalse(
                    client.process_outcome("task-1", "test", "haiku", 4, 10, 0.0)
                )
                report = client.get_report()
            self.assertFalse(report["ok"])
            self.assertTrue(report["error"])

    def test_memory_offline_append_is_cached_but_not_reported_as_durable(self):
        with tempfile.TemporaryDirectory() as root:
            client = MemoryClient(socket_path=self.missing_socket(root, "memory.sock"))
            with self.assertLogs("memory_client", level="WARNING") as logs:
                self.assertFalse(client.append("events", {"event_id": "event-1"}))
            self.assertEqual(client.offline_cache["events"], [{"event_id": "event-1"}])
            self.assertTrue(any("not durable" in item.lower() for item in logs.output))

    def test_memory_application_rejection_is_not_cached_as_an_outage(self):
        with tempfile.TemporaryDirectory() as root:
            client = MemoryClient(socket_path=self.missing_socket(root, "memory.sock"))
            with patch.object(
                client,
                "_send_request",
                return_value={"ok": False, "error": "invalid memory name"},
            ):
                with self.assertLogs("memory_client", level="ERROR") as logs:
                    self.assertFalse(client.append("../invalid", {"event_id": "event-2"}))
            self.assertEqual(client.offline_cache, {})
            self.assertTrue(any("rejected append" in item.lower() for item in logs.output))

    def test_alert_operations_report_unavailable_service(self):
        with tempfile.TemporaryDirectory() as root:
            client = AlertClient(socket_path=self.missing_socket(root, "alert.sock"))
            with self.assertLogs("tools.alert_client", level="WARNING") as logs:
                self.assertFalse(client.connect())
                self.assertEqual(client.trigger_check(), [])
                self.assertFalse(client.acknowledge("alert-1"))
            self.assertTrue(any("not running" in item.lower() for item in logs.output))
            self.assertTrue(any("failed to trigger check" in item.lower() for item in logs.output))

    def test_messenger_fails_closed_when_socket_is_missing(self):
        with tempfile.TemporaryDirectory() as root:
            client = MessengerClient(self.missing_socket(root, "messenger.sock"))
            with self.assertRaises(FileNotFoundError):
                client.publish("events", {"event_id": "event-1"})
            with self.assertRaises(FileNotFoundError):
                next(client.subscribe("events", reconnect=False))

    def test_optional_operational_memory_write_returns_false_when_unavailable(self):
        writer = OperationalMemoryWriter(base_url="http://127.0.0.1:1/memory", timeout=0.01)
        with patch(
            "shared.operational_memory.urllib.request.urlopen",
            side_effect=urllib.error.URLError("service unavailable"),
        ):
            self.assertFalse(
                writer.write_event("event-1", "test.event", "test", {"ok": True})
            )

    def test_required_thompson_state_persistence_raises_when_unavailable(self):
        writer = OperationalMemoryWriter(base_url="http://127.0.0.1:1/memory", timeout=0.01)
        with patch(
            "shared.operational_memory.urllib.request.urlopen",
            side_effect=urllib.error.URLError("service unavailable"),
        ):
            with self.assertRaises(OperationalMemoryPersistenceError):
                writer.write_event("event-1", "thompson.state", "thompson", {"state": {}})


if __name__ == "__main__":
    unittest.main()
