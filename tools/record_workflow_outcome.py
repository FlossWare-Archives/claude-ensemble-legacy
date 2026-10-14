#!/usr/bin/env python3
"""Submit an evidence-backed workflow outcome through the Learning service.

This is an orchestration adapter, not a Claude Code lifecycle hook: generic
Stop/SessionEnd events do not establish task quality. The caller must supply a
stable run ID, a real 1-5 evaluation, and evidence explaining that evaluation.
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from learning.learning_client import LearningClient
from learning.portable_artifacts import LearningArtifact

ARTIFACT_TYPE = "workflow-outcome"


def _required_text(value: Any, field: str, max_length: int = 512) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string")
    value = value.strip()
    if len(value) > max_length:
        raise ValueError(f"{field} must be at most {max_length} characters")
    return value


def validate_payload(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("input must be a JSON object")

    run_id = _required_text(payload.get("run_id"), "run_id")
    task_id = _required_text(payload.get("task_id", run_id), "task_id")
    task_type = _required_text(payload.get("task_type"), "task_type")
    model = _required_text(payload.get("model"), "model")
    rating = payload.get("rating")
    if isinstance(rating, bool) or not isinstance(rating, int) or not 1 <= rating <= 5:
        raise ValueError("rating must be an integer from 1 through 5")

    tokens = payload.get("tokens")
    if isinstance(tokens, bool) or not isinstance(tokens, int) or tokens < 0:
        raise ValueError("tokens must be a non-negative integer")

    cost = payload.get("cost")
    if isinstance(cost, bool) or not isinstance(cost, (int, float)) or cost < 0:
        raise ValueError("cost must be a finite non-negative number")
    try:
        cost = float(cost)
    except (OverflowError, ValueError) as exc:
        raise ValueError("cost must be a finite non-negative number") from exc
    if not math.isfinite(cost):
        raise ValueError("cost must be a finite non-negative number")

    evidence = payload.get("evidence")
    if not isinstance(evidence, dict):
        raise ValueError("evidence must be an object; completion alone is not evidence")
    evidence = {
        "kind": _required_text(evidence.get("kind"), "evidence.kind", 80),
        "reference": _required_text(evidence.get("reference"), "evidence.reference", 1024),
        "summary": _required_text(evidence.get("summary"), "evidence.summary", 2000),
    }

    return {
        "run_id": run_id,
        "task_id": task_id,
        "task_type": task_type,
        "model": model,
        "rating": rating,
        "tokens": tokens,
        "cost": cost,
        "evidence": evidence,
    }


def submit_outcome(payload: Any, client: Any) -> dict[str, Any]:
    outcome = validate_payload(payload)
    # A deterministic artifact is important: a retry must match the original
    # content under the Learning service's (artifact type, run_id) key.
    artifact = LearningArtifact(
        artifact_type=ARTIFACT_TYPE,
        source="claude-ensemble-workflow-adapter",
        payload={
            "run_id": outcome["run_id"],
            "task_id": outcome["task_id"],
            "task_type": outcome["task_type"],
            "model": outcome["model"],
            "rating": outcome["rating"],
            "tokens": outcome["tokens"],
            "cost": outcome["cost"],
        },
        provenance={"evidence": outcome["evidence"]},
        created_at=None,
    )
    artifact_result = client.record_artifact(artifact.to_dict(), request_id=outcome["run_id"])
    if not artifact_result.get("ok"):
        raise RuntimeError("Learning service did not acknowledge the outcome artifact")

    if not client.process_outcome(
        task_id=outcome["task_id"],
        task_type=outcome["task_type"],
        model=outcome["model"],
        rating=outcome["rating"],
        tokens=outcome["tokens"],
        cost=outcome["cost"],
        request_id=outcome["run_id"],
    ):
        raise RuntimeError("Learning service did not acknowledge outcome processing")

    return {
        "ok": True,
        "run_id": outcome["run_id"],
        "task_id": outcome["task_id"],
        "artifact_status": artifact_result.get("artifact_status", "acknowledged"),
        "learning_outcome": "acknowledged",
    }


def main() -> int:
    try:
        raw = sys.stdin.read()
        if not raw.strip():
            raise ValueError("expected a JSON object on stdin")
        result = submit_outcome(json.loads(raw), LearningClient())
        print(json.dumps(result, sort_keys=True))
        return 0
    except (ValueError, json.JSONDecodeError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, sort_keys=True), file=sys.stderr)
        return 2
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, sort_keys=True), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
