"""Regression test: negative business acknowledgement is logged as an operation error."""
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SERVICE_PATH = Path(__file__).with_name("learning_service.py")
SPEC = importlib.util.spec_from_file_location("ce_learning_service_logging_test", SERVICE_PATH)
SERVICE = importlib.util.module_from_spec(SPEC)
import sys
sys.modules[SPEC.name] = SERVICE
SPEC.loader.exec_module(SERVICE)


class FakeConnection:
    def __init__(self, request):
        self.request = request.encode("utf-8")
        self.sent = b""
        self.closed = False

    def settimeout(self, _timeout):
        pass

    def recv(self, _size):
        request, self.request = self.request, b""
        return request

    def sendall(self, data):
        self.sent += data

    def close(self):
        self.closed = True


class LearningResponseLoggingTests(unittest.TestCase):
    def test_negative_ack_is_logged_as_error_and_response_is_still_sent(self):
        with tempfile.TemporaryDirectory() as temp:
            service = SERVICE.LearningService(
                socket_path=Path(temp) / "learning.sock",
                learning_dir=Path(temp) / "learning",
            )
            conn = FakeConnection(json.dumps({"op": "unsupported-test-op"}) + "\\n")
            with patch.object(service, "_process_request", return_value=json.dumps({
                "ok": False, "error": "operation_failed"
            })), patch.object(SERVICE.RequestContext, "log_exit") as log_exit:
                service._handle_client(conn)

            self.assertEqual(json.loads(conn.sent.decode("utf-8")), {
                "ok": False, "error": "operation_failed"
            })
            self.assertTrue(conn.closed)
            log_exit.assert_called_once_with(status="error", error_code="operation_failed")


if __name__ == "__main__":
    unittest.main()
