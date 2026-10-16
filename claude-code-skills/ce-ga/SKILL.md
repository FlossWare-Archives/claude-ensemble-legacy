---
name: ce-ga
description: Inspect Claude Ensemble genetic-algorithm tuning, candidate fitness, persisted results, and promotion eligibility. Use when asked whether GA is working or what tuning has actually improved.
---

# Claude Ensemble GA tuning

Inspect genetic-algorithm (GA) tuning evidence without confusing optimizer output with real-world quality improvement.

## 1. Verify environment and implementation

1. Resolve the selected Claude Code profile and verify the active CE checkout using `ENSEMBLE_REPO_ROOT` or the configured service definition and Git remote.
2. Inspect the current GA README, configuration, implementation, and dashboard before running a command. Confirm the actual dashboard path and available flags from the checked-out source or `--help`; do not guess paths.
3. Run `bash tools/ce diagnose --deep --json` if service health or Learning integration is relevant. Capture the exit status and output.

## 2. Inspect results read-only

- Use the documented GA dashboard and read-only artifact inspection paths. Identify the newest result, run ID, timestamp, evaluator, candidate count, selected parameters, and persistence acknowledgement when those fields exist.
- Check whether the evaluator is synthetic or based on real, independently evaluated task outcomes.
- Follow the result through Learning and operational Memory only using the actual contracts in the current checkout. Distinguish artifact creation, successful persistence, parameter selection, and downstream adoption.
- Inspect whether Knowledge promotion is eligible and why. Never infer eligibility from a high fitness score alone.
- Compare candidates to the documented baseline and check sample sizes, evaluator provenance, stale artifacts, and failure records.

## 3. Safety

Default to read-only. Do not start a GA run, modify configuration, promote Knowledge, change Thompson state, or write Learning/Memory records unless the user explicitly requests that operation. Do not invent fitness numbers or claim real improvement from synthetic evaluators. Never expose credentials or route source to external reviewers.

## 4. Report

State what ran, the latest grounded results, evaluator provenance, what is persisted, whether the result is eligible for promotion, what evidence supports or fails to support improvement, and the smallest useful next step. If no real outcome evaluation exists, say that improvement is not yet established.
