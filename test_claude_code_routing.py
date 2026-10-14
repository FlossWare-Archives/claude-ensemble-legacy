"""Tests for opt-in Claude Code routing and upstream provider checks."""

import json

import pytest

from providers.claude_code import ClaudeCodeProvider
from providers.model_provider import ModelRequest
from providers.registry import ProviderRegistry


class FakeProcess:
    returncode = 0

    def __init__(self, payload):
        self.payload = payload

    def communicate(self, input=None, timeout=None):
        return json.dumps(self.payload), ""


def make_payload(upstream="anthropic"):
    return {
        "is_error": False,
        "subtype": "success",
        "result": "ok",
        "usage": {"input_tokens": 4, "output_tokens": 3},
        "modelUsage": {
            "model-x": {
                "provider": upstream,
                "inputTokens": 4,
                "outputTokens": 3,
            }
        },
    }


def test_registry_routes_only_configured_models(monkeypatch):
    monkeypatch.setenv("ENSEMBLE_CLAUDE_CODE_MODELS", "haiku,sonnet")
    registry = ProviderRegistry()

    assert registry.resolve_model("haiku") == ("claude-code", "haiku")
    assert registry.resolve_model("opus")[0] == "anthropic"


def test_expected_provider_is_accepted(monkeypatch):
    monkeypatch.setattr("providers.claude_code.shutil.which", lambda _: "/fake/claude")
    provider = ClaudeCodeProvider(expected_provider="anthropic")
    monkeypatch.setattr(provider, "_verify_upstream_provider_before_invocation", lambda _: None)
    monkeypatch.setattr(provider, "_start_process", lambda _: FakeProcess(make_payload()))

    assert provider.generate(ModelRequest(prompt="test")).text == "ok"


def test_unexpected_provider_is_rejected(monkeypatch):
    monkeypatch.setattr("providers.claude_code.shutil.which", lambda _: "/fake/claude")
    provider = ClaudeCodeProvider(expected_provider="anthropic")
    monkeypatch.setattr(provider, "_verify_upstream_provider_before_invocation", lambda _: None)
    monkeypatch.setattr(
        provider, "_start_process", lambda _: FakeProcess(make_payload("vertex"))
    )

    with pytest.raises(RuntimeError, match="expected 'anthropic'"):
        provider.generate(ModelRequest(prompt="test"))


def test_missing_provider_metadata_is_rejected(monkeypatch):
    monkeypatch.setattr("providers.claude_code.shutil.which", lambda _: "/fake/claude")
    data = make_payload()
    data["modelUsage"]["model-x"].pop("provider")
    provider = ClaudeCodeProvider(expected_provider="anthropic")
    monkeypatch.setattr(provider, "_verify_upstream_provider_before_invocation", lambda _: None)
    monkeypatch.setattr(provider, "_start_process", lambda _: FakeProcess(data))

    with pytest.raises(RuntimeError, match="did not consistently report its upstream provider"):
        provider.generate(ModelRequest(prompt="test"))


def test_blank_expected_provider_is_rejected(monkeypatch):
    monkeypatch.setenv("ENSEMBLE_CLAUDE_CODE_EXPECTED_PROVIDER", "")
    with pytest.raises(ValueError, match="non-empty"):
        ClaudeCodeProvider()


def test_preflight_rejects_vertex_before_invocation(monkeypatch):
    monkeypatch.setattr("providers.claude_code.shutil.which", lambda _: "/fake/claude")
    provider = ClaudeCodeProvider(expected_provider="anthropic")

    class Status:
        returncode = 0
        stdout = json.dumps({"loggedIn": True, "apiProvider": "vertex"})
        stderr = ""

    monkeypatch.setattr("providers.claude_code.subprocess.run", lambda *args, **kwargs: Status())
    monkeypatch.setattr(
        provider,
        "_start_process",
        lambda _: pytest.fail("prompt invocation must not happen after a failed preflight"),
    )
    with pytest.raises(RuntimeError, match="refusing to send the prompt"):
        provider.generate(ModelRequest(prompt="sensitive test"))


def test_mixed_model_upstream_metadata_is_rejected():
    payload = make_payload("anthropic")
    payload["modelUsage"]["second-model"] = {
        "provider": "vertex",
        "inputTokens": 1,
        "outputTokens": 1,
    }
    assert ClaudeCodeProvider._reported_upstream_provider(payload, "model-x") is None
