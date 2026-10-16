#!/usr/bin/env python3
"""Report supported Claude Code model aliases without external API discovery.

Claude Ensemble has one active model runtime. This command is intentionally
read-only: it does not probe third-party APIs, rewrite the Thompson router, or
modify a user's Claude settings file.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(message)s")
logger = logging.getLogger(__name__)

SUPPORTED_MODELS = {"haiku": "haiku", "sonnet": "sonnet", "opus": "opus"}


class ModelDiscovery:
    """Compatibility wrapper for callers of the former discovery script."""

    def __init__(self, settings_path: Path, thompson_path: Path):
        self.settings_path = Path(settings_path)
        self.thompson_path = Path(thompson_path)
        self.discovered = dict(SUPPORTED_MODELS)

    def get_best_models(self) -> Dict[str, str]:
        """Return the only model aliases supported by the active runtime."""
        return dict(SUPPORTED_MODELS)

    def update_thompson_router(self, best_models: Dict[str, str]) -> None:
        """No-op: do not rewrite source code to discover model names."""
        logger.info("Thompson routing retained; model discovery no longer edits source")

    def update_settings(self, best_models: Dict[str, str]) -> None:
        """No-op: do not mutate user-owned Claude settings."""
        logger.info("User settings left unchanged")

    def report(self, best_models: Dict[str, str]) -> None:
        print("\n" + "=" * 60)
        print("  CLAUDE CODE MODEL RUNTIME")
        print("=" * 60)
        print("Supported aliases:")
        for alias in ("haiku", "sonnet", "opus"):
            print(f"  {alias:12s} -> {best_models[alias]}")
        print("\nAll requests use the installed Claude Code CLI.")
        print("No external model APIs were contacted; no settings were changed.")
        print("=" * 60 + "\n")

    def run(self) -> Dict[str, str]:
        best_models = self.get_best_models()
        self.update_thompson_router(best_models)
        self.update_settings(best_models)
        self.report(best_models)
        return best_models


if __name__ == "__main__":
    import os

    rh_tools_root = Path(
        os.environ.get(
            "RH_TOOLS_ROOT",
            Path.home() / "Development/redhat/scm/gitlab/cee/sfloess/claude-global-skills",
        )
    )
    discoverer = ModelDiscovery(
        rh_tools_root / "settings.json",
        rh_tools_root / "shared/thompson_router.py",
    )
    discoverer.run()
