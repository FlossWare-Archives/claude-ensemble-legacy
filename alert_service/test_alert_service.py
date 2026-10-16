#!/usr/bin/env python3
"""Tests for the alert service."""

import json
import os
import socket
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from datetime import datetime
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

    def test_scheduled_checks_run_when_due_and_deduplicate_same_day_alerts(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            sys.path.insert(0, str(Path(__file__).parent.parent))
            from alert_service.alert_service import AlertService

            service = AlertService(
                socket_path=Path(temp_dir) / "alert.sock",
                alert_dir=Path(temp_dir) / "alerts",
            )

            class FakeManager:
                calls = 0

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

            service.manager = FakeManager()
            published = []
            service._publish_messenger_alert = lambda alert, source: published.append((dict(alert), source)) or True
            service.check_interval_seconds = 30
            service.last_check_time = 100.0

            self.assertFalse(service._run_scheduled_check_if_due(now=129.9))
            self.assertTrue(service._run_scheduled_check_if_due(now=130.0))
            self.assertEqual(service.manager.calls, 1)
            self.assertEqual(len(published), 1)
            self.assertEqual(published[0][1], "scheduled")
            self.assertTrue(published[0][0]["alert_id"].startswith(datetime.now().date().isoformat()))

            # The same alert type is not republished on every interval that day.
            self.assertFalse(service._run_scheduled_check_if_due(now=140.0))
            self.assertEqual(service.manager.calls, 1)
            duplicate = service._process_alerts(service.manager.trigger_check(), source="manual")
            self.assertEqual(duplicate, [])
            self.assertEqual(len(published), 1)

    def test_alerts_publish_through_messenger_and_record_delivery(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            sys.path.insert(0, str(Path(__file__).parent.parent))
            from alert_service.alert_service import AlertService

            service = AlertService(
                socket_path=Path(temp_dir) / "alert.sock",
                alert_dir=Path(temp_dir) / "alerts",
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

    def test_email_delivery_is_never_reported_as_success_when_unimplemented(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            sys.path.insert(0, str(Path(__file__).parent.parent))
            from alert_service.alert_service import AlertService

            service = AlertService(
                socket_path=Path(temp_dir) / "alert.sock",
                alert_dir=Path(temp_dir) / "alerts",
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
