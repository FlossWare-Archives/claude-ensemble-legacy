"""Tests for multi-account credential selection."""

from __future__ import annotations

from pathlib import Path
import os

from providers.credentials import CredentialPool


def test_default_environment_credential(monkeypatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "default-secret")
    pool = CredentialPool()
    credential = pool.select("anthropic")
    assert credential.name == "default"
    assert credential.api_key == "default-secret"


def test_numbered_environment_credentials_rotate(monkeypatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY_ACCOUNT_A", "secret-a")
    monkeypatch.setenv("ANTHROPIC_API_KEY_ACCOUNT_B", "secret-b")
    pool = CredentialPool()
    assert pool.select("anthropic").name == "account-a"
    assert pool.select("anthropic").name == "account-b"


def test_yaml_credentials_can_be_selected(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    path = tmp_path / "credentials.yaml"
    path.write_text(
        "anthropic:\n"
        "  personal-1:\n"
        "    api_key: secret-one\n"
        "  personal-2:\n"
        "    api_key: secret-two\n",
        encoding="utf-8",
    )
    pool = CredentialPool(credentials_file=str(path))
    assert pool.select("anthropic", "personal-2").api_key == "secret-two"


def test_failed_credential_enters_cooldown(monkeypatch) -> None:
    for name in tuple(os.environ):
        if name == "ANTHROPIC_API_KEY" or name.startswith("ANTHROPIC_API_KEY_"):
            monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY_ACCOUNT_A", "secret-a")
    monkeypatch.setenv("ANTHROPIC_API_KEY_ACCOUNT_B", "secret-b")
    pool = CredentialPool()
    pool.mark_failed("anthropic", "account-a", cooldown_seconds=60)
    assert pool.status("anthropic")[0]["state"] == "cooling_down"
    assert pool.select("anthropic").name != "account-a"
