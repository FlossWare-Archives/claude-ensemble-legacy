"""REST gateway forwarding tests for direct and nested service boundaries."""

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
        status = 418 if self.path.split("?", 1)[0].endswith("/failure") else 200
        payload = json.dumps(
            {
                "ok": status == 200,
                "method": self.command,
                "path": self.path,
                "body": body,
                **({"error": "upstream teapot"} if status != 200 else {}),
            }
        ).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    do_GET = _respond
    do_POST = _respond
    do_PUT = _respond
    do_DELETE = _respond

    def log_message(self, *_args):
        pass


class HealthServiceHandler(BaseHTTPRequestHandler):
    """Minimal concrete-service health endpoint with an unambiguous identity."""

    service_name = "service"

    def do_GET(self):
        payload = json.dumps(
            {"ok": True, "service": self.service_name, "path": self.path}
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *_args):
        pass


class GraphHealthHandler(HealthServiceHandler):
    service_name = "graph"


class MemoryHealthHandler(HealthServiceHandler):
    service_name = "memory"


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


def stop(server, thread):
    server.shutdown()
    server.server_close()
    thread.join(2)


def test_gateway_forwards_method_path_query_body_and_health(tmp_path, monkeypatch):
    monkeypatch.setenv("ENSEMBLE_METRICS_FILE", str(tmp_path / "metrics.jsonl"))
    downstream = ThreadingHTTPServer(("127.0.0.1", 0), StubServiceHandler)
    downstream_thread = start(downstream)
    base = f"http://127.0.0.1:{downstream.server_port}"
    gateway = create_server("127.0.0.1", 0, graph_url=base, memory_url=base)
    gateway_thread = start(gateway)
    try:
        status, health = request(gateway, "GET", "/api/v1/health")
        assert status == 200
        assert health == {"ok": True, "service": "claude-ensemble"}

        status, graph = request(gateway, "GET", "/api/v1/graph/health")
        assert status == 200
        assert graph["method"] == "GET"
        assert graph["path"] == "/health"

        status, memory = request(gateway, "GET", "/api/v1/memory/search?q=needle&limit=3")
        assert status == 200
        assert memory["method"] == "GET"
        assert memory["path"] == "/memory/search?q=needle&limit=3"

        payload = {"name": "node-a"}
        status, created = request(
            gateway, "POST", "/api/v1/graph/nodes?source=test", payload
        )
        assert status == 200
        assert created["method"] == "POST"
        assert created["path"] == "/graph/nodes?source=test"
        assert json.loads(created["body"]) == payload

        status, updated = request(
            gateway, "PUT", "/api/v1/graph/nodes/node-a?version=2", {"enabled": True}
        )
        assert status == 200
        assert updated["method"] == "PUT"
        assert updated["path"] == "/graph/nodes/node-a?version=2"
        assert json.loads(updated["body"]) == {"enabled": True}
    finally:
        stop(gateway, gateway_thread)
        stop(downstream, downstream_thread)


def test_gateway_health_routes_return_correct_service_identity(tmp_path, monkeypatch):
    monkeypatch.setenv("ENSEMBLE_METRICS_FILE", str(tmp_path / "metrics.jsonl"))
    graph = ThreadingHTTPServer(("127.0.0.1", 0), GraphHealthHandler)
    memory = ThreadingHTTPServer(("127.0.0.1", 0), MemoryHealthHandler)
    graph_thread = start(graph)
    memory_thread = start(memory)
    gateway = create_server(
        "127.0.0.1",
        0,
        graph_url=f"http://127.0.0.1:{graph.server_port}",
        memory_url=f"http://127.0.0.1:{memory.server_port}",
    )
    gateway_thread = start(gateway)
    try:
        status, graph_health = request(gateway, "GET", "/api/v1/graph/health")
        assert status == 200
        assert graph_health == {"ok": True, "service": "graph", "path": "/health"}

        status, memory_health = request(gateway, "GET", "/api/v1/memory/health")
        assert status == 200
        assert memory_health == {"ok": True, "service": "memory", "path": "/health"}
    finally:
        stop(gateway, gateway_thread)
        stop(graph, graph_thread)
        stop(memory, memory_thread)


def test_nested_gateways_preserve_graph_and_memory_health_identity(tmp_path, monkeypatch):
    monkeypatch.setenv("ENSEMBLE_METRICS_FILE", str(tmp_path / "metrics.jsonl"))
    graph = ThreadingHTTPServer(("127.0.0.1", 0), GraphHealthHandler)
    memory = ThreadingHTTPServer(("127.0.0.1", 0), MemoryHealthHandler)
    graph_thread = start(graph)
    memory_thread = start(memory)
    graph_url = f"http://127.0.0.1:{graph.server_port}"
    memory_url = f"http://127.0.0.1:{memory.server_port}"
    inner = create_server("127.0.0.1", 0, graph_url=graph_url, memory_url=memory_url)
    inner_thread = start(inner)
    inner_url = f"http://127.0.0.1:{inner.server_port}"
    outer = create_server("127.0.0.1", 0, graph_url=inner_url, memory_url=inner_url)
    outer_thread = start(outer)
    try:
        status, graph_health = request(outer, "GET", "/api/v1/graph/health")
        assert status == 200
        assert graph_health == {"ok": True, "service": "graph", "path": "/health"}

        status, memory_health = request(outer, "GET", "/api/v1/memory/health")
        assert status == 200
        assert memory_health == {"ok": True, "service": "memory", "path": "/health"}
    finally:
        stop(outer, outer_thread)
        stop(inner, inner_thread)
        stop(graph, graph_thread)
        stop(memory, memory_thread)


def test_gateway_preserves_downstream_http_status_and_error_payload(tmp_path, monkeypatch):
    monkeypatch.setenv("ENSEMBLE_METRICS_FILE", str(tmp_path / "metrics.jsonl"))
    downstream = ThreadingHTTPServer(("127.0.0.1", 0), StubServiceHandler)
    downstream_thread = start(downstream)
    gateway = create_server(
        "127.0.0.1", 0, graph_url=f"http://127.0.0.1:{downstream.server_port}"
    )
    gateway_thread = start(gateway)
    try:
        status, payload = request(gateway, "GET", "/api/v1/graph/failure?detail=full")
        assert status == 418
        assert payload == {
            "ok": False,
            "method": "GET",
            "path": "/graph/failure?detail=full",
            "body": "",
            "error": "upstream teapot",
        }
    finally:
        stop(gateway, gateway_thread)
        stop(downstream, downstream_thread)


def test_gateway_forwards_through_nested_ensemble_gateway(tmp_path, monkeypatch):
    monkeypatch.setenv("ENSEMBLE_METRICS_FILE", str(tmp_path / "metrics.jsonl"))
    downstream = ThreadingHTTPServer(("127.0.0.1", 0), StubServiceHandler)
    downstream_thread = start(downstream)
    concrete_url = f"http://127.0.0.1:{downstream.server_port}"
    inner = create_server("127.0.0.1", 0, graph_url=concrete_url)
    inner_thread = start(inner)
    outer = create_server(
        "127.0.0.1", 0, graph_url=f"http://127.0.0.1:{inner.server_port}"
    )
    outer_thread = start(outer)
    try:
        payload = {"name": "nested-node"}
        status, result = request(
            outer, "POST", "/api/v1/graph/nodes?origin=outer", payload
        )
        assert status == 200
        assert result["method"] == "POST"
        assert result["path"] == "/graph/nodes?origin=outer"
        assert json.loads(result["body"]) == payload
    finally:
        stop(outer, outer_thread)
        stop(inner, inner_thread)
        stop(downstream, downstream_thread)


def test_gateway_uses_service_url_environment_configuration(tmp_path, monkeypatch):
    monkeypatch.setenv("ENSEMBLE_METRICS_FILE", str(tmp_path / "metrics.jsonl"))
    downstream = ThreadingHTTPServer(("127.0.0.1", 0), StubServiceHandler)
    downstream_thread = start(downstream)
    monkeypatch.setenv(
        "ENSEMBLE_GRAPH_URL", f"http://127.0.0.1:{downstream.server_port}"
    )
    gateway = create_server("127.0.0.1", 0)
    gateway_thread = start(gateway)
    try:
        status, result = request(gateway, "GET", "/api/v1/graph/health")
        assert status == 200
        assert result["path"] == "/health"
    finally:
        stop(gateway, gateway_thread)
        stop(downstream, downstream_thread)


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
        stop(gateway, thread)


def test_gateway_forwards_delete_method_path_query_and_response(tmp_path, monkeypatch):
    monkeypatch.setenv("ENSEMBLE_METRICS_FILE", str(tmp_path / "metrics.jsonl"))
    downstream = ThreadingHTTPServer(("127.0.0.1", 0), StubServiceHandler)
    downstream_thread = start(downstream)
    gateway = create_server(
        "127.0.0.1", 0, graph_url=f"http://127.0.0.1:{downstream.server_port}"
    )
    gateway_thread = start(gateway)
    try:
        status, result = request(
            gateway, "DELETE", "/api/v1/graph/nodes/node-a?force=true&version=3"
        )
        assert status == 200
        assert result == {
            "ok": True,
            "method": "DELETE",
            "path": "/graph/nodes/node-a?force=true&version=3",
            "body": "",
        }
    finally:
        stop(gateway, gateway_thread)
        stop(downstream, downstream_thread)


def test_nested_gateway_preserves_downstream_http_error(tmp_path, monkeypatch):
    monkeypatch.setenv("ENSEMBLE_METRICS_FILE", str(tmp_path / "metrics.jsonl"))
    downstream = ThreadingHTTPServer(("127.0.0.1", 0), StubServiceHandler)
    downstream_thread = start(downstream)
    concrete_url = f"http://127.0.0.1:{downstream.server_port}"
    inner = create_server("127.0.0.1", 0, graph_url=concrete_url)
    inner_thread = start(inner)
    outer = create_server(
        "127.0.0.1", 0, graph_url=f"http://127.0.0.1:{inner.server_port}"
    )
    outer_thread = start(outer)
    try:
        status, result = request(
            outer, "GET", "/api/v1/graph/failure?detail=full"
        )
        assert status == 418
        assert result == {
            "ok": False,
            "method": "GET",
            "path": "/graph/failure?detail=full",
            "body": "",
            "error": "upstream teapot",
        }
    finally:
        stop(outer, outer_thread)
        stop(inner, inner_thread)
        stop(downstream, downstream_thread)
