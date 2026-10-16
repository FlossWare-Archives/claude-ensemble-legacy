---
name: ce-review
description: Review local code changes or a specific file using Claude Ensemble's worker/arbiter workflow through the authenticated Claude Code runtime. Use when asked to review code with CE, without requiring a Jira ticket. Never send repository source to external reviewer providers.
---

# Claude Ensemble code review

Run this workflow in the user's current Claude Code session. Do not claim the review ran unless the subprocess completed and produced a report.

## Invocation

When invoked with a file path argument:
```bash
/ce-review path/to/file.py
```

Or with the current repository (no arguments):
```bash
/ce-review
```

Optional parameters:
- `--base BRANCH`: Review against a branch other than `main`.

## 1. Verify the target and runtime

1. **Parse arguments:** Extract optional file path and `--base BRANCH` from the skill args if present.
2. **Resolve the target path:** 
   - If a file path is provided, resolve it relative to the current working directory and validate it exists.
   - If no file path is provided, use the current working directory.
   - Confirm the target (file or directory) is inside a Git repository.
3. Do not reset, stash, checkout, clean, amend, or otherwise mutate the target repository.
4. **Check for reviewable content:**
   - If the target is a directory (repository review), verify there are changes against the base branch. 
   - If there are no changes and no file path was specified, explain this and show the exact command for a file-specific review: `python3 $CE_REPO/tools/arbitrate.py code-review <filepath> --target-branch main --phases 1 --tiers 1`
   - Do not run the review if there are no changes and no specific file path was provided.
5. Confirm the CE gateway is healthy:
   `curl -fsS --max-time 5 http://127.0.0.1:8080/api/v1/health`
6. Locate the active CE checkout. Prefer `ENSEMBLE_REPO_ROOT` when set. Otherwise inspect the active `claude-ensemble.service` unit with `systemctl --user cat claude-ensemble.service` and check the known FlossWare CE checkout paths. Accept a candidate only if it contains `tools/arbitrate.py` and its Git remote identifies the intended FlossWare/claude-ensemble repository. Do not silently use an arbitrary stale checkout. If no valid checkout is found, stop and report the blocker.

## 2. Run the CE worker/arbiter review

Use the active CE checkout's CLI and the user's authenticated Claude Code runtime. Do not invoke web reviewers, Gemini, Grok, Perplexity, or other external reviewer providers for repository source.

The command is the same for both repository and file reviews; `arbitrate.py` accepts either:

```bash
python3 "$CE_REPO/tools/arbitrate.py" code-review "$TARGET_PATH" \
  --target-branch "$BASE_BRANCH" --phases 1 --tiers 1
```

Where `$TARGET_PATH` is either:
- A directory (repository review with changes against `--target-branch`), or
- A file path (review a specific file even if it has no staged changes).

Capture the command exit status and complete report. Wait for completion. A nonzero exit, unavailable worker, or empty report is a failed/incomplete review, not success. Do not edit the target repository while reviewing it.

## 3. Report findings

**On successful review completion:**
- target path (file or repository) and base branch
- whether CE completed successfully (exit code 0)
- findings ordered by severity, with file/line references when available
- missing tests and likely regressions
- worker/arbiter errors, unavailable services, or limitations

**On missing content (clean working tree with no file path specified):**
- Clearly state there are no changes to review against the base branch.
- Show the exact command for a file-specific review.
- Do not claim a review ran.

Distinguish confirmed defects from questions and suggestions. Verify every finding against the supplied source. Do not claim a finding is validated unless the source supports it.
