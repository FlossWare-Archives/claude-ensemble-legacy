"""Regression tests for service identity and nested gateway health routing."""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from server.ensemble_server import create_server


def service_handler(service_name, observed_paths):
    class ServiceHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            observed_paths.append(self.path)
            payload = json.dumps(
                {"ok": True, "service": service_name, "path": self.path}
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *_args):
            pass

    return ServiceHandler


def start(server):
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return thread


def stop(server, thread):
    server.shutdown()
    server.server_close()
    thread.join(2)


def get_json(server, path):
    request = urllib.request.Request(
        f"http://127.0.0.1:{server.server_port}{path}", method="GET"
    )
    try:
        with urllib.request.urlopen(request, timeout=3) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read())


def test_concrete_graph_and_memory_health_return_their_own_identity(tmp_path, monkeypatch):
    monkeypatch.setenv("ENSEMBLE_METRICS_FILE", str(tmp_path / "metrics.jsonl"))
    graph_paths = []
    memory_paths = []
    graph = ThreadingHTTPServer(
        ("127.0.0.1", 0), service_handler("graph", graph_paths)
    )
    memory = ThreadingHTTPServer(
        ("127.0.0.1", 0), service_handler("memory", memory_paths)
    )
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
        graph_status, graph_body = get_json(gateway, "/api/v1/graph/health")
        memory_status, memory_body = get_json(gateway, "/api/v1/memory/health")

        assert graph_status == 200
        assert graph_body == {"ok": True, "service": "graph", "path": "/health"}
        assert memory_status == 200
        assert memory_body == {"ok": True, "service": "memory", "path": "/health"}
        # The identity and forwarded path are the contract. The target-health
        # probe may be skipped when its result is already cached.
        assert "/health" in graph_paths
        assert "/health" in memory_paths
        assert "/api/v1/graph/health" not in graph_paths
        assert "/api/v1/memory/health" not in memory_paths
    finally:
        stop(gateway, gateway_thread)
        stop(graph, graph_thread)
        stop(memory, memory_thread)


def test_memory_readiness_route_reaches_concrete_ready_endpoint(tmp_path, monkeypatch):
    monkeypatch.setenv("ENSEMBLE_METRICS_FILE", str(tmp_path / "metrics.jsonl"))
    memory_paths = []
    memory = ThreadingHTTPServer(
        ("127.0.0.1", 0), service_handler("memory", memory_paths)
    )
    memory_thread = start(memory)
    gateway = create_server(
        "127.0.0.1", 0, memory_url=f"http://127.0.0.1:{memory.server_port}"
    )
    gateway_thread = start(gateway)
    try:
        status, body = get_json(gateway, "/api/v1/memory/ready")
        assert status == 200
        assert body == {"ok": True, "service": "memory", "path": "/ready"}
        assert "/ready" in memory_paths
        assert "/api/v1/memory/ready" not in memory_paths
    finally:
        stop(gateway, gateway_thread)
        stop(memory, memory_thread)


def test_nested_gateway_health_route_reaches_the_concrete_service(tmp_path, monkeypatch):
    monkeypatch.setenv("ENSEMBLE_METRICS_FILE", str(tmp_path / "metrics.jsonl"))
    graph_paths = []
    graph = ThreadingHTTPServer(
        ("127.0.0.1", 0), service_handler("graph", graph_paths)
    )
    graph_thread = start(graph)
    inner = create_server(
        "127.0.0.1", 0, graph_url=f"http://127.0.0.1:{graph.server_port}"
    )
    inner_thread = start(inner)
    outer = create_server(
        "127.0.0.1", 0, graph_url=f"http://127.0.0.1:{inner.server_port}"
    )
    outer_thread = start(outer)
    try:
        status, body = get_json(outer, "/api/v1/graph/health")
        assert status == 200
        assert body == {"ok": True, "service": "graph", "path": "/health"}
        assert graph_paths.count("/health") == 1
        assert "/api/v1/graph/health" not in graph_paths
    finally:
        stop(outer, outer_thread)
        stop(inner, inner_thread)
        stop(graph, graph_thread)


def test_gateway_liveness_is_distinct_from_downstream_readiness(tmp_path, monkeypatch):
    monkeypatch.setenv("ENSEMBLE_METRICS_FILE", str(tmp_path / "metrics.jsonl"))
    graph_paths = []
    graph = ThreadingHTTPServer(
        ("127.0.0.1", 0), service_handler("graph", graph_paths)
    )
    graph_thread = start(graph)
    gateway = create_server(
        "127.0.0.1",
        0,
        graph_url=f"http://127.0.0.1:{graph.server_port}",
        memory_url="http://127.0.0.1:1",
    )
    gateway_thread = start(gateway)
    try:
        live_status, live_body = get_json(gateway, "/api/v1/health")
        ready_status, ready_body = get_json(gateway, "/api/v1/ready")

        assert live_status == 200
        assert live_body == {"ok": True, "service": "claude-ensemble"}
        assert ready_status == 503
        assert ready_body["ok"] is False
        assert ready_body["ready"] is False
        assert ready_body["dependencies"]["graph"]["ok"] is True
        assert ready_body["dependencies"]["memory"]["ok"] is False
    finally:
        stop(gateway, gateway_thread)
        stop(graph, graph_thread)


def test_gateway_readiness_stays_503_while_memory_is_warming(tmp_path, monkeypatch):
    monkeypatch.setenv("ENSEMBLE_METRICS_FILE", str(tmp_path / "metrics.jsonl"))
    graph_paths = []
    memory_paths = []

    class WarmingMemoryHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            memory_paths.append(self.path)
            if self.path == "/health":
                status, payload = 200, {"ok": True, "service": "memory"}
            elif self.path == "/ready":
                status, payload = 503, {
                    "ok": False,
                    "service": "memory",
                    "ready": False,
                    "search_index": {"state": "warming"},
                }
            else:
                status, payload = 404, {"ok": False}
            raw = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def log_message(self, *_args):
            pass

    graph = ThreadingHTTPServer(
        ("127.0.0.1", 0), service_handler("graph", graph_paths)
    )
    memory = ThreadingHTTPServer(("127.0.0.1", 0), WarmingMemoryHandler)
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
        memory_live_status, memory_live = get_json(
            gateway, "/api/v1/memory/health"
        )
        gateway_ready_status, gateway_ready = get_json(gateway, "/api/v1/ready")

        assert memory_live_status == 200 and memory_live["ok"] is True
        assert gateway_ready_status == 503
        assert gateway_ready["ready"] is False
        assert gateway_ready["dependencies"]["graph"]["ok"] is True
        assert gateway_ready["dependencies"]["memory"]["ok"] is False
        assert gateway_ready["dependencies"]["memory"]["response"]["search_index"]["state"] == "warming"
        assert "/health" in memory_paths
        assert "/ready" in memory_paths
    finally:
        stop(gateway, gateway_thread)
        stop(memory, memory_thread)
        stop(graph, graph_thread)


def test_readiness_rejects_a_cross_wired_dependency_identity(tmp_path, monkeypatch):
    monkeypatch.setenv("ENSEMBLE_METRICS_FILE", str(tmp_path / "metrics.jsonl"))
    graph_paths = []
    graph = ThreadingHTTPServer(
        ("127.0.0.1", 0), service_handler("graph", graph_paths)
    )
    graph_thread = start(graph)
    gateway = create_server(
        "127.0.0.1",
        0,
        graph_url=f"http://127.0.0.1:{graph.server_port}",
        # Deliberately point Memory at the healthy Graph endpoint.
        memory_url=f"http://127.0.0.1:{graph.server_port}",
    )
    gateway_thread = start(gateway)
    try:
        status, body = get_json(gateway, "/api/v1/ready")
        assert status == 503
        assert body["ready"] is False
        assert body["dependencies"]["graph"]["ok"] is True
        assert body["dependencies"]["memory"]["ok"] is False
        assert body["dependencies"]["memory"]["response"]["service"] == "graph"
    finally:
        stop(gateway, gateway_thread)
        stop(graph, graph_thread)
