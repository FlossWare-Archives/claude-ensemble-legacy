"""REST tests for the operational metrics stream."""
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
    )
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=3) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read())


def test_metrics_endpoint_and_aggregate(tmp_path, monkeypatch):
    monkeypatch.setenv("ENSEMBLE_METRICS_FILE", str(tmp_path / "metrics.jsonl"))
    server = create_server("127.0.0.1", 0, graph_url="", memory_url="")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        metric = {
            "execution_id": "rest-1",
            "timestamp": "2026-10-04T00:00:00+00:00",
            "service": "model",
            "provider": "fake",
            "model": "test-model",
            "status": "success",
            "input_tokens": 3,
            "output_tokens": 2,
            "total_tokens": 5,
            "estimated_cost": 0.004,
        }
        status, body = request(server, "POST", "/api/v1/metrics", metric)
        assert status == 201
        assert body["metric"]["execution_id"] == "rest-1"

        status, body = request(server, "GET", "/api/v1/metrics")
        assert status == 200
        assert len(body["metrics"]) == 1

        status, body = request(server, "GET", "/api/v1/metrics/aggregate")
        assert status == 200
        assert body["aggregate"]["records"] == 1
        assert body["aggregate"]["by_model"] == {"test-model": 1}
    finally:
        server.shutdown()
        server.server_close()
        thread.join(2)

def test_metrics_listing_is_bounded_and_paginated(tmp_path, monkeypatch):
    from operational_metrics import MetricsRecord

    monkeypatch.setenv("ENSEMBLE_METRICS_FILE", str(tmp_path / "metrics.jsonl"))
    server = create_server("127.0.0.1", 0, graph_url="", memory_url="")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        for index in range(505):
            server.application.metrics.record(
                MetricsRecord(execution_id=f"page-{index}", timestamp="now", service="test")
            )
        status, first = request(server, "GET", "/api/v1/metrics")
        assert status == 200
        assert len(first["metrics"]) == 100
        assert first["offset"] == 0
        assert first["limit"] == 100
        assert first["next_offset"] == 100

        status, second = request(server, "GET", "/api/v1/metrics?limit=500&offset=100")
        assert status == 200
        assert len(second["metrics"]) == 405
        assert second["next_offset"] is None
        assert second["metrics"][0]["execution_id"] == "page-100"

        status, capped = request(server, "GET", "/api/v1/metrics?limit=501")
        assert status == 400
        assert capped["error_code"] == "invalid_request"
        status, invalid = request(server, "GET", "/api/v1/metrics?limit=ten")
        assert status == 400
        status, invalid_offset = request(server, "GET", "/api/v1/metrics?offset=-1")
        assert status == 400
        status, duplicate = request(server, "GET", "/api/v1/metrics?limit=2&limit=3")
        assert status == 400
    finally:
        server.shutdown()
        server.server_close()
        thread.join(2)
