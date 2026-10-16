"""Tests for the single Claude Code runtime and compatibility client."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from arbitration.api_client import MultiModelClient
from providers.model_provider import ModelResponse
from providers.registry import ProviderRegistry


def test_multi_model_client_uses_claude_code_runtime_for_claude_model() -> None:
    provider = MagicMock()
    provider.generate.return_value = ModelResponse(
        provider="claude-code", model="sonnet", text="hello",
        input_tokens=3, output_tokens=2,
    )
    registry = ProviderRegistry({"claude-code": provider})

    response = MultiModelClient(registry=registry).call_model_response(
        model="claude-sonnet-test", prompt="hello"
    )

    assert response.text == "hello"
    provider.generate.assert_called_once()
    assert provider.generate.call_args.args[0].model == "claude-sonnet-test"


@pytest.mark.parametrize(
    "model",
    ["gemini", "gemini-2.5-flash", "gpt-4", "cursor", "ollama-local"],
)
def test_unsupported_provider_labels_are_rejected(model: str) -> None:
    registry = ProviderRegistry()
    with pytest.raises(ValueError, match="Unsupported model label"):
        registry.resolve_model(model)


@pytest.mark.parametrize(
    ("model", "expected"),
    [
        ("haiku", "haiku"),
        ("sonnet", "sonnet"),
        ("opus", "opus"),
        ("claude-sonnet-test", "claude-sonnet-test"),
        ("claude-opus-4-1", "claude-opus-4-1"),
    ],
)
def test_claude_model_names_are_preserved(model: str, expected: str) -> None:
    registry = ProviderRegistry()
    assert registry.resolve_model(model) == ("claude-code", expected)


@pytest.mark.parametrize("model", ["sonett", "gpt-999", "gemini-typo", "unknown-model"])
def test_unknown_model_labels_are_rejected(model: str) -> None:
    registry = ProviderRegistry()
    with pytest.raises(ValueError, match="Unsupported model label"):
        registry.resolve_model(model)


@pytest.mark.parametrize("model", ["", "   "])
def test_blank_model_labels_are_rejected(model: str) -> None:
    registry = ProviderRegistry()
    with pytest.raises(ValueError, match="non-empty string"):
        registry.resolve_model(model)


def test_only_claude_code_runtime_can_be_injected() -> None:
    provider = MagicMock()
    assert ProviderRegistry({"claude-code": provider}).resolve("sonnet") is provider
    with pytest.raises(ValueError, match="Only the 'claude-code' runtime"):
        ProviderRegistry({"google": MagicMock()})


def test_register_rejects_additional_runtimes() -> None:
    registry = ProviderRegistry()
    with pytest.raises(ValueError, match="Only the 'claude-code' runtime"):
        registry.register("google", MagicMock())


def test_string_api_remains_backward_compatible() -> None:
    provider = MagicMock()
    provider.generate.return_value = ModelResponse(
        provider="claude-code", model="sonnet", text="hello"
    )
    registry = ProviderRegistry({"claude-code": provider})

    assert MultiModelClient(registry=registry).call_model("sonnet", "hello") == "hello"
    assert provider.generate.call_args.args[0].model == "sonnet"


def test_registry_does_not_eagerly_construct_default_provider() -> None:
    registry = ProviderRegistry()
    assert registry.providers == {}


def test_registry_defaults_expected_upstream_to_vertex(monkeypatch) -> None:
    monkeypatch.delenv("ENSEMBLE_CLAUDE_CODE_EXPECTED_PROVIDER", raising=False)
    registry = ProviderRegistry()
    assert registry._configured_provider == "vertex"


def test_credential_status_does_not_expose_api_key_accounts() -> None:
    registry = ProviderRegistry()
    assert registry.credential_status("anthropic") == []
    assert registry.credential_status("google") == []
