---
name: ce-diagnose
description: Run and interpret Claude Ensemble diagnostics to find concrete service, gateway, hook, and worker/arbiter failures. Use when a CE feature is failing or deeper diagnostics are needed.
---

# Claude Ensemble diagnostics

Use this skill to run the existing diagnostic tooling and explain its evidence. Do not replace diagnostics with guesses, generic health checks, or CI status.

## Establish the active checkout and profile

1. Resolve the selected Claude Code profile from `ENSEMBLE_CLAUDE_CODE_CONFIG_DIR`, then `CLAUDE_CONFIG_DIR`, then `~/.claude`.
2. Locate CE via `ENSEMBLE_REPO_ROOT`; otherwise inspect `systemctl --user cat claude-ensemble.service` and verify the candidate checkout has `tools/ce`, `scripts/ce-diagnose.py`, and a Git remote for `FlossWare/claude-ensemble`.
3. Never run diagnosis from an unverified stale checkout or assume the default URL when an override is configured.

## Run diagnostics in increasing scope

Run these from the verified checkout, capturing full output and exit status:

```bash
bash tools/ce doctor --verbose
bash tools/ce diagnose --json
bash tools/ce diagnose --deep --json
```

If supported by the checked-out CLI, narrow investigation with `bash tools/ce diagnose --service NAME --json`. Confirm available flags from `bash tools/ce diagnose --help` rather than assuming an option exists.

Use `bash tools/ce diagnose --hook-smoke --json` when prompt-hook integration is relevant. Do not run `--session-end-smoke` unless the user explicitly authorizes its production Memory write via `--allow-memory-write`.

## Investigate failures

- Read the failing check's remediation, then inspect its implementation and actual runtime configuration.
- For systemd services, compare unit, PID, working directory, checkout revision, and logs. Do not equate an active process with a functioning request path.
- For HTTP failures, capture status, content type, and a safely truncated response body. A 200 response with malformed or unexpected content is not a successful contract check.
- For worker/arbiter failures, inspect the full subprocess output and wait result. A gateway health response does not prove that a review completed.
- Redact tokens and credentials. Never dump the full process environment.

## Safety and report

Diagnostics default to read-only. Do not edit configuration, restart services, write Memory, trigger GA, or alter production state without explicit authorization.

Return the exact commands and exit statuses, verified profile and checkout, passed/failed/skipped checks, evidence-backed root cause versus hypothesis, and the smallest safe remediation. Avoid unrelated CI/Sonar work unless explicitly requested.
