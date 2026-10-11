"""Security and policy tests for the REST collaboration boundary."""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request

from server.ensemble_server import create_server


def request(server, payload, token=None):
    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        f"http://127.0.0.1:{server.server_port}/api/v1/collaboration/run",
        data=data,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    if token is not None:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=3) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read())


def run_request(monkeypatch, tmp_path, payload, token=None):
    monkeypatch.setenv("ENSEMBLE_METRICS_FILE", str(tmp_path / "metrics.jsonl"))
    monkeypatch.setenv("ENSEMBLE_EXPERIMENT_FILE", str(tmp_path / "experiments.jsonl"))
    server = create_server("127.0.0.1", 0, graph_url="", memory_url="")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        return request(server, payload, token)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(2)


def test_collaboration_is_disabled_without_server_token(monkeypatch, tmp_path):
    monkeypatch.delenv("ENSEMBLE_COLLABORATION_AUTH_TOKEN", raising=False)
    status, body = run_request(monkeypatch, tmp_path, {"task": "test task"})
    assert status == 503
    assert "disabled" in body["error"]


def test_collaboration_rejects_missing_bearer_token(monkeypatch, tmp_path):
    monkeypatch.setenv("ENSEMBLE_COLLABORATION_AUTH_TOKEN", "test-secret")
    status, body = run_request(monkeypatch, tmp_path, {"task": "test task"})
    assert status == 401
    assert body["error_code"] == "unauthorized"


def test_collaboration_blocks_external_reviewers_without_opt_in(monkeypatch, tmp_path):
    monkeypatch.setenv("ENSEMBLE_COLLABORATION_AUTH_TOKEN", "test-secret")
    monkeypatch.setenv("ENSEMBLE_COLLABORATION_REVIEWERS", "grok")
    monkeypatch.delenv("ENSEMBLE_COLLABORATION_ALLOW_EXTERNAL_DATA", raising=False)
    status, body = run_request(monkeypatch, tmp_path, {"task": "test task"}, token="test-secret")
    assert status == 403
    assert body["error_code"] == "forbidden"
    assert "external reviewers are disabled" in body["error"]


def test_collaboration_rejects_empty_task_before_provider_dispatch(monkeypatch, tmp_path):
    monkeypatch.setenv("ENSEMBLE_COLLABORATION_AUTH_TOKEN", "test-secret")
    monkeypatch.setenv("ENSEMBLE_COLLABORATION_ALLOW_EXTERNAL_DATA", "true")
    status, body = run_request(
        monkeypatch,
        tmp_path,
        {"task": " ", "solvers": ["claude-sonnet"], "reviewers": []},
        token="test-secret",
    )
    assert status == 400
    assert body["error_code"] == "invalid_request"
    assert body["error"] == "task is required"
