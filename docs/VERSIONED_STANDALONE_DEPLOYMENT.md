# Versioned standalone deployment

> **Host acceptance is still pending.** CI and mocked staging/rollback tests pass, but they do not prove this installation is safe on the target host. First run the staging-only procedure below and inspect the generated manifest and service units. Do not run normal install/activation until staging acceptance is recorded; activation requires a separate, explicit maintenance-window decision.

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

The normal installer stages a release without `.git` metadata, preserves profile files without overwriting existing shared files, writes a deployment manifest, and then activates the release. Activation stops and restarts the seven CE services and rewrites their user systemd units, so do not use normal install mode as a harmless staging test.

### Stage and validate without activation

Use staging-only mode first when testing a release on a host with running CE services:

```bash
TEST_ROOT="$HOME/.FlossWare/ce-staging-test"
ENSEMBLE_INSTALL_ROOT="$TEST_ROOT" \
ENSEMBLE_STAGING_ONLY=1 \
  bash tools/install-ce-versioned.sh 0.1 /path/to/clean/claude-ensemble
```

Staging-only mode requires an explicit `ENSEMBLE_INSTALL_ROOT` beneath `HOME` and a new or empty target root. It does not import the legacy CE profile, does not require an active systemd user manager, does not write host service unit files, does not create or switch `current`, and does not activate services. It copies the release, checks structural containment, and preflights all seven generated service units in a disposable directory with systemd commands replaced by a no-op. The preflight checks stable `current` paths and ensures service configuration, persistent data, logs, sockets, and runtime paths resolve under `shared/`. It then writes a manifest with validation status `staged` and exits. The developer-checkout symlink `claude-global-skills` (which points into a Red Hat checkout) is deliberately omitted from standalone releases; it is host-specific and must not be shipped or followed. Other unexpected external symlinks still fail validation. A failed structural validation removes the newly staged release and exits nonzero.

The test root must be separate from the live deployment. Inspect the staged release and manifest before running the normal installer against the actual deployment root. Staging validates the generated unit contract but is not proof that services start or that the gateway is healthy.

To rerun the generated-unit preflight explicitly against a staged release:

```bash
ENSEMBLE_INSTALL_ROOT="$TEST_ROOT" \
  bash "$TEST_ROOT/releases/0.1/tools/ce-deployment" validate 0.1
```

This command renders units into a temporary directory and uses no-op systemd commands. It must not write the host's unit directory, invoke the host systemd manager, or change `current`. Activation repeats the same preflight before it creates an activation journal, snapshots units, stops services, or switches the active release.

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
- `ce-deployment validate VERSION` renders and validates all seven service units in a temporary directory before any activation side effects. It checks stable `current` execution paths and deployment-owned shared state paths without invoking the real systemd manager.
- Activation repeats preflight before it creates the journal or stops services, then switches `current`, reruns the canonical installer through that stable path, configures deployment-owned service paths, restarts in dependency order, and checks the gateway readiness endpoint.
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
