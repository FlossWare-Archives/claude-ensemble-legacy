"""Direct Google Gemini generateContent API provider."""

from __future__ import annotations

import os
import time
from typing import Any

from .credentials import CredentialPool
from .http import ProviderHTTPError, post_json
from .model_provider import ModelProvider, ModelRequest, ModelResponse


class GoogleProvider(ModelProvider):
    """Invoke Gemini models through Google's REST API."""

    name = "google"
    default_model = "gemini-2.5-flash"
    api_url = "https://generativelanguage.googleapis.com/v1beta/models"

    def __init__(self, api_key: str | None = None, *, default_model: str | None = None, credentials: CredentialPool | None = None):
        self.api_key = api_key
        self.credentials = credentials
        if api_key is None and credentials is None:
            self.api_key = os.environ.get("GOOGLE_API_KEY")
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
            raise RuntimeError("GOOGLE_API_KEY is not configured")

        model = request.model or self.default_model
        model = model.removeprefix("models/")
        conversation = request.conversation()
        contents = [
            {
                "role": "user" if message["role"] == "user" else "model",
                "parts": [{"text": message["content"]}],
            }
            for message in conversation
        ]
        payload: dict[str, Any] = {
            "contents": contents,
            "generationConfig": {
                "temperature": request.temperature,
                "maxOutputTokens": request.max_tokens,
            },
        }
        if request.system_prompt:
            payload["systemInstruction"] = {"parts": [{"text": request.system_prompt}]}

        started = time.monotonic()
        try:
            data, headers = post_json(
                provider=self.name,
                url=f"{self.api_url}/{model}:generateContent",
                payload=payload,
                headers={"x-goog-api-key": api_key},
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

        candidates = data.get("candidates")
        if not isinstance(candidates, list) or not candidates:
            self._raise_api_error(data)
        first = candidates[0]
        content = first.get("content") if isinstance(first, dict) else None
        parts = content.get("parts", []) if isinstance(content, dict) else []
        text = "".join(
            part.get("text", "")
            for part in parts
            if isinstance(part, dict) and isinstance(part.get("text"), str)
        )
        usage = data.get("usageMetadata") or {}
        return ModelResponse(
            provider=self.name,
            model=model,
            text=text,
            input_tokens=self._count(usage.get("promptTokenCount")),
            output_tokens=self._count(usage.get("candidatesTokenCount")),
            request_id=self._header(headers, "x-request-id"),
            latency_ms=latency_ms,
            raw_metadata=data,
        )

    @staticmethod
    def _count(value: Any) -> int:
        if value is None:
            return 0
        if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
            raise RuntimeError(f"Google returned invalid token count: {value!r}")
        return int(value)

    @staticmethod
    def _header(headers: dict[str, str], name: str) -> str | None:
        wanted = name.lower()
        return next((value for key, value in headers.items() if key.lower() == wanted), None)

    @staticmethod
    def _raise_api_error(data: dict[str, Any]) -> None:
        error = data.get("error") or data
        raise RuntimeError(f"Google API returned an invalid response: {error}")
