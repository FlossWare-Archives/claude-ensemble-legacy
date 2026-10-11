"""Opt-in authenticated smoke tests for real Anthropic and Google APIs.

These tests are skipped in ordinary CI. Enable them explicitly with
ENSEMBLE_LIVE_PROVIDER_TESTS=1 on a machine with provider credentials configured.
"""

from __future__ import annotations

import os

import pytest

from providers import AnthropicProvider, GoogleProvider, ModelRequest


def _live_enabled() -> bool:
    return os.environ.get("ENSEMBLE_LIVE_PROVIDER_TESTS") == "1"


@pytest.mark.skipif(not _live_enabled(), reason="live provider calls are opt-in")
def test_live_anthropic_messages_api() -> None:
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        pytest.skip("not run: ANTHROPIC_API_KEY is missing")

    provider = AnthropicProvider(api_key=api_key)
    model = os.environ.get("ENSEMBLE_LIVE_ANTHROPIC_MODEL", provider.default_model)
    response = provider.generate(
        ModelRequest(
            "Reply with exactly READY and no other text.",
            model=model,
            temperature=0,
            max_tokens=8,
            timeout=60,
        )
    )

    assert response.provider == "anthropic"
    assert response.model
    assert response.text.strip() == "READY"
    assert response.input_tokens > 0
    assert response.output_tokens > 0


@pytest.mark.skipif(not _live_enabled(), reason="live provider calls are opt-in")
def test_live_google_generate_content_api() -> None:
    api_key = os.environ.get("GOOGLE_API_KEY")
    if not api_key:
        pytest.skip("not run: GOOGLE_API_KEY is missing")

    provider = GoogleProvider(api_key=api_key)
    model = os.environ.get("ENSEMBLE_LIVE_GOOGLE_MODEL", provider.default_model)
    response = provider.generate(
        ModelRequest(
            "Reply with exactly READY and no other text.",
            model=model,
            temperature=0,
            max_tokens=8,
            timeout=60,
        )
    )

    assert response.provider == "google"
    assert response.model
    assert response.text.strip() == "READY"
    assert response.input_tokens > 0
    assert response.output_tokens > 0
