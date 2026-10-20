# Claude Ensemble Service Lifecycle

Claude Ensemble's supported managed runtime is the REST gateway plus four application services: Memory, Thompson, Learning, and Graph. Alert and Messenger are legacy services and are not started by current installs.

## Managed units

- `claude-memory.service`
- `claude-thompson.service`
- `claude-learning.service`
- `claude-graph.service`
- `claude-ensemble.service` (REST gateway)

## Inspect runtime state

```bash
systemctl --user --no-pager --full status claude-ensemble.service claude-memory.service claude-thompson.service claude-learning.service claude-graph.service
tools/ce diagnose --deep --json
```

Use `journalctl --user -u UNIT -n 100 --no-pager` to inspect a specific unit. Diagnostics are bounded and do not call a model. The `--prompt-context-smoke` mode exercises automatic Memory/Knowledge and Graph retrieval through the installed prompt hook.

## Versioned deployment

The versioned installer stores immutable releases under `~/.FlossWare/claude-ensemble/releases/`, selects the active release with `current`, and keeps mutable profile/configuration/data under `shared/`. Run staging-only validation before activation:

```bash
ENSEMBLE_INSTALL_ROOT="$HOME/.FlossWare/ce-staging-test" ENSEMBLE_STAGING_ONLY=1 \
  bash tools/install-ce-versioned.sh 0.1 "$PWD"
```

Activation backs up managed and legacy unit files, stops the previous runtime, switches the active release, installs the five supported units, starts them in dependency order, and checks readiness. On upgrades, old `claude-alert.service` and `claude-messenger.service` units are stopped and their unit files removed. Shared data and immutable releases are preserved for rollback.

To inspect or recover a deployment, use the deployment helper from the active release:

```bash
~/.FlossWare/claude-ensemble/current/tools/ce-deployment status
~/.FlossWare/claude-ensemble/current/tools/ce-deployment recover
```

Uninstalling removes CE's five managed unit files and active-release pointer but preserves releases and shared data unless `--purge` is explicitly supplied. Ordinary `~/.claude/` is not CE's profile and must remain untouched by default.

See [`SERVICES_GUIDE.md`](SERVICES_GUIDE.md) for service responsibilities and [`docs/PROFILES_AND_BACKUPS.md`](docs/PROFILES_AND_BACKUPS.md) for profile isolation and recovery details.
