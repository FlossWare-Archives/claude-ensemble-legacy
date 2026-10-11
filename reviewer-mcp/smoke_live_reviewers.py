#!/usr/bin/env python3
"""Opt-in live smoke checks for configured reviewer-MCP providers.

This deliberately sends the selected public PR diff to the configured external
reviewers. It is never run by ordinary CI and requires an explicit opt-in.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import broker  # noqa: E402


PROVIDERS = {
    "grok": ("XAI_API_KEY", broker.grok, "xai"),
    "perplexity": ("PERPLEXITY_API_KEY", broker.perplexity, "perplexity"),
    "jules": ("JULES_API_KEY", broker.jules, "google-jules"),
}


def main() -> int:
    if os.environ.get("ENSEMBLE_LIVE_REVIEWER_TESTS") != "1":
        print("not run: set ENSEMBLE_LIVE_REVIEWER_TESTS=1 to authorize live reviewer calls")
        return 2

    repository = os.environ.get("REVIEWER_SMOKE_REPOSITORY", "")
    pr_number = os.environ.get("REVIEWER_SMOKE_PR_NUMBER", "")
    if not repository or not pr_number.isdigit():
        print("not run: set REVIEWER_SMOKE_REPOSITORY and REVIEWER_SMOKE_PR_NUMBER")
        return 2

    selected = [
        name.strip()
        for name in os.environ.get(
            "REVIEWER_SMOKE_REVIEWERS", "grok,perplexity,jules"
        ).split(",")
        if name.strip()
    ]
    unknown = sorted(set(selected) - set(PROVIDERS))
    if unknown:
        print("invalid reviewer selection: " + ", ".join(unknown))
        return 2

    try:
        payload = broker.package(repository, int(pr_number))
    except Exception as exc:
        print(json.dumps({"status": "failed", "stage": "fetch-pr", "error": str(exc)}))
        return 1

    results = []
    attempted = 0
    failed = False
    for name in selected:
        env_name, invoke, provider = PROVIDERS[name]
        if not os.environ.get(env_name):
            results.append({
                "reviewer": name,
                "provider": provider,
                "status": "not_run",
                "reason": "missing credential: " + env_name,
            })
            continue
        attempted += 1
        result = invoke(payload)
        status = result.get("status", "failed")
        safe_result = {
            "reviewer": name,
            "provider": result.get("provider", provider),
            "status": status,
            "model": result.get("model", ""),
            "latency_ms": result.get("latency_ms"),
            "error": result.get("error", ""),
        }
        results.append(safe_result)
        failed = failed or status != "complete"

    print(json.dumps(results, indent=2))
    if attempted == 0:
        return 2
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
