"""Tests for explicit provider routing and the compatibility client."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from arbitration.api_client import MultiModelClient
from providers.model_provider import ModelResponse
from providers.registry import ProviderRegistry


@pytest.mark.parametrize(("model", "expected"), [
    ("haiku", ("claude-code", "haiku")),
    ("sonnet", ("claude-code", "sonnet")),
    ("opus", ("claude-code", "opus")),
    ("claude-sonnet-test", ("claude-code", "claude-sonnet-test")),
])
def test_claude_models_use_claude_code(model, expected):
    registry = ProviderRegistry()
    assert registry.resolve_model(model) == expected


def test_gemini_alias_is_blocked_by_default(monkeypatch):
    monkeypatch.delenv("ENSEMBLE_ALLOW_GOOGLE_MODEL_API", raising=False)
    with pytest.raises(ValueError, match="Google model routing is disabled"):
        ProviderRegistry().resolve_model("gemini")


@pytest.mark.parametrize(("model", "expected"), [
    ("gemini", "gemini-2.5-flash"),
    ("gemini-2.5-flash", "gemini-2.5-flash"),
    ("models/gemini-2.5-flash", "gemini-2.5-flash"),
])
def test_gemini_routes_to_google_only_after_explicit_opt_in(monkeypatch, model, expected):
    monkeypatch.setenv("ENSEMBLE_ALLOW_GOOGLE_MODEL_API", "true")
    registry = ProviderRegistry({"google": MagicMock()})
    assert registry.resolve_model(model) == ("google", expected)


@pytest.mark.parametrize("model", ["gpt-4", "cursor", "ollama-local", "unknown-model"])
def test_unsupported_models_fail_without_fallback(model, monkeypatch):
    monkeypatch.setenv("ENSEMBLE_ALLOW_GOOGLE_MODEL_API", "true")
    with pytest.raises(ValueError, match="Unsupported model label"):
        ProviderRegistry().resolve_model(model)


def test_multi_model_client_uses_selected_provider(monkeypatch):
    monkeypatch.setenv("ENSEMBLE_ALLOW_GOOGLE_MODEL_API", "true")
    provider = MagicMock()
    provider.generate.return_value = ModelResponse(
        provider="google", model="gemini-2.5-flash", text="hello"
    )
    registry = ProviderRegistry({"google": provider})
    response = MultiModelClient(registry=registry).call_model_response(
        model="gemini", prompt="hello"
    )
    assert response.text == "hello"
    provider.generate.assert_called_once()
    assert provider.generate.call_args.args[0].model == "gemini-2.5-flash"


def test_claude_runtime_does_not_eagerly_construct_provider():
    registry = ProviderRegistry()
    assert registry.providers == {}


def test_only_explicitly_supported_providers_can_be_registered():
    with pytest.raises(ValueError, match="Unsupported provider"):
        ProviderRegistry({"anthropic": MagicMock()})


def test_credential_status_never_exposes_secrets(monkeypatch):
    monkeypatch.setenv("ENSEMBLE_ALLOW_GOOGLE_MODEL_API", "true")
    monkeypatch.setenv("GOOGLE_API_KEY", "must-not-leak")
    status = ProviderRegistry().credential_status("google")
    assert status
    assert "must-not-leak" not in repr(status)
