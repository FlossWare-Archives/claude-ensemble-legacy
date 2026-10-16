---
name: ce-learning
description: Inspect Claude Ensemble's autonomous learning, Thompson routing, and GA tuning without fabricating outcomes or changing production learning state. Use when asked whether CE is learning, how tuning is progressing, or what evidence supports a routing change.
---

# Claude Ensemble learning and tuning

Use this skill to inspect evidence from CE's Learning, Thompson, and GA subsystems. A running timer or generated artifact is not proof of useful learning.

## 1. Verify the active checkout and service state

1. Resolve the selected Claude Code profile and active CE checkout. Prefer `ENSEMBLE_REPO_ROOT`; otherwise inspect the user service definition and verify the Git remote.
2. Run `bash tools/ce diagnose --deep --json` from the verified checkout. Capture the exit status and full output.
3. Inspect the checkout's README and command help before invoking dashboards or maintenance tools. The documented dashboard entry points include `thompson-dashboard.py`, `autonomous-learning-dashboard.py`, and `ga-tuning-dashboard.py`; confirm each is available with `command -v` or locate its actual implementation in the checkout. Never guess a script path.

## 2. Inspect learning evidence

- Use the documented dashboards and read-only inspection interfaces.
- Separate operational artifact creation, evaluated task outcomes, Thompson updates, and GA tuning candidates. They are different events and must not be treated as interchangeable.
- For GA, distinguish synthetic/zero-cost parameter optimization from evidence that real task outcomes improved.
- Check timestamps, sample counts, artifact status, deduplication, and the relationship between a result and the task/outcome that supports it when those fields exist.
- Do not treat SessionEnd, successful process exit, or artifact existence alone as a quality score.
- If a dashboard or endpoint is unavailable, report the exact failure and inspect its documented contract before trying alternatives.

## 3. Safety

Default to read-only inspection. Do not trigger tuning, replay outcomes, alter Thompson state, write Learning artifacts, or run an operation that may change production data unless the user explicitly asks for that action. Do not expose credentials or dump environment variables. Do not route source code or private workspace data to external providers.

## 4. Report

Summarize:
- service and diagnostic status
- latest relevant artifacts/candidates and their timestamps
- evidence of real evaluated outcomes versus synthetic tuning
- any missing linkage, stale data, low sample count, or service errors
- the smallest next action that would improve confidence

Use “not enough evidence” when the records do not establish improvement. Do not turn a graph that moves upward into a scientific conclusion just because humans enjoy charts.
