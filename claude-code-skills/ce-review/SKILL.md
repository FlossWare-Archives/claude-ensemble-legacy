---
name: ce-review
description: Review local code changes or a specific file using Claude Ensemble's worker/arbiter workflow through the authenticated Claude Code runtime. Use when asked to review code with CE, without requiring a Jira ticket. Never send repository source to external reviewer providers.
---

# Claude Ensemble code review

Run this workflow in the user's current Claude Code session. Do not claim the review ran unless the subprocess completed and produced a report.

## Inputs

- Local Git repository path: default to the current working directory.
- Base branch: default to `main`, unless the user specifies another base.
- Optional specific file: use it when the user asks to review one file, even if it has no diff.

## 1. Verify the target and runtime

1. Resolve the target path and confirm it is inside a Git repository.
2. Do not reset, stash, checkout, clean, amend, or otherwise mutate the target repository.
3. For a repository review, verify there are changes against the requested base branch. If there are none, explain that and offer a file-specific review using the existing CLI.
4. Confirm the CE gateway is healthy:
   `curl -fsS --max-time 5 http://127.0.0.1:8080/api/v1/health`
5. Locate the active CE checkout. Prefer `ENSEMBLE_REPO_ROOT` when set. Otherwise inspect the active `claude-ensemble.service` unit with `systemctl --user cat claude-ensemble.service` and check the known FlossWare CE checkout paths. Accept a candidate only if it contains `tools/arbitrate.py` and its Git remote identifies the intended FlossWare/claude-ensemble repository. Do not silently use an arbitrary stale checkout. If no valid checkout is found, stop and report the blocker.

## 2. Run the CE worker/arbiter review

Use the active CE checkout's CLI and the user's authenticated Claude Code runtime. Do not invoke web reviewers, Gemini, Grok, Perplexity, or other external reviewer providers for repository source.

For a repository review:

```bash
python3 "$CE_REPO/tools/arbitrate.py" code-review "$REPO_PATH" \
  --target-branch "$BASE_BRANCH" --phases 1 --tiers 1
```

For a specific file:

```bash
python3 "$CE_REPO/tools/arbitrate.py" code-review "$FILE_PATH" \
  --target-branch "$BASE_BRANCH" --phases 1 --tiers 1
```

Capture the command exit status and complete report. Wait for completion. A nonzero exit, unavailable worker, or empty report is a failed/incomplete review, not success. Do not edit the target repository while reviewing it.

## 3. Report findings

Return:
- repository path, base branch, and files reviewed
- whether CE completed successfully
- findings ordered by severity, with file/line references when available
- missing tests and likely regressions
- worker/arbiter errors, unavailable services, or limitations

Distinguish confirmed defects from questions and suggestions. Verify every finding against the supplied source. Do not claim a finding is validated unless the source supports it.
