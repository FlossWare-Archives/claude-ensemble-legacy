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



def test_duplicate_default_identity_across_environment_and_yaml_is_rejected(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "environment-secret-sentinel")
    path = tmp_path / "credentials.yaml"
    path.write_text(
        "anthropic:\n"
        "  default:\n"
        "    api_key: yaml-secret-sentinel\n",
        encoding="utf-8",
    )
    try:
        CredentialPool(credentials_file=str(path))
    except ValueError as exc:
        assert "anthropic/default" in str(exc)
        assert "environment-secret-sentinel" not in str(exc)
        assert "yaml-secret-sentinel" not in str(exc)
    else:
        raise AssertionError("duplicate default identity should be rejected")


def test_duplicate_named_identity_across_environment_and_yaml_is_rejected(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY_PERSONAL_1", "environment-secret-sentinel")
    path = tmp_path / "credentials.yaml"
    path.write_text(
        "anthropic:\n"
        "  personal-1:\n"
        "    api_key: yaml-secret-sentinel\n",
        encoding="utf-8",
    )
    try:
        CredentialPool(credentials_file=str(path))
    except ValueError as exc:
        assert "anthropic/personal-1" in str(exc)
        assert "environment-secret-sentinel" not in str(exc)
        assert "yaml-secret-sentinel" not in str(exc)
    else:
        raise AssertionError("duplicate named identity should be rejected")



def test_yaml_only_normalized_identity_collision_is_rejected(
    tmp_path: Path, monkeypatch
) -> None:
    for name in tuple(os.environ):
        if name == "ANTHROPIC_API_KEY" or name.startswith("ANTHROPIC_API_KEY_"):
            monkeypatch.delenv(name, raising=False)
    path = tmp_path / "credentials.yaml"
    path.write_text(
        "anthropic:\\n"
        "  account_a:\\n"
        "    api_key: first-secret-sentinel\\n"
        "  account-a:\\n"
        "    api_key: second-secret-sentinel\\n",
        encoding="utf-8",
    )
    try:
        CredentialPool(credentials_file=str(path))
    except ValueError as exc:
        assert "anthropic/account-a" in str(exc)
        assert "first-secret-sentinel" not in str(exc)
        assert "second-secret-sentinel" not in str(exc)
    else:
        raise AssertionError("normalized YAML-only identity collision should be rejected")


def test_failed_reload_keeps_previous_credentials_active(
    tmp_path: Path, monkeypatch
) -> None:
    for name in tuple(os.environ):
        if name == "ANTHROPIC_API_KEY" or name.startswith("ANTHROPIC_API_KEY_"):
            monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY_ACCOUNT_A", "environment-secret-sentinel")
    path = tmp_path / "credentials.yaml"
    path.write_text(
        "anthropic:\\n"
        "  personal-2:\\n"
        "    api_key: yaml-secret-sentinel\\n",
        encoding="utf-8",
    )
    pool = CredentialPool(credentials_file=str(path))
    assert pool.select("anthropic", "account-a").api_key == "environment-secret-sentinel"

    path.write_text(
        "anthropic:\\n"
        "  account-a:\\n"
        "    api_key: colliding-secret-sentinel\\n",
        encoding="utf-8",
    )
    try:
        pool.reload()
    except ValueError as exc:
        assert "anthropic/account-a" in str(exc)
        assert "environment-secret-sentinel" not in str(exc)
        assert "colliding-secret-sentinel" not in str(exc)
    else:
        raise AssertionError("reload with duplicate identity should be rejected")

    assert pool.select("anthropic", "account-a").api_key == "environment-secret-sentinel"


def test_failed_credential_enters_cooldown(monkeypatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY_ACCOUNT_A", "secret-a")
    monkeypatch.setenv("ANTHROPIC_API_KEY_ACCOUNT_B", "secret-b")
    pool = CredentialPool()
    pool.mark_failed("anthropic", "account-a", cooldown_seconds=60)
    assert pool.status("anthropic")[0]["state"] == "cooling_down"
    assert pool.select("anthropic").name != "account-a"
