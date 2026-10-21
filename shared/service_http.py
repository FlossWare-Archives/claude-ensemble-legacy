"""Small HTTP/JSON adapter shared by independently deployed CE services.

Learning and Thompson expose only loopback HTTP/JSON. This adapter does not
provide Unix-domain socket compatibility or any additional transport.
"""
from __future__ import annotations

import json
import logging
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from typing import Callable, Any

LOG = logging.getLogger(__name__)


def start_service_http(
    service_name: str,
    port: int,
    process_request: Callable[[str], str],
    action_field: str,
) -> ThreadingHTTPServer:
    """Start a loopback-only HTTP adapter in a daemon thread.

    GET /health is a side-effect-free health probe. GET /<service>/ping checks
    the same request-processing path used by POST /<service>. POST accepts the
    service's existing JSON operation payload, preserving business semantics.
    """
    prefix = f"/{service_name}"

    class Handler(BaseHTTPRequestHandler):
        server_version = "FlossWareServiceREST/1"

        def _send(self, status: int, payload: dict[str, Any]) -> None:
            raw = (json.dumps(payload, sort_keys=True) + "\n").encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_GET(self) -> None:
            if self.path == "/health":
                self._send(HTTPStatus.OK, {"ok": True, "service": service_name})
                return
            if self.path == prefix + "/ping":
                request = {action_field: "ping"}
                self._process(request)
                return
            self._send(HTTPStatus.NOT_FOUND, {"ok": False, "error": "not found"})

        def do_POST(self) -> None:
            if self.path != prefix:
                self._send(HTTPStatus.NOT_FOUND, {"ok": False, "error": "not found"})
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if length <= 0 or length > 1024 * 1024:
                    raise ValueError("request body must be between 1 byte and 1 MiB")
                payload = json.loads(self.rfile.read(length).decode("utf-8"))
                if not isinstance(payload, dict):
                    raise ValueError("request body must be a JSON object")
            except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
                self._send(HTTPStatus.BAD_REQUEST, {"ok": False, "error": str(exc)})
                return
            self._process(payload)

        def _process(self, payload: dict[str, Any]) -> None:
            if payload.get(action_field) == "ping":
                # Still invoke the service processor so this verifies that the
                # application is initialized, not merely that a port is open.
                pass
            try:
                raw = process_request(json.dumps(payload))
                response = json.loads(raw)
                if not isinstance(response, dict):
                    raise ValueError("service response must be a JSON object")
            except Exception:
                LOG.exception("%s REST request failed", service_name)
                self._send(HTTPStatus.INTERNAL_SERVER_ERROR,
                           {"ok": False, "error": "internal service error"})
                return
            status = HTTPStatus.OK if response.get("ok") is True else HTTPStatus.SERVICE_UNAVAILABLE
            self._send(status, response)

        def log_message(self, fmt: str, *args: Any) -> None:
            LOG.info("%s REST %s", service_name, fmt % args)

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    thread = Thread(target=server.serve_forever, name=f"{service_name}-http", daemon=True)
    thread.start()
    LOG.info("%s REST listening on http://127.0.0.1:%s", service_name, port)
    return server
