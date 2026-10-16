---
name: ce-solve
description: Investigate and solve a coding problem in the current repository using Claude Code, targeted tests, and Claude Ensemble review where useful. Use when asked to fix a bug, implement a feature, or resolve a concrete engineering task.
---

# Claude Ensemble solve

Solve the requested engineering task in the current working repository. Use the authenticated Claude Code runtime and local project context. Do not send proprietary source to external reviewer providers.

## 1. Establish scope and safety

1. Identify the current repository root, branch, working-tree status, and the user's requested outcome.
2. Read relevant project instructions and inspect existing changes before editing. Preserve unrelated user work; never reset, stash, checkout, clean, or overwrite user changes to make the task easier.
3. If the request is underspecified in a way that blocks a safe implementation, ask one focused question. Otherwise state a short plan and proceed.
4. Locate the active CE checkout using `ENSEMBLE_REPO_ROOT` or the configured CE service and verify its Git remote before invoking CE tooling.

## 2. Investigate before editing

- Reproduce the failure or trace the relevant execution path where feasible.
- Read the implementation, tests, configuration, and caller contracts that govern the behavior.
- Identify the smallest change that addresses the root cause. Avoid opportunistic refactors and unrelated formatting churn.
- Prefer existing project patterns and dependencies. Do not add a new dependency without a concrete need.

## 3. Implement and verify

- Make the change in the current target repository, respecting its instructions and the user's scope.
- Add or update focused regression tests for the defect or behavior.
- Run the narrowest meaningful tests first, then broader relevant checks when practical. Report commands, exit statuses, and failures honestly. Do not claim tests passed if they were not run.
- If the change is reviewable, use the installed `/ce-review` workflow or the verified CE CLI `tools/arbitrate.py code-review` to request a worker/arbiter review. Check the CLI help and actual arguments first; do not assume a wrapper option is supported.
- Treat review findings as hypotheses to verify against source. Fix valid findings and rerun relevant tests.

## 4. Boundaries

- Do not commit, push, open a PR, merge, deploy, restart services, or perform destructive data/configuration changes unless the user explicitly requested that action or it is clearly part of the current authorized task.
- Do not silently change branches or discard worktree changes.
- Do not route source code or private workspace data to Grok, Perplexity, Gemini, web reviewers, or other external providers. Keep review calls on the authenticated Claude Code runtime.
- If blocked by missing credentials, unavailable services, or a failing test environment, continue with safe source-level work and clearly identify the blocker rather than fabricating runtime validation.

## 5. Completion report

Summarize the root cause, files changed, behavior now addressed, tests actually run and their results, any remaining risk, and whether CE worker/arbiter review completed. Separate completed work from recommended follow-up.
