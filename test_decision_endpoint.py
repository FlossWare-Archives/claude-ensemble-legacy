"""REST dispatch and dependency-failure tests for Decision Support endpoints."""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request

from server.ensemble_server import create_server


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


def start_server(tmp_path, graph_url=""):
    return create_server(
        "127.0.0.1",
        0,
        graph_url=graph_url,
        memory_url="",
    )


def test_decision_recommend_requires_task_type(tmp_path, monkeypatch):
    monkeypatch.setenv("ENSEMBLE_METRICS_FILE", str(tmp_path / "metrics.jsonl"))
    server = start_server(tmp_path)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        status, body = request(server, "POST", "/api/v1/decision/recommend", {})
        assert status == 400
        assert body["error_code"] == "invalid_request"
        assert "task_type" in body["error"]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(2)


def test_decision_graph_route_maps_unavailable_dependency(tmp_path, monkeypatch):
    monkeypatch.setenv("ENSEMBLE_METRICS_FILE", str(tmp_path / "metrics.jsonl"))
    server = start_server(tmp_path, graph_url="http://127.0.0.1:1")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        status, body = request(server, "POST", "/api/v1/decision/query/graph", {"start": "node-a"})
        assert status == 503
        assert body["error_code"] == "dependency_unavailable"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(2)


def test_decision_recommend_rejects_get_method(tmp_path, monkeypatch):
    monkeypatch.setenv("ENSEMBLE_METRICS_FILE", str(tmp_path / "metrics.jsonl"))
    server = start_server(tmp_path)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        status, body = request(server, "GET", "/api/v1/decision/recommend")
        assert status == 405
        assert body["error"] == "method not allowed"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(2)
