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

## Run from a source checkout without systemd

CE has a venv-based foreground mode for development and hosts without a systemd user manager. It uses the isolated Claude profile `~/.FlossWare/claude-ensemble-profile`, separate service state under `~/.FlossWare/claude-ensemble-state`, and a virtualenv at `~/.FlossWare/venvs/claude-ensemble`; it does not change ordinary `~/.claude`.

From the source checkout, set up the environment once:

```bash
tools/ce dev setup
```

In each service terminal, load the same isolated environment and run one service. Start Memory, Thompson, Learning, and Graph before the gateway:

```bash
eval "$(tools/ce dev env)"
tools/ce dev run memory
```

Use `tools/ce dev run thompson`, `tools/ce dev run learning`, `tools/ce dev run graph`, and `tools/ce dev run gateway` in separate terminals. The development mode uses loopback ports 18767 (Memory), 18766 (Graph), and 18080 (gateway) by default, avoiding the normal installed ports. Override them with `ENSEMBLE_CE_DEV_MEMORY_PORT`, `ENSEMBLE_CE_DEV_GRAPH_PORT`, or `ENSEMBLE_CE_DEV_GATEWAY_PORT` if needed.

In each terminal, use Ctrl-C to stop its service. CE clients do not silently start a replacement daemon. If Memory is unavailable, `MemoryClient.append()` returns `False`; there is no automatic replay or flush. Start the missing service and retry the operation explicitly. Do not start a second process against the same state directory or ports.

See [`SERVICES_GUIDE.md`](SERVICES_GUIDE.md) for service responsibilities and [`docs/PROFILES_AND_BACKUPS.md`](docs/PROFILES_AND_BACKUPS.md) for profile isolation and recovery details.
