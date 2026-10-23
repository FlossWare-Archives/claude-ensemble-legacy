"""Canonical paths for rebuildable Claude Ensemble corpus data.

This module centralizes derived-data locations. It never moves existing data:
operators must explicitly migrate or configure legacy paths before enabling writes.
"""
from __future__ import annotations

import os
from pathlib import Path


def _configured_path(name: str, default: Path) -> Path:
    value = os.environ.get(name)
    path = Path(value).expanduser() if value else default
    if not path.is_absolute():
        raise ValueError(f"{name} must be an absolute path")
    return path


def install_root() -> Path:
    """Return the release-independent CE install root."""
    return _configured_path(
        "ENSEMBLE_INSTALL_ROOT",
        Path.home() / ".FlossWare" / "claude-ensemble",
    )


def corpus_data_root() -> Path:
    """Return the canonical root for durable, rebuildable corpus data."""
    return _configured_path(
        "ENSEMBLE_CORPUS_DATA_DIR",
        install_root() / "shared" / "data",
    )


def memory_index_dir() -> Path:
    return _configured_path("ENSEMBLE_MEMORY_DIR", corpus_data_root() / "memory")


def memory_sources_dir() -> Path:
    return _configured_path(
        "ENSEMBLE_MEMORY_SOURCES_DIR", corpus_data_root() / "memory-sources"
    )


def knowledge_dir() -> Path:
    return _configured_path("ENSEMBLE_KNOWLEDGE_DIR", corpus_data_root() / "knowledge")


def graph_store_path() -> Path:
    # Match tools/managed_unit_config.py and the standalone deployment's
    # existing ENSEMBLE_GRAPH_STORE value. Do not silently create a second store.
    return _configured_path("ENSEMBLE_GRAPH_STORE", corpus_data_root() / "graph.json")
