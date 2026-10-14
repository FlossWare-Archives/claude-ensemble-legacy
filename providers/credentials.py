"""Credential sources and multi-account selection for model providers."""

from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class Credential:
    name: str
    provider: str
    api_key: str
    source: str


class CredentialPool:
    """Resolve provider credentials from env and an optional YAML file."""

    def __init__(self, *, credentials_file: str | None = None) -> None:
        self.credentials_file = credentials_file or os.environ.get("ENSEMBLE_CREDENTIALS_FILE")
        self._lock = threading.Lock()
        self._next: dict[str, int] = {}
        self._cooldowns: dict[tuple[str, str], float] = {}
        self._credentials: dict[str, list[Credential]] = {}
        self.reload()

    def reload(self) -> None:
        credentials: dict[str, list[Credential]] = {}
        for provider, env_name in {
            "anthropic": "ANTHROPIC_API_KEY",
            "google": "GOOGLE_API_KEY",
        }.items():
            value = os.environ.get(env_name)
            if value:
                credentials.setdefault(provider, []).append(
                    Credential("default", provider, value, f"env:{env_name}")
                )
            prefix = env_name + "_"
            for name, secret in os.environ.items():
                if name.startswith(prefix) and secret:
                    account = name[len(prefix):].lower().replace("_", "-")
                    credentials.setdefault(provider, []).append(
                        Credential(account, provider, secret, f"env:{name}")
                    )
        if self.credentials_file:
            credentials = self._merge_file(credentials, Path(self.credentials_file).expanduser())
        self._validate_unique_identities(credentials)
        with self._lock:
            self._credentials = credentials
            self._next.clear()

    @staticmethod
    def _validate_unique_identities(credentials: dict[str, list[Credential]]) -> None:
        for provider, entries in credentials.items():
            seen: set[str] = set()
            for item in entries:
                identity = item.name.strip().lower().replace("_", "-")
                if not identity or identity in seen:
                    # Never include credential values in diagnostics.
                    raise ValueError(
                        f"duplicate or empty credential identity for {provider}/{identity or '<empty>'}"
                    )
                seen.add(identity)

    @staticmethod
    def _merge_file(credentials: dict[str, list[Credential]], path: Path) -> dict[str, list[Credential]]:
        if not path.exists():
            return credentials
        try:
            import yaml
        except ImportError as exc:
            raise RuntimeError("PyYAML is required when ENSEMBLE_CREDENTIALS_FILE is configured") from exc
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if not isinstance(raw, dict):
            raise ValueError("credentials YAML must contain an object at the top level")
        for provider, entries in raw.items():
            if not isinstance(provider, str) or not isinstance(entries, dict):
                raise ValueError("credentials YAML must map providers to credential objects")
            provider = provider.strip().lower()
            if not provider:
                raise ValueError("credential provider names must not be empty")
            for name, value in entries.items():
                if not isinstance(name, str) or not name.strip() or not isinstance(value, dict):
                    raise ValueError(f"invalid credential entry for {provider!r}")
                account = name.strip().lower().replace("_", "-")
                api_key = value.get("api_key")
                if not isinstance(api_key, str) or not api_key:
                    raise ValueError(f"credential {provider}/{name} must contain api_key")
                credentials.setdefault(provider, []).append(
                    Credential(account, provider, api_key, f"file:{path}")
                )
        return credentials

    def names(self, provider: str) -> list[str]:
        with self._lock:
            return [item.name for item in self._credentials.get(provider, [])]

    def select(self, provider: str, requested: str | None = None) -> Credential:
        with self._lock:
            all_credentials = self._credentials.get(provider, [])
            available = [
                item for item in all_credentials
                if self._cooldowns.get((provider, item.name), 0) <= time.monotonic()
            ]
            if requested:
                for item in available:
                    if item.name == requested:
                        return item
                if any(item.name == requested for item in all_credentials):
                    raise RuntimeError(f"credential {provider}/{requested!r} is cooling down")
                raise ValueError(f"credential {provider}/{requested!r} is not configured")
            if not available:
                raise RuntimeError(f"no available credentials configured for provider {provider!r}")
            index = self._next.get(provider, 0) % len(available)
            self._next[provider] = index + 1
            return available[index]

    def mark_failed(self, provider: str, credential: str, cooldown_seconds: float = 30.0) -> None:
        with self._lock:
            self._cooldowns[(provider, credential)] = time.monotonic() + cooldown_seconds

    def status(self, provider: str) -> list[dict[str, Any]]:
        now = time.monotonic()
        with self._lock:
            return [
                {"name": item.name, "source": item.source,
                 "state": "cooling_down" if self._cooldowns.get((provider, item.name), 0) > now else "available"}
                for item in self._credentials.get(provider, [])
            ]
