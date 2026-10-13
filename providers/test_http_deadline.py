"""Local-only regression tests for provider HTTP deadlines."""
from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from providers.http_client import ProviderHTTPError, post_json


class _SlowProgressHandler(BaseHTTPRequestHandler):
    body_delay = 0.07
    chunks = (b'{"', b'o', b'k', b'":', b't', b'r', b'u', b'e', b'}')

    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length", "0")))
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(sum(map(len, self.chunks))))
        self.end_headers()
        try:
            for chunk in self.chunks:
                self.wfile.write(chunk)
                self.wfile.flush()
                time.sleep(self.body_delay)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def log_message(self, *_args):
        pass


@pytest.fixture
def local_server():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _SlowProgressHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=1)


def test_slow_progress_body_cannot_exceed_overall_deadline(local_server):
    started = time.monotonic()
    with pytest.raises(ProviderHTTPError, match="overall request deadline exceeded"):
        post_json(
            provider="test",
            url=local_server,
            payload={"prompt": "test"},
            headers={},
            timeout=0.30,
        )
    elapsed = time.monotonic() - started
    assert elapsed < 1.5  # Includes process termination and cleanup tolerance.


def test_normal_response_within_deadline_is_decoded(local_server):
    _SlowProgressHandler.body_delay = 0.001
    try:
        body, headers = post_json(
            provider="test",
            url=local_server,
            payload={"prompt": "test"},
            headers={},
            timeout=3.0,
        )
        assert body == {"ok": True}
        assert headers["Content-Type"] == "application/json"
    finally:
        _SlowProgressHandler.body_delay = 0.07


def test_nonserializable_payload_is_rejected_before_transport():
    from unittest.mock import patch

    with patch("providers.http_client.subprocess.Popen") as launch:
        with pytest.raises(ProviderHTTPError, match="not JSON serializable"):
            post_json(
                provider="test", url="http://127.0.0.1:1/",
                payload={"invalid": object()}, headers={}, timeout=1.0,
            )
    launch.assert_not_called()


def test_invalid_timeout_is_rejected_before_transport():
    for timeout in (True, 0, -1, float("nan"), float("inf"), "1"):
        with pytest.raises(ValueError, match="finite number greater than zero"):
            post_json(
                provider="test", url="http://127.0.0.1:1/",
                payload={}, headers={}, timeout=timeout,
            )
