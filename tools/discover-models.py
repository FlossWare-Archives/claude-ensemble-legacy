#!/usr/bin/env python3
"""Print the model aliases supported by Claude Ensemble's active runtime.

This is an inventory command, not live provider discovery. It does not contact
model APIs, read credentials, mutate settings, or rewrite router code.
"""

from __future__ import annotations

import json

SUPPORTED_MODELS = ("haiku", "sonnet", "opus")


def main() -> int:
    print(json.dumps({
        "runtime": "claude-code",
        "models": [
            {"alias": model, "provider": "claude-code"}
            for model in SUPPORTED_MODELS
        ],
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
