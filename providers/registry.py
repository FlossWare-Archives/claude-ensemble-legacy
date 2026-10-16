"""Explicit model-to-provider routing for Claude Ensemble.

Claude models use the installed Claude Code runtime, whose upstream is checked
against ENSEMBLE_CLAUDE_CODE_EXPECTED_PROVIDER (Vertex by default). Gemini
requests use Google's API only after an explicit opt-in. Unknown labels fail;
they are never silently substituted with another model.
"""
from __future__ import annotations

import os
from collections.abc import Mapping

from .claude_code import ClaudeCodeProvider
from .credentials import CredentialPool
from .google import GoogleProvider
from .model_provider import ModelProvider

_DEFAULT_GEMINI_MODEL = "gemini-2.5-flash"


class ProviderRegistry:
    """Resolve requested model labels to an explicit, policy-gated provider."""

    def __init__(
        self,
        providers: Mapping[str, ModelProvider] | None = None,
        credentials: CredentialPool | None = None,
    ) -> None:
        self.providers = dict(providers or {})
        unsupported = set(self.providers) - {"claude-code", "google"}
        if unsupported:
            raise ValueError(f"Unsupported provider(s): {', '.join(sorted(unsupported))}")
        self.credentials = credentials or CredentialPool()
        self._configured_provider = os.environ.get(
            "ENSEMBLE_CLAUDE_CODE_EXPECTED_PROVIDER", "vertex"
        ).strip().lower()
        if not self._configured_provider:
            raise ValueError("ENSEMBLE_CLAUDE_CODE_EXPECTED_PROVIDER must not be blank")

    @staticmethod
    def google_api_enabled() -> bool:
        """Google is an external data destination and remains disabled by default."""
        return os.environ.get("ENSEMBLE_ALLOW_GOOGLE_MODEL_API", "").strip().lower() == "true"

    def _provider(self, name: str) -> ModelProvider:
        if name not in self.providers:
            if name == "claude-code":
                self.providers[name] = ClaudeCodeProvider(
                    expected_provider=self._configured_provider
                )
            elif name == "google":
                self.providers[name] = GoogleProvider(credentials=self.credentials)
            else:
                raise ValueError(f"Provider {name!r} is not registered")
        return self.providers[name]

    def register(self, name: str, provider: ModelProvider) -> None:
        if name not in {"claude-code", "google"}:
            raise ValueError(f"Unsupported provider: {name!r}")
        self.providers[name] = provider

    def resolve(self, model: str) -> ModelProvider:
        provider_name, _ = self.resolve_model(model)
        return self._provider(provider_name)

    def resolve_model(self, model: str) -> tuple[str, str]:
        """Resolve aliases without changing the requested model family."""
        if not isinstance(model, str) or not model.strip():
            raise ValueError("model must be a non-empty string")
        normalized = model.strip()
        lowered = normalized.lower()

        if lowered in {"haiku", "sonnet", "opus"} or lowered.startswith(("claude-", "claude_")):
            self._provider("claude-code")
            return "claude-code", lowered if lowered in {"haiku", "sonnet", "opus"} else normalized

        if lowered == "gemini":
            if not self.google_api_enabled():
                raise ValueError(
                    "Google model routing is disabled; set ENSEMBLE_ALLOW_GOOGLE_MODEL_API=true "
                    "only for workloads approved to send data to Google"
                )
            self._provider("google")
            return "google", _DEFAULT_GEMINI_MODEL

        if lowered.startswith(("gemini-", "gemini_", "models/gemini-")):
            if not self.google_api_enabled():
                raise ValueError(
                    "Google model routing is disabled; set ENSEMBLE_ALLOW_GOOGLE_MODEL_API=true "
                    "only for workloads approved to send data to Google"
                )
            self._provider("google")
            return "google", normalized.removeprefix("models/")

        raise ValueError(
            f"Unsupported model label: {normalized!r}; no provider fallback is performed"
        )

    def credential_status(self, provider: str) -> list[dict[str, object]]:
        if provider == "google" and not self.google_api_enabled():
            return []
        if provider == "google":
            return self.credentials.status("google")
        # Claude Code owns its authentication; CE does not inspect its secrets.
        return []
