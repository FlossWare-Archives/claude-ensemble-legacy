#!/usr/bin/env python3
"""Tests for the alert service."""

import json
import os
import socket
import stat
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from datetime import datetime, timezone
from pathlib import Path


SERVICE = Path(__file__).with_name("alert_service.py")


def send_request(socket_path: Path, request: dict) -> dict:
    """Send a JSON request to the alert service and get response."""
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


class AlertServiceTest(unittest.TestCase):
    def test_alert_store_initialization(self):
        """Verify AlertStore initializes config correctly."""
        with tempfile.TemporaryDirectory() as temp_dir:
            alerts_dir = Path(temp_dir) / "alerts"

            # Import the AlertStore class from alert_service module
            import sys
            sys.path.insert(0, str(Path(__file__).parent.parent))
            from alert_service.alert_service import AlertStore

            # Create store - should initialize config
            store = AlertStore(alerts_dir)

            config_file = alerts_dir / "config.json"
            self.assertTrue(config_file.exists(), "config.json should be created")

            config = json.loads(config_file.read_text())
            self.assertIn("cost_spike_threshold_multiplier", config)
            self.assertIn("quality_drop_threshold", config)
            self.assertIn("enabled", config)
            self.assertIn("email_recipient", config)

    def test_alert_store_get_config(self):
        """Verify AlertStore reads config correctly."""
        with tempfile.TemporaryDirectory() as temp_dir:
            alerts_dir = Path(temp_dir) / "alerts"

            import sys
            sys.path.insert(0, str(Path(__file__).parent.parent))
            from alert_service.alert_service import AlertStore

            store = AlertStore(alerts_dir)
            config = store.get_config()

            self.assertIsInstance(config, dict)
            self.assertGreater(len(config), 0)
            self.assertEqual(config.get("enabled"), True)

    def test_alert_store_save_alert(self):
        """Verify AlertStore saves alerts to file."""
        with tempfile.TemporaryDirectory() as temp_dir:
            alerts_dir = Path(temp_dir) / "alerts"

            import sys
            sys.path.insert(0, str(Path(__file__).parent.parent))
            from alert_service.alert_service import AlertStore

            store = AlertStore(alerts_dir)

            alert = {
                "alert_type": "cost_spike",
                "severity": "high",
                "message": "Cost exceeded threshold",
                "timestamp": datetime.now().isoformat()
            }

            success = store.save_alert(alert)
            self.assertTrue(success, "save_alert should succeed")

            # Check alert file was created
            alert_files = list(alerts_dir.glob("cost_spike_*.json"))
            self.assertEqual(len(alert_files), 1, "should have created one alert file")

            # Verify alert content
            saved_alert = json.loads(alert_files[0].read_text())
            self.assertEqual(saved_alert["alert_type"], "cost_spike")

    def test_quality_alert_reads_canonical_and_legacy_learning_outcomes(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            sys.path.insert(0, str(Path(__file__).parent.parent))
            from alert_service.alert_service import AlertManager

            repo_root = Path(temp_dir)
            canonical_dir = repo_root / "learning" / "autonomous_outcomes"
            legacy_dir = repo_root / "learning" / "post_task_outcomes"
            canonical_dir.mkdir(parents=True)
            legacy_dir.mkdir(parents=True)
            now = datetime.now(timezone.utc).isoformat()

            (canonical_dir / "one.json").write_text(json.dumps({
                "task_id": "task-one", "rating": 2, "timestamp": now,
            }))
            (canonical_dir / "two.json").write_text(json.dumps({
                "task_id": "task-two", "rating": 2, "timestamp": now,
            }))
            # The legacy copy of task-one must not double-count or override the
            # canonical outcome's rating.
            (legacy_dir / "one-legacy.json").write_text(json.dumps({
                "task_id": "task-one",
                "outcome": {"timestamp": now},
                "comparison": {"final_rating": 5},
            }))

            manager = AlertManager.__new__(AlertManager)
            manager.repo_root = repo_root
            manager.alert_dir = repo_root / "alerts"
            alert = manager.check_quality_drop(rating_threshold=3.0, window_days=7)

            self.assertIsNotNone(alert)
            self.assertEqual(alert["alert_type"], "quality_drop")
            self.assertEqual(alert["metrics"]["sample_size"], 2)
            self.assertEqual(alert["metrics"]["avg_rating"], 2.0)

    def test_scheduled_checks_run_when_due_and_deduplicate_same_day_alerts(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            sys.path.insert(0, str(Path(__file__).parent.parent))
            from alert_service.alert_service import AlertService

            class FakeManager:
                def __init__(self):
                    self.calls = 0

                def trigger_check(self):
                    self.calls += 1
                    return [{
                        "alert_type": "cost_spike",
                        "severity": "warning",
                        "timestamp": datetime.now().isoformat(),
                        "message": "Cost exceeded threshold",
                        "metrics": {"today_cost": 4.0, "baseline": 1.0},
                        "action_recommended": "Review model selection",
                    }]

            manager = FakeManager()
            service = AlertService(
                socket_path=Path(temp_dir) / "alert.sock",
                alert_dir=Path(temp_dir) / "alerts",
                manager=manager,
            )
            published = []

            def fake_publish(alert, source):
                published.append((dict(alert), source))
                service.store.append_delivery_log({
                    "alert_id": alert["alert_id"],
                    "alert_type": alert["alert_type"],
                    "method": "messenger",
                    "success": True,
                    "delivered": 1,
                })
                return True

            service._publish_messenger_alert = fake_publish
            service.check_interval_seconds = 30
            service.last_check_time = 100.0

            self.assertFalse(service._run_scheduled_check_if_due(now=129.9))
            self.assertTrue(service._run_scheduled_check_if_due(now=130.0))
            self.assertEqual(manager.calls, 1)
            self.assertEqual(len(published), 1)
            self.assertEqual(published[0][1], "scheduled")
            self.assertTrue(published[0][0]["alert_id"].startswith(datetime.now().date().isoformat()))

            # The same alert type is not republished on every interval that day.
            self.assertFalse(service._run_scheduled_check_if_due(now=140.0))
            self.assertEqual(service.manager.calls, 1)
            duplicate = service._process_alerts(manager.trigger_check(), source="manual")
            self.assertEqual(duplicate, [])
            self.assertEqual(len(published), 1)

    def test_service_loop_runs_periodic_checks_without_client_requests(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            sys.path.insert(0, str(Path(__file__).parent.parent))
            from alert_service.alert_service import AlertService

            class FakeManager:
                def __init__(self):
                    self.calls = 0

                def trigger_check(self):
                    self.calls += 1
                    return []

            manager = FakeManager()
            service = AlertService(
                socket_path=Path(temp_dir) / "alert.sock",
                alert_dir=Path(temp_dir) / "alerts",
                manager=manager,
            )
            service.check_interval_seconds = 0.05
            thread = threading.Thread(target=service.run, daemon=True)
            thread.start()
            deadline = time.monotonic() + 2.0
            while manager.calls < 2 and time.monotonic() < deadline:
                time.sleep(0.01)
            try:
                self.assertGreaterEqual(manager.calls, 2, "startup and scheduled checks should run")
            finally:
                service.stop()
                thread.join(timeout=2)
            self.assertFalse(thread.is_alive(), "service should stop cleanly")

    def test_alerts_publish_through_messenger_and_record_delivery(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            sys.path.insert(0, str(Path(__file__).parent.parent))
            from alert_service.alert_service import AlertService

            service = AlertService(
                socket_path=Path(temp_dir) / "alert.sock",
                alert_dir=Path(temp_dir) / "alerts",
                manager=SimpleNamespace(trigger_check=lambda: []),
            )
            published = []

            class FakeMessengerClient:
                def publish(self, topic, data, *, event_type=None):
                    published.append((topic, data, event_type))
                    return 2

            with patch.dict(sys.modules, {"messenger_client": SimpleNamespace(MessengerClient=FakeMessengerClient)}):
                success = service._publish_messenger_alert({
                    "alert_id": "alert-123",
                    "alert_type": "quality_drop",
                    "severity": "critical",
                    "timestamp": datetime.now().isoformat(),
                    "message": "Quality degradation",
                    "metrics": {"average_rating": 2.0},
                    "action_recommended": "Review model selection",
                }, source="scheduled")

            self.assertTrue(success)
            self.assertEqual(len(published), 1)
            topic, data, event_type = published[0]
            self.assertEqual(topic, "alerts")
            self.assertEqual(event_type, "alert.triggered")
            self.assertEqual(data["alert_id"], "alert-123")
            self.assertEqual(data["source"], "scheduled")
            delivery_log = json.loads((Path(temp_dir) / "alerts" / "delivery_log.jsonl").read_text().splitlines()[0])
            self.assertEqual(delivery_log["method"], "messenger")
            self.assertTrue(delivery_log["success"])
            self.assertEqual(delivery_log["delivered"], 2)

    def test_failed_messenger_alert_delivery_is_retried_without_duplicate_storage(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            sys.path.insert(0, str(Path(__file__).parent.parent))
            from alert_service.alert_service import AlertService

            service = AlertService(
                socket_path=Path(temp_dir) / "alert.sock",
                alert_dir=Path(temp_dir) / "alerts",
                manager=SimpleNamespace(trigger_check=lambda: []),
            )
            attempts = []

            class FlakyMessengerClient:
                def publish(self, topic, data, *, event_type=None):
                    attempts.append((topic, data, event_type))
                    if len(attempts) == 1:
                        raise OSError("mock Messenger unavailable")
                    return 1

            alert = {
                "alert_type": "model_error",
                "severity": "high",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "message": "Model invocation failed",
                "metrics": {"error_count": 3},
                "action_recommended": "Check provider connectivity",
            }
            with patch.dict(sys.modules, {"messenger_client": SimpleNamespace(MessengerClient=FlakyMessengerClient)}):
                first = service._process_alerts([alert], source="startup")
                second = service._process_alerts([alert], source="scheduled")
                third = service._process_alerts([alert], source="scheduled")

            self.assertEqual(len(first), 1)
            self.assertEqual(second, [])
            self.assertEqual(third, [])
            self.assertEqual(len(attempts), 2, "failed delivery retries once the broker becomes available")
            alert_files = list((Path(temp_dir) / "alerts").glob("model_error_*.json"))
            self.assertEqual(len(alert_files), 1, "retry must not duplicate the persisted alert")
            delivery_entries = [
                json.loads(line)
                for line in (Path(temp_dir) / "alerts" / "delivery_log.jsonl").read_text().splitlines()
            ]
            self.assertEqual([entry["success"] for entry in delivery_entries], [False, True])

    def test_email_delivery_is_never_reported_as_success_when_unimplemented(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            sys.path.insert(0, str(Path(__file__).parent.parent))
            from alert_service.alert_service import AlertService

            service = AlertService(
                socket_path=Path(temp_dir) / "alert.sock",
                alert_dir=Path(temp_dir) / "alerts",
                manager=SimpleNamespace(trigger_check=lambda: []),
            )
            result = service._send_email_alert({
                "alert_id": "alert-email",
                "alert_type": "cost_spike",
                "severity": "warning",
                "message": "Cost spike",
            })
            self.assertFalse(result)
            entry = json.loads((Path(temp_dir) / "alerts" / "delivery_log.jsonl").read_text().splitlines()[0])
            self.assertEqual(entry["method"], "email")
            self.assertFalse(entry["success"])
            self.assertEqual(entry["status"], "not_configured")


if __name__ == "__main__":
    unittest.main()
