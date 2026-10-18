# Versioned standalone deployment

> **Preview:** the versioned installer is under active development. Do not use it for a production deployment until the activation, rollback, and migration checks in PR #507 pass on the target host.

## Layout

The default root is `~/.FlossWare/claude-ensemble`. Set `ENSEMBLE_INSTALL_ROOT` to choose another directory beneath your home directory.

```text
~/.FlossWare/claude-ensemble/
├── current -> releases/0.2/
├── releases/
│   ├── 0.0/                 # only created when migrating an old standalone install
│   ├── 0.1/
│   └── 0.2/
├── shared/
│   ├── claude-code/         # CE profile, separate from ~/.claude
│   ├── config/
│   ├── data/
│   └── logs/
└── deployment.json
```

Release trees are intended to be immutable. The systemd unit files use the stable logical path `~/.FlossWare/claude-ensemble/current`, not a specific release directory or a development checkout. `current` is changed using an atomic symlink replacement while the CE services are stopped.

The internal `0.0` release label is reserved for a snapshot of a migrated unversioned deployment. It is a rollback baseline, not a published CE release version.

## Install or upgrade

Choose a new MAJOR.MINOR version for each deploy. Do not overwrite an existing release.

From a development checkout:

```bash
bash tools/install-ce-versioned.sh 0.1 /path/to/claude-ensemble
```

To install the published `main` branch archive:

```bash
curl -fsSL https://raw.githubusercontent.com/FlossWare/claude-ensemble/main/tools/install-ce-versioned.sh \
  -o /tmp/install-ce-versioned.sh
bash /tmp/install-ce-versioned.sh 0.1
```

The installer stages a release without `.git` metadata, preserves profile files without overwriting existing shared files, writes a deployment manifest, and activates the release. It does not modify the ordinary `~/.claude` profile.

## Inspect and roll back

Run the lifecycle helper from the active release:

```bash
bash ~/.FlossWare/claude-ensemble/current/tools/ce-deployment status
# If status warns about an interrupted activation, recover before any new activation/prune:
bash ~/.FlossWare/claude-ensemble/current/tools/ce-deployment recover
bash ~/.FlossWare/claude-ensemble/current/tools/ce-deployment rollback
```

Rollback chooses the highest retained release other than the active release. Check the status output before pruning.

```bash
bash ~/.FlossWare/claude-ensemble/current/tools/ce-deployment prune
```

Pruning keeps the active release and one other retained release. It should only be run after verifying the active services and rollback candidate.

## Safety behavior

- A release label must use MAJOR.MINOR format.
- Existing releases are never overwritten.
- A development checkout containing `.git` is not accepted as the install root.
- Activation stops all seven CE services, switches `current`, reruns the canonical installer through that stable path, verifies generated unit paths, restarts in dependency order, and checks the gateway readiness endpoint.
- Before stopping services, activation writes a durable journal and unit/state backup under `shared/activation-backups/`. If the process is interrupted, `status` warns and `recover` restores the previous release (or original unit files on first install). Activation and pruning refuse to proceed while recovery is pending.
- If activation fails after a previous release was active, the helper attempts to restore the previous pointer and restart that release.
- Existing legacy profile files are copied without overwriting shared files. Original legacy directories are retained for manual review.
- If rollback itself fails, inspect the systemd user journal and do not prune either release.

Diagnostics:

```bash
systemctl --user --no-pager --full status \
  claude-memory.service claude-thompson.service claude-learning.service \
  claude-alert.service claude-messenger.service claude-graph.service \
  claude-ensemble.service
journalctl --user -u claude-ensemble.service -n 100 --no-pager
```
