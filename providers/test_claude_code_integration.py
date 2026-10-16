"""Optional real Claude Code integration test.

Run with ENSEMBLE_CLAUDE_INTEGRATION=1 on a machine with authenticated Claude Code.
"""

from __future__ import annotations

import os
import shutil

import pytest

from arbitration.api_client import MultiModelClient


@pytest.mark.skipif(
    os.environ.get("ENSEMBLE_CLAUDE_INTEGRATION") != "1",
    reason="real Claude Code integration is opt-in",
)
def test_real_claude_code_execution() -> None:
    if shutil.which("claude") is None:
        pytest.skip("Claude Code executable is not installed")

    # Exercise the real compatibility facade and registry, not just the low-level
    # adapter. This is the same routing path used by CE orchestration.
    response = MultiModelClient().call_model_response(
        model=os.environ.get("ENSEMBLE_CLAUDE_MODEL", "sonnet"),
        prompt="Reply with exactly the word READY.",
        timeout=120,
    )

    assert response.text.strip() == "READY"
    assert response.provider == "claude-code"
    assert response.model != "unknown"
