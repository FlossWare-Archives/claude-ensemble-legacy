---
name: ce-status
description: Diagnose Claude Ensemble services, profile wiring, hooks, and the live worker/arbiter path. Use when asked whether CE is healthy, why a CE skill is missing or failing, or which CE service is broken.
---

# Claude Ensemble status and diagnostics

Use this skill for evidence-based diagnosis. Do not claim a service or workflow works just because a process exists.

## 1. Identify the active profile and checkout

1. Record the current working directory.
2. Resolve the active Claude Code profile from `ENSEMBLE_CLAUDE_CODE_CONFIG_DIR`, then `CLAUDE_CONFIG_DIR`, then `~/.claude`. Do not assume the default profile is active.
3. Locate the CE checkout. Prefer `ENSEMBLE_REPO_ROOT`; otherwise inspect `systemctl --user cat claude-ensemble.service` and verify a candidate contains `tools/ce`, `scripts/ce-diagnose.py`, and a Git remote for `FlossWare/claude-ensemble`.
4. Never modify or replace profile files during diagnosis. Do not use the legacy installer as a diagnostic shortcut.

## 2. Run read-only diagnostics

From the verified checkout, run and capture exit status plus complete output:

```bash
bash tools/ce doctor --verbose
bash tools/ce diagnose --deep --json
```

If a specific service is failing, narrow the diagnostic with `bash tools/ce diagnose --service NAME --json`. Use `bash tools/ce diagnose --hook-smoke --json` only when checking installed UserPromptSubmit integration. Do not run `--session-end-smoke` unless the user explicitly authorizes the production Memory write required by that test.

Check the gateway independently:

```bash
curl -fsS --max-time 5 http://127.0.0.1:8080/api/v1/health
```

Use the actual configured URLs if CE configuration specifies alternatives; do not treat a hard-coded default as authoritative when overridden.

## 3. Diagnose profile integration

- Check whether the selected profile has `settings.json`, expected hook registrations, and the installed CE skill files under `skills/<skill-name>/SKILL.md`.
- Confirm that each skill's frontmatter `name` matches its directory and slash command.
- Distinguish skill discovery from skill execution. A visible slash command proves Claude Code discovered a file, not that the subprocess, gateway, workers, or arbiter completed successfully.
- Preserve unrelated settings and user skills. Do not overwrite, delete, or repair files without a separate, explicit change request.

## 4. Investigate failures, do not paper over them

For a failing diagnostic, inspect the relevant route, client, service implementation, and current runtime configuration before proposing a fix. If HTTP returns 200 but the body is not the expected JSON, capture content type and a safely truncated response body; then compare the live service's unit, PID, working directory, and checkout revision with the source being inspected. Do not infer that the endpoint is healthy from the status code alone.

Never print secrets, access tokens, or full environment dumps. Redact credential values from logs.

## 5. Report

Return:
- selected profile and verified CE checkout
- exact commands and exit statuses
- checks that passed and failed
- evidence-based likely cause, distinguishing fact from hypothesis
- smallest safe next action

Do not report success when a diagnostic failed, timed out, returned malformed data, or skipped a check. Avoid unrelated CI/Sonar work unless explicitly requested.
