"""Direct Anthropic Messages API provider."""

from __future__ import annotations

import os
import time
from typing import Any

from .credentials import CredentialPool
from .http import ProviderHTTPError, post_json
from .model_provider import ModelProvider, ModelRequest, ModelResponse


class AnthropicProvider(ModelProvider):
    """Invoke Anthropic models through the public Messages API."""

    name = "anthropic"
    default_model = "claude-sonnet-4-5"
    api_url = "https://api.anthropic.com/v1/messages"

    def __init__(self, api_key: str | None = None, *, default_model: str | None = None, credentials: CredentialPool | None = None):
        self.api_key = api_key
        self.credentials = credentials
        if api_key is None and credentials is None:
            self.api_key = os.environ.get("ANTHROPIC_API_KEY")
        self.default_model = default_model or self.default_model

    def generate(self, request: ModelRequest) -> ModelResponse:
        requested = (request.metadata or {}).get("credential")
        credential = None
        if self.credentials is not None:
            credential = self.credentials.select(self.name, requested)
            api_key = credential.api_key
            credential_name = credential.name
        else:
            api_key = self.api_key
            credential_name = None
        if not api_key:
            raise RuntimeError("ANTHROPIC_API_KEY is not configured")

        model = request.model or self.default_model
        payload: dict[str, Any] = {
            "model": model,
            "max_tokens": request.max_tokens,
            "temperature": request.temperature,
            "messages": [
                {"role": m["role"], "content": m["content"]}
                for m in request.conversation()
            ],
        }
        if request.system_prompt:
            payload["system"] = request.system_prompt

        started = time.monotonic()
        try:
            data, headers = post_json(
                provider=self.name,
                url=self.api_url,
                payload=payload,
                headers={
                    "x-api-key": api_key,
                    "anthropic-version": "2023-06-01",
                },
                timeout=request.timeout,
            )
        except ProviderHTTPError as exc:
            if (
                exc.status in {401, 403}
                and self.credentials is not None
                and requested is None
                and credential is not None
            ):
                self.credentials.mark_failed(self.name, credential_name)
            raise
        latency_ms = (time.monotonic() - started) * 1000

        content = data.get("content")
        if not isinstance(content, list):
            self._raise_api_error(data)
        text = "".join(
            block.get("text", "")
            for block in content
            if isinstance(block, dict) and block.get("type") == "text"
        )
        usage = data.get("usage") or {}
        return ModelResponse(
            provider=self.name,
            model=str(data.get("model") or model),
            text=text,
            input_tokens=self._count(usage.get("input_tokens")),
            output_tokens=self._count(usage.get("output_tokens")),
            cache_read_tokens=self._count(usage.get("cache_read_input_tokens")),
            cache_creation_tokens=self._count(usage.get("cache_creation_input_tokens")),
            request_id=self._header(headers, "request-id"),
            latency_ms=latency_ms,
            raw_metadata=data,
        )

    @staticmethod
    def _count(value: Any) -> int:
        if value is None:
            return 0
        if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
            raise RuntimeError(f"Anthropic returned invalid token count: {value!r}")
        return int(value)

    @staticmethod
    def _header(headers: dict[str, str], name: str) -> str | None:
        wanted = name.lower()
        return next((value for key, value in headers.items() if key.lower() == wanted), None)

    @staticmethod
    def _raise_api_error(data: dict[str, Any]) -> None:
        error = data.get("error") or data
        raise RuntimeError(f"Anthropic API returned an invalid response: {error}")
