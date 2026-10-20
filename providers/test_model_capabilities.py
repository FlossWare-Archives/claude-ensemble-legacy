"""Contract tests for provider capabilities and request construction."""

from __future__ import annotations

import pytest

from providers import (
    ClaudeCodeProvider,
    ModelCapabilities,
    ModelCapabilityError,
    ModelRequest,
    build_model_request,
)


def test_model_request_tracks_omitted_and_explicit_default_controls():
    omitted = ModelRequest(prompt="task")
    explicit = ModelRequest(prompt="task", temperature=0.7, max_tokens=2000)

    assert omitted.temperature == 0.7
    assert omitted.max_tokens == 2000
    assert omitted.explicit_fields == frozenset()
    assert explicit.explicit_fields == frozenset({"temperature", "max_tokens"})


def test_capability_builder_accepts_omitted_and_explicit_default_controls():
    provider = ClaudeCodeProvider(expected_provider="vertex")

    omitted = build_model_request(provider, prompt="task")
    explicit = build_model_request(
        provider, prompt="task", temperature=0.7, max_tokens=2000
    )

    assert omitted.temperature == explicit.temperature == 0.7
    assert omitted.max_tokens == explicit.max_tokens == 2000
    assert "temperature" not in omitted.explicit_fields
    assert "temperature" in explicit.explicit_fields


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"temperature": 0.2}, "claude-code does not support temperature overrides"),
        ({"max_tokens": 100}, "claude-code does not support max_tokens overrides"),
        (
            {"messages": ({"role": "user", "content": "prior turn"},)},
            "claude-code does not support prior conversation messages",
        ),
    ],
)
def test_capability_builder_rejects_unsupported_controls(kwargs, message):
    provider = ClaudeCodeProvider(expected_provider="vertex")

    with pytest.raises(ModelCapabilityError, match=message):
        build_model_request(provider, prompt="task", **kwargs)


def test_direct_provider_invocation_uses_same_capability_contract():
    provider = ClaudeCodeProvider(expected_provider="vertex")

    with pytest.raises(ModelCapabilityError, match="temperature overrides"):
        provider.generate(ModelRequest(prompt="task", temperature=0.2))


def test_capabilities_are_provider_discoverable_and_immutable():
    capabilities = ClaudeCodeProvider(expected_provider="vertex").capabilities

    assert isinstance(capabilities, ModelCapabilities)
    assert capabilities.provider == "claude-code"
    assert capabilities.supports_messages is False
    assert capabilities.supports_temperature_override is False
    assert capabilities.supports_max_tokens_override is False
