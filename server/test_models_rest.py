"""Tests for the canonical REST model boundary."""

from __future__ import annotations

import json
import os
import threading
import urllib.error
import urllib.request


def test_models_endpoint_exposes_credential_status(monkeypatch) -> None:
    for name in tuple(os.environ):
        if name == "ANTHROPIC_API_KEY" or name.startswith("ANTHROPIC_API_KEY_"):
            monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY_ACCOUNT_A", "secret-a")
    from server.ensemble_server import create_server

    server = create_server("127.0.0.1", 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with urllib.request.urlopen(
            f"http://127.0.0.1:{server.server_port}/api/v1/models/credentials",
            timeout=3,
        ) as response:
            payload = json.loads(response.read())
        assert payload["ok"]
        assert payload["credentials"]["anthropic"][0]["name"] == "account-a"
        assert "api_key" not in json.dumps(payload)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(2)


def _request(server, method: str, path: str, payload=None):
    data = None if payload is None else json.dumps(payload).encode()
    request = urllib.request.Request(
        f"http://127.0.0.1:{server.server_port}{path}",
        data=data,
        method=method,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=3) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read())


def _start(server):
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return thread


def test_models_list_endpoint_returns_public_model_names_without_secrets(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("ENSEMBLE_METRICS_FILE", str(tmp_path / "metrics.jsonl"))
    from server.ensemble_server import create_server

    server = create_server("127.0.0.1", 0)
    thread = _start(server)
    try:
        status, payload = _request(server, "GET", "/api/v1/models/")
        assert status == 200
        assert payload["ok"] is True
        assert payload["models"] == ["haiku", "sonnet", "opus", "gemini-2.5-flash"]
        assert "api_key" not in json.dumps(payload)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(2)


def test_models_invoke_validates_required_fields_without_provider_call(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("ENSEMBLE_METRICS_FILE", str(tmp_path / "metrics.jsonl"))
    from server.ensemble_server import create_server

    server = create_server("127.0.0.1", 0)
    thread = _start(server)
    try:
        status, payload = _request(server, "POST", "/api/v1/models/invoke", {})
        assert status == 400
        assert payload["error_code"] == "invalid_request"
        assert payload["error"] == "model is required"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(2)


def test_models_invoke_returns_provider_response_contract(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("ENSEMBLE_METRICS_FILE", str(tmp_path / "metrics.jsonl"))
    from providers import ModelResponse
    from server.ensemble_server import create_server

    server = create_server("127.0.0.1", 0)
    thread = _start(server)
    response = ModelResponse(
        provider="fake",
        model="fake-model",
        text="test answer",
        input_tokens=3,
        output_tokens=4,
        request_id="request-123",
        latency_ms=1.25,
        cost_usd=0.001,
    )
    monkeypatch.setattr(server.application.models, "call_model_response", lambda **_kwargs: response)
    try:
        status, payload = _request(
            server,
            "POST",
            "/api/v1/models/invoke",
            {"model": "fake-model", "prompt": "hello"},
        )
        assert status == 200
        assert payload == {
            "ok": True,
            "provider": "fake",
            "model": "fake-model",
            "text": "test answer",
            "input_tokens": 3,
            "output_tokens": 4,
            "request_id": "request-123",
            "latency_ms": 1.25,
            "cost_usd": 0.001,
        }
    finally:
        server.shutdown()
        server.server_close()
        thread.join(2)
