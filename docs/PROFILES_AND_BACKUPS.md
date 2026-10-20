# Claude Code profiles, installation modes, and backups

This document is the operational contract for Claude Code profile isolation in Claude Ensemble (CE). The important rule is that **a profile is a configuration/authentication boundary, not merely a directory name**. Always verify which profile the running CLI and each CE service actually use.

## Supported installation modes

### Versioned standalone deployment (preferred)

Use the root entry points:

- First install: `bash install-standalone.sh MAJOR.MINOR [source-checkout]`
- Upgrade: `bash upgrade.sh MAJOR.MINOR [source-checkout]`
- Inspect active release: `bash "$HOME/.FlossWare/claude-ensemble/current/tools/ce-deployment" status`
- Roll back: `bash "$HOME/.FlossWare/claude-ensemble/current/tools/ce-deployment" rollback`
- Uninstall while preserving state: `bash uninstall.sh`
- Explicitly delete retained releases and state: `bash uninstall.sh --purge`

The default deployment root is `~/.FlossWare/claude-ensemble`. Release code is immutable under `releases/MAJOR.MINOR/`; `current` is the active-release symlink. Mutable CE state lives under `shared/`.

| State | Standalone location | Ownership |
|---|---|---|
| CE Claude Code settings/profile | `shared/claude-code/` | Mutable user-managed CE profile; preserved across upgrades |
| CE shared configuration | `shared/config/` | Deployment configuration |
| Memory/Learning/alert and other persistent data | `shared/data/` | Persistent CE state |
| Logs | `shared/logs/` | Operational evidence |
| Release source | `releases/MAJOR.MINOR/` | Immutable versioned code |
| Active release | `current -> releases/MAJOR.MINOR` | Deployment manager |
| Durable pre-operation backups | `~/.FlossWare/claude-ensemble-backups/` | User-owned recovery snapshots |

The ordinary `~/.claude/` directory is **not** the standalone CE profile. The standalone installer must not rewrite it, move its authentication, or register CE hooks there. Do not copy an entire profile between work and personal environments casually: that may transfer credentials and provider-specific configuration.

### Explicitly selected profile for tools and skill installation

Tools that install Claude Code skills/configuration accept `ENSEMBLE_CLAUDE_CODE_CONFIG_DIR`. For example:

```bash
ENSEMBLE_CLAUDE_CODE_CONFIG_DIR="$HOME/.config/claude-ensemble/acceptance-profile" \
  bash tools/install-ce-skills.sh
```

The aggregate installer is idempotent and preserves conflicting user-owned skill files. This variable selects the target for that invocation only. The versioned deployment manager separately configures both `ENSEMBLE_CLAUDE_CODE_CONFIG_DIR` (the CE contract) and `CLAUDE_CONFIG_DIR` (the native Claude Code profile override) on each managed service. Arbitrary direct commands, unrelated scheduled jobs, and interactive shells do not inherit the service environment automatically.

### Direct development/runtime installer

The root `install.sh` is the runtime installer used by the deployment manager and can also be run against a source checkout for development. In direct mode, its profile target is `ENSEMBLE_CLAUDE_CODE_CONFIG_DIR` when set, otherwise `~/.FlossWare/claude-ensemble-profile`. The versioned standalone deployment instead uses `~/.FlossWare/claude-ensemble/shared/claude-code/`. Neither mode modifies ordinary `~/.claude/` by default. Before migrating, the installer backs up legacy service data and copies missing Memory, Learning, Thompson, and Graph state into the selected isolated state directory; the original legacy paths are left untouched, and existing destination files are never overwritten.

Do not treat direct source-checkout installation and standalone deployment as interchangeable. The standalone workflow provides immutable releases, a stable active pointer, deployment manifests, health checks, and release rollback.

## Profile selection and verification

Before debugging authentication or provider selection, establish these facts for the exact process being tested:

1. Which `claude` executable is found on that process's `PATH`.
2. Which configuration/profile directory the process uses.
3. Which user owns the process and which environment variables systemd passes to it.
4. Whether the native `CLAUDE_CONFIG_DIR` and CE `ENSEMBLE_CLAUDE_CODE_CONFIG_DIR` both point to the intended directory for that process.
5. Whether authentication exists in that profile and is valid for the configured provider.
6. Whether the service or scheduled job uses the same profile path as the interactive command.
7. Whether `settings.json` contains only the expected CE-managed hooks/settings and preserves unrelated user settings.

Do not infer profile selection from the shell's current environment when checking a systemd unit or cron job. Inspect the generated unit and the scheduled command. Use CE's doctor/diagnose tooling for supported read-only checks; do not paste credentials or full authentication files into logs, issues, or review comments.

For provider acceptance, keep separate profiles for separate authentication contracts. A Vertex-backed Claude Code profile and a first-party Anthropic profile are not interchangeable merely because both launch the `claude` executable. Record the expected provider in the acceptance profile's test configuration and verify the actual provider/auth state before running any review. Never send Red Hat proprietary source to an external reviewer provider.

## Backup contract

Every **non-staging versioned first install or upgrade** must create a durable backup before profile migration, release creation, or service activation. The operation must stop if the backup cannot be completed.

Backups are stored outside the deployment root at:

```
~/.FlossWare/claude-ensemble-backups/<MAJOR.MINOR>_<YYYY-MM-DD_HH-MM-SS-mmm>/
```

The timestamp is UTC and includes milliseconds. If a name collision still occurs, the backup creator appends a numeric collision suffix rather than overwriting an existing snapshot. The version is taken from the explicit release version supplied by the versioned installer. For other callers, an existing validated `current -> releases/MAJOR.MINOR` pointer determines the version being protected; a first-install backup with no active pointer resolves the next MAJOR.MINOR version from the source checkout and deployment state. If neither source nor active release state can establish a version, backup creation fails closed instead of creating an ambiguously named snapshot. The manifest records both the caller's reason and the resolved version.

Each backup contains:

- A copy of the complete existing versioned deployment tree, when one exists. This includes releases and mutable shared state.
- The source-checkout CE profile at `~/.FlossWare/claude-ensemble-profile`, when present.
- The isolated source-checkout service state at `~/.FlossWare/claude-ensemble-state`, when present.
- Copies of the five current CE service units and any legacy Alert/Messenger unit files that existed before the operation.
- A private manifest recording the operation and what was captured.

Backups are private to the current user (directory mode 0700; manifest mode 0600) because profile and shared-state copies may contain sensitive material. Keep the backup directory outside the deployment root so a normal uninstall or purge of the deployment cannot silently erase the recovery copy. Backups are not automatically pruned; delete one only after verifying recovery and deciding the retention policy.

The backup is a filesystem recovery point, not a guarantee that an external provider session remains valid forever. It does not back up remote services, remote databases, or state stored outside the CE deployment and the explicitly listed CE state, unit, and profile paths.

Staging-only mode deliberately does not create a host-state backup because it must not alter host state. A backup failure in a real install/upgrade is a hard stop; do not bypass it to get the deployment moving.

## Recovery

1. Stop before retrying if an install/upgrade reports that its backup or activation failed.
2. Preserve the printed backup path. Do not purge the deployment or delete the backup.
3. Inspect the backup manifest and verify the snapshot contains the expected deployment/profile/unit files.
4. For release activation failures, use the deployment manager's recovery journal procedure first; an activation rollback and a pre-operation backup serve different purposes.
5. If filesystem restoration is required, stop the CE user services first, preserve the failed state for diagnosis, then restore only the affected paths from the snapshot. Restore the deployment tree and managed unit files as a coordinated operation; do not blindly overwrite the ordinary `~/.claude` directory.
6. Verify active release, service health, gateway readiness, and the actual profile used by each process before considering recovery complete.

## Important distinction

- **Release rollback** switches to the previous known-good immutable release.
- **Activation recovery** reconciles an interrupted switch using the activation journal.
- **Pre-operation backup restore** recovers filesystem state that an install/upgrade may have changed.

These are related safety layers, not synonyms. Keep the backup until the new release has passed host acceptance and its profile/provider behavior has been verified.
