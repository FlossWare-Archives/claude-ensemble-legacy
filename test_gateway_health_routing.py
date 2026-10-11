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
