"""Canonical provider-neutral model invocation contract."""

from __future__ import annotations

import math
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Mapping

_UNSET = object()


@dataclass(frozen=True)
class ModelRequest:
    """A provider-neutral model invocation.

    messages contains prior conversation turns. prompt is always the
    current/final user turn and is appended to those messages.
    """

    prompt: str
    model: str | None = None
    system_prompt: str | None | object = _UNSET
    messages: tuple[Mapping[str, str], ...] | object = _UNSET
    temperature: float | object = _UNSET
    max_tokens: int | object = _UNSET
    timeout: float | object = _UNSET
    metadata: Mapping[str, Any] = field(default_factory=dict)
    explicit_fields: frozenset[str] = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        explicit = frozenset(
            name
            for name, value in (
                ("system_prompt", self.system_prompt),
                ("messages", self.messages),
                ("temperature", self.temperature),
                ("max_tokens", self.max_tokens),
                ("timeout", self.timeout),
            )
            if value is not _UNSET
        )
        object.__setattr__(self, "explicit_fields", explicit)
        if self.system_prompt is _UNSET:
            object.__setattr__(self, "system_prompt", None)
        if self.messages is _UNSET:
            object.__setattr__(self, "messages", ())
        if self.temperature is _UNSET:
            object.__setattr__(self, "temperature", 0.7)
        if self.max_tokens is _UNSET:
            object.__setattr__(self, "max_tokens", 2000)
        if self.timeout is _UNSET:
            object.__setattr__(self, "timeout", 300.0)
        if not isinstance(self.prompt, str) or not self.prompt:
            raise ValueError("prompt must be a non-empty string")
        if self.model is not None and not isinstance(self.model, str):
            raise ValueError("model must be a string or None")
        if self.system_prompt is not None and not isinstance(self.system_prompt, str):
            raise ValueError("system_prompt must be a string or None")
        if isinstance(self.temperature, bool) or not isinstance(self.temperature, (int, float)) or not math.isfinite(self.temperature) or not 0 <= self.temperature <= 2:
            raise ValueError("temperature must be between 0 and 2")
        if isinstance(self.max_tokens, bool) or not isinstance(self.max_tokens, int) or self.max_tokens < 1:
            raise ValueError("max_tokens must be a positive integer")
        if isinstance(self.timeout, bool) or not isinstance(self.timeout, (int, float)) or not math.isfinite(self.timeout) or self.timeout <= 0:
            raise ValueError("timeout must be a finite number greater than zero")
        if not isinstance(self.messages, (tuple, list)):
            raise ValueError("messages must be a sequence of message mappings")
        for message in self.messages:
            if not isinstance(message, Mapping):
                raise ValueError("each message must be a mapping")
            if message.get("role") not in {"user", "assistant", "model"}:
                raise ValueError(f"unsupported message role: {message.get('role')!r}")
            if not isinstance(message.get("content"), str):
                raise ValueError("message content must be a string")

    def conversation(self) -> tuple[Mapping[str, str], ...]:
        """Return prior messages followed by the current prompt."""
        return (*self.messages, {"role": "user", "content": self.prompt})


@dataclass(frozen=True)
class ModelResponse:
    """Normalized provider response plus provider-specific metadata."""

    provider: str
    model: str
    text: str
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_creation_tokens: int = 0
    thinking_tokens: int = 0
    cost_usd: float | None = None
    request_id: str | None = None
    latency_ms: float | None = None
    raw_metadata: dict[str, Any] = field(default_factory=dict)


class ModelProvider(ABC):
    """Minimal contract implemented by every Ensemble model provider."""

    @abstractmethod
    def generate(self, request: ModelRequest) -> ModelResponse:
        """Generate one response for a provider-neutral request."""
        raise NotImplementedError
