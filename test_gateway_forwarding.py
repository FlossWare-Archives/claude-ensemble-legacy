"""REST gateway forwarding tests for the Graph and Memory service boundaries."""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from server.ensemble_server import create_server


class StubServiceHandler(BaseHTTPRequestHandler):
    def _respond(self):
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length).decode() if length else ""
        payload = json.dumps({
            "ok": True,
            "method": self.command,
            "path": self.path,
            "body": body,
        }).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    do_GET = _respond
    do_POST = _respond

    def log_message(self, *_args):
        pass


def request(server, method, path, payload=None):
    data = None if payload is None else json.dumps(payload).encode()
    req = urllib.request.Request(
        f"http://127.0.0.1:{server.server_port}{path}",
        data=data,
        method=method,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=3) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read())


def start(server):
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return thread


def test_gateway_forwards_graph_and_memory_requests(tmp_path, monkeypatch):
    monkeypatch.setenv("ENSEMBLE_METRICS_FILE", str(tmp_path / "metrics.jsonl"))
    downstream = ThreadingHTTPServer(("127.0.0.1", 0), StubServiceHandler)
    downstream_thread = start(downstream)
    base = f"http://127.0.0.1:{downstream.server_port}"
    gateway = create_server("127.0.0.1", 0, graph_url=base, memory_url=base)
    gateway_thread = start(gateway)
    try:
        status, graph = request(gateway, "GET", "/api/v1/graph/health")
        assert status == 200
        assert graph["path"] == "/graph/health"

        status, memory = request(gateway, "GET", "/api/v1/memory/search?q=needle")
        assert status == 200
        assert memory["path"] == "/memory/search?q=needle"

        status, created = request(gateway, "POST", "/api/v1/graph/nodes", {"name": "node-a"})
        assert status == 200
        assert created["path"] == "/graph/nodes"
        assert json.loads(created["body"]) == {"name": "node-a"}
    finally:
        gateway.shutdown()
        gateway.server_close()
        downstream.shutdown()
        downstream.server_close()
        gateway_thread.join(2)
        downstream_thread.join(2)


def test_gateway_reports_unavailable_downstream_service(tmp_path, monkeypatch):
    monkeypatch.setenv("ENSEMBLE_METRICS_FILE", str(tmp_path / "metrics.jsonl"))
    gateway = create_server("127.0.0.1", 0, graph_url="http://127.0.0.1:1", memory_url="")
    thread = start(gateway)
    try:
        status, body = request(gateway, "GET", "/api/v1/graph/health")
        assert status == 503
        assert body["ok"] is False
        assert "service unavailable" in body["error"]
    finally:
        gateway.shutdown()
        gateway.server_close()
        thread.join(2)
