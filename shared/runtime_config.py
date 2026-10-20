"""Shared runtime paths for Claude Ensemble services.

Environment variables make the services usable outside the original workstation
layout while using an isolated CE state root when variables are not configured.
"""

import os
from pathlib import Path


def path_from_env(name: str, default: Path) -> Path:
    value = os.environ.get(name)
    return Path(value).expanduser() if value else default


def memory_dir() -> Path:
    return path_from_env(
        "ENSEMBLE_MEMORY_DIR",
        Path.home() / ".FlossWare" / "claude-ensemble-state" / "data" / "memory",
    )


def log_dir() -> Path:
    return path_from_env("ENSEMBLE_LOG_DIR", Path.home() / ".FlossWare" / "claude-ensemble-state" / "logs")


def runtime_dir() -> Path:
    configured = os.environ.get("ENSEMBLE_RUNTIME_DIR")
    if configured:
        return Path(configured).expanduser()
    xdg = os.environ.get("XDG_RUNTIME_DIR")
    return (Path(xdg) if xdg else Path.home() / ".cache") / "claude-ensemble"


def socket_path(env_name: str, default_filename: str) -> Path:
    value = os.environ.get(env_name)
    if value:
        return Path(value).expanduser()
    return Path(default_filename).expanduser()


def learning_dir() -> Path:
    return path_from_env(
        "ENSEMBLE_LEARNING_DIR",
        Path.home() / ".FlossWare" / "claude-ensemble-state" / "data" / "learning",
    )


def thompson_state_file() -> Path:
    return path_from_env(
        "ENSEMBLE_THOMPSON_STATE_FILE",
        learning_dir() / "thompson-sampling-state.json",
    )


def alert_dir() -> Path:
    return path_from_env("ENSEMBLE_ALERT_DIR", Path.home() / ".FlossWare" / "claude-ensemble-state" / "data" / "alerts")


def repo_root() -> Path:
    value = os.environ.get("ENSEMBLE_REPO_ROOT")
    if value:
        return Path(value).expanduser()
    return Path.cwd()
