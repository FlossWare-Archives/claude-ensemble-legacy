"""Small model-to-provider registry for Claude Ensemble."""

from __future__ import annotations

import os
from collections.abc import Mapping

from .anthropic import AnthropicProvider
from .credentials import CredentialPool
from .claude_code import ClaudeCodeProvider
from .google import GoogleProvider
from .model_provider import ModelProvider


class ProviderRegistry:
    """Resolve models and construct providers backed by shared credentials."""

    _ANTHROPIC_ALIASES = {"haiku": "CLAUDE_HAIKU_MODEL", "sonnet": "CLAUDE_SONNET_MODEL", "opus": "CLAUDE_OPUS_MODEL"}
    _DEFAULT_ANTHROPIC_MODELS = {"haiku": "claude-haiku-4-5", "sonnet": "claude-sonnet-5", "opus": "claude-opus-5"}

    def __init__(self, providers: Mapping[str, ModelProvider] | None = None,
                 credentials: CredentialPool | None = None) -> None:
        self.providers = dict(providers or {})
        self.credentials = credentials or CredentialPool()

    def _provider(self, name: str) -> ModelProvider:
        if name not in self.providers:
            if name == "anthropic":
                self.providers[name] = AnthropicProvider(credentials=self.credentials)
            elif name == "google":
                self.providers[name] = GoogleProvider(credentials=self.credentials)
            elif name == "claude-code":
                self.providers[name] = ClaudeCodeProvider()
        if name not in self.providers:
            raise ValueError(f"Provider {name!r} is not registered")
        return self.providers[name]

    def register(self, name: str, provider: ModelProvider) -> None:
        self.providers[name] = provider

    def resolve(self, model: str) -> ModelProvider:
        provider_name, _ = self.resolve_model(model)
        return self._provider(provider_name)

    def resolve_model(self, model: str) -> tuple[str, str]:
        normalized = model.lower()
        cli_models = {
            item.strip().lower()
            for item in os.environ.get("ENSEMBLE_CLAUDE_CODE_MODELS", "").split(",")
            if item.strip()
        }
        if normalized in cli_models:
            provider_name = "claude-code"
            # Claude Code accepts short aliases itself; preserve them rather than
            # translating them into direct-API model IDs.
            canonical = normalized
        elif normalized in self._ANTHROPIC_ALIASES:
            provider_name = "anthropic"
            env_name = self._ANTHROPIC_ALIASES[normalized]
            canonical = os.environ.get(env_name, self._DEFAULT_ANTHROPIC_MODELS[normalized])
        elif normalized.startswith(("claude-", "claude_")):
            provider_name, canonical = "anthropic", model
        elif normalized.startswith(("gemini-", "gemini_", "models/gemini-")):
            provider_name, canonical = "google", model
        else:
            raise ValueError(f"No provider mapping for model {model!r}; register it explicitly")
        self._provider(provider_name)
        return provider_name, canonical

    def credential_status(self, provider: str) -> list[dict[str, object]]:
        return self.credentials.status(provider)
