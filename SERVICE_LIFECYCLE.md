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

Use this mode for development or hosts without a systemd user manager. It runs processes in the foreground and does not change the normal `~/.claude` profile.

```bash
export CE_CODE="$HOME/src/claude-ensemble"
export CE_STATE_ROOT="$HOME/.FlossWare/claude-ensemble-profile"
export CE_VENV="$HOME/.FlossWare/venvs/claude-ensemble"

python3 -m venv "$CE_VENV"
"$CE_VENV/bin/python" -m pip install -r "$CE_CODE/requirements.txt"
mkdir -p "$CE_STATE_ROOT"/{runtime,logs,data/memory,data/learning}
chmod 700 "$CE_STATE_ROOT" "$CE_STATE_ROOT/runtime"

export ENSEMBLE_REPO_ROOT="$CE_CODE"
export ENSEMBLE_RUNTIME_DIR="$CE_STATE_ROOT/runtime"
export ENSEMBLE_LOG_DIR="$CE_STATE_ROOT/logs"
export ENSEMBLE_MEMORY_DIR="$CE_STATE_ROOT/data/memory"
export ENSEMBLE_MEMORY_SOCKET="$CE_STATE_ROOT/runtime/memory.sock"
export ENSEMBLE_MEMORY_HTTP_HOST=127.0.0.1
export ENSEMBLE_MEMORY_HTTP_PORT=18767
export FLOSSWARE_MEMORY_URL=http://127.0.0.1:18767
export ENSEMBLE_MEMORY_URL=http://127.0.0.1:18767
export ENSEMBLE_GRAPH_STORE="$CE_STATE_ROOT/data/graph.json"
export ENSEMBLE_GRAPH_HOST=127.0.0.1
export ENSEMBLE_GRAPH_PORT=18766
export ENSEMBLE_GRAPH_URL=http://127.0.0.1:18766
export ENSEMBLE_LEARNING_DIR="$CE_STATE_ROOT/data/learning"
export ENSEMBLE_LEARNING_SOCKET="$CE_STATE_ROOT/runtime/learning.sock"
export ENSEMBLE_THOMPSON_STATE_FILE="$CE_STATE_ROOT/data/thompson-sampling-state.json"
export ENSEMBLE_THOMPSON_SOCKET="$CE_STATE_ROOT/runtime/thompson.sock"
export ENSEMBLE_HTTP_HOST=127.0.0.1
export ENSEMBLE_HTTP_PORT=18080
export ENSEMBLE_GATEWAY_URL=http://127.0.0.1:18080
```

Run each command in a separate terminal after loading the same environment. Start Memory, Thompson, Learning, and Graph first, then the gateway:

```bash
cd "$CE_CODE"
"$CE_VENV/bin/python" memory-service/memory_service.py
"$CE_VENV/bin/python" thompson-service/thompson_service.py
"$CE_VENV/bin/python" learning-service/learning_service.py
"$CE_VENV/bin/python" graph-service/graph_service.py
"$CE_VENV/bin/python" server/ensemble_server.py
```

If the dependencies are already installed in the active Python environment, the equivalent service entry points are `python3 memory-service/memory_service.py`, `python3 thompson-service/thompson_service.py`, `python3 learning-service/learning_service.py`, and `python3 graph-service/graph_service.py`. No Alert or Messenger process is required. In each terminal, use Ctrl-C to stop its service. CE clients do not silently start a replacement daemon.

If Memory is unavailable, `MemoryClient.append()` returns `False`; there is no automatic replay or flush. Start the missing service and retry the operation explicitly. Do not start a second process against the same state directory or ports.

See [`SERVICES_GUIDE.md`](SERVICES_GUIDE.md) for service responsibilities and [`docs/PROFILES_AND_BACKUPS.md`](docs/PROFILES_AND_BACKUPS.md) for profile isolation and recovery details.
