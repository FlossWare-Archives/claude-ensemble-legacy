"""Focused unit tests for the explicit Google Gemini adapter."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from providers.google import GoogleProvider
from providers.http_client import ProviderHTTPError
from providers.model_provider import ModelRequest


def test_google_normalizes_response_usage_and_headers():
    payload = {
        "candidates": [{"content": {"parts": [{"text": "review summary"}]}}],
        "usageMetadata": {"promptTokenCount": 12, "candidatesTokenCount": 8},
    }
    with patch(
        "providers.google.post_json",
        return_value=(payload, {"x-request-id": "req-google-1"}),
    ) as post:
        response = GoogleProvider(api_key="test-key").generate(
            ModelRequest("review this", model="gemini-2.5-flash")
        )

    assert response.provider == "google"
    assert response.model == "gemini-2.5-flash"
    assert response.text == "review summary"
    assert response.input_tokens == 12
    assert response.output_tokens == 8
    assert response.request_id == "req-google-1"
    assert response.latency_ms is not None
    assert post.call_args.kwargs["headers"] == {"x-goog-api-key": "test-key"}


def test_google_preserves_system_prompt_and_conversation():
    payload = {"candidates": [{"content": {"parts": [{"text": "ok"}]}}]}
    request = ModelRequest(
        "continue",
        model="gemini-2.5-flash",
        system_prompt="You are a reviewer",
        messages=({"role": "user", "content": "Review this diff"},),
    )
    with patch("providers.google.post_json", return_value=(payload, {})) as post:
        GoogleProvider(api_key="test-key").generate(request)

    body = post.call_args.kwargs["payload"]
    assert body["systemInstruction"]["parts"][0]["text"] == "You are a reviewer"
    assert body["contents"] == [
        {"role": "user", "parts": [{"text": "Review this diff"}]},
        {"role": "user", "parts": [{"text": "continue"}]},
    ]


def test_google_missing_key_fails_before_network():
    with pytest.raises(RuntimeError, match="GOOGLE_API_KEY"):
        GoogleProvider(api_key="").generate(ModelRequest("hello"))


def test_google_http_errors_are_not_silently_retried():
    with patch(
        "providers.google.post_json",
        side_effect=ProviderHTTPError("google", 429, "rate limited"),
    ):
        with pytest.raises(ProviderHTTPError, match="HTTP 429"):
            GoogleProvider(api_key="test-key").generate(
                ModelRequest("hello", model="gemini-2.5-flash")
            )


def test_google_rejects_invalid_token_counts():
    payload = {
        "candidates": [{"content": {"parts": [{"text": "ok"}]}}],
        "usageMetadata": {"promptTokenCount": True, "candidatesTokenCount": 1},
    }
    with patch("providers.google.post_json", return_value=(payload, {})):
        with pytest.raises(RuntimeError, match="invalid token count"):
            GoogleProvider(api_key="test-key").generate(ModelRequest("hello"))
