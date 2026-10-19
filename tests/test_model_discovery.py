"""Tests for the single-runtime model inventory command."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def test_discovery_reports_only_supported_claude_code_aliases() -> None:
    result = subprocess.run(
        [sys.executable, str(REPO / "tools" / "discover-models.py")],
        cwd=REPO,
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(result.stdout)
    assert payload["runtime"] == "claude-code"
    assert [item["alias"] for item in payload["models"]] == [
        "haiku",
        "sonnet",
        "opus",
    ]
    assert all(item["provider"] == "claude-code" for item in payload["models"])


def test_discovery_has_no_legacy_path_or_mutation_api() -> None:
    source = (REPO / "tools" / "discover-models.py").read_text(encoding="utf-8")
    assert "RH_TOOLS_ROOT" not in source
    assert "Development/redhat" not in source
    assert "update_thompson_router" not in source
    assert "update_settings" not in source
    assert "class ModelDiscovery" not in source
