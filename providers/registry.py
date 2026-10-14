"""Single-runtime model routing for Claude Ensemble.

All model work runs through the installed Claude Code CLI. Direct Anthropic and
Google API adapters have been removed from this branch; the prior implementation
is preserved on the archive branch.
"""

from __future__ import annotations

import os
from collections.abc import Mapping

from .claude_code import ClaudeCodeProvider
from .model_provider import ModelProvider


class ProviderRegistry:
    """Compatibility facade that routes every model request through Claude Code."""

    def __init__(
        self,
        providers: Mapping[str, ModelProvider] | None = None,
        credentials: object | None = None,
    ) -> None:
        # Keep the old constructor shape for callers, but reject alternative
        # runtimes instead of silently allowing multi-provider dispatch.
        self.providers: dict[str, ModelProvider] = {}
        for name, provider in (providers or {}).items():
            if name != "claude-code":
                raise ValueError(
                    f"Only the 'claude-code' runtime is supported; got {name!r}"
                )
            self.providers[name] = provider
        self._configured_provider = os.environ.get(
            "ENSEMBLE_CLAUDE_CODE_EXPECTED_PROVIDER", "anthropic"
        ).strip().lower()
        if not self._configured_provider:
            raise ValueError(
                "ENSEMBLE_CLAUDE_CODE_EXPECTED_PROVIDER must not be blank"
            )

    def _provider(self) -> ModelProvider:
        if "claude-code" not in self.providers:
            self.providers["claude-code"] = ClaudeCodeProvider(
                expected_provider=self._configured_provider
            )
        return self.providers["claude-code"]

    def register(self, name: str, provider: ModelProvider) -> None:
        if name != "claude-code":
            raise ValueError(
                f"Only the 'claude-code' runtime is supported; got {name!r}"
            )
        self.providers[name] = provider

    def resolve(self, model: str) -> ModelProvider:
        self.resolve_model(model)
        return self._provider()

    def resolve_model(self, model: str) -> tuple[str, str]:
        """Normalize legacy model labels to one Claude Code model.

        Claude model names and aliases are preserved. Historical non-Claude
        labels are mapped to Sonnet so old worker lists continue to execute,
        but they do not invoke another provider or imply model diversity.
        """
        if not isinstance(model, str) or not model.strip():
            raise ValueError("model must be a non-empty string")
        normalized = model.strip()
        lowered = normalized.lower()

        aliases = {"haiku", "sonnet", "opus"}
        if lowered in aliases:
            canonical = lowered
        elif lowered.startswith(("claude-", "claude_")):
            canonical = normalized
        else:
            canonical = "sonnet"
        self._provider()
        return "claude-code", canonical

    def credential_status(self, provider: str) -> list[dict[str, object]]:
        """Return no API-key accounts; Claude Code owns authentication."""
        if provider in {"claude-code", "anthropic", "google"}:
            return []
        return []
