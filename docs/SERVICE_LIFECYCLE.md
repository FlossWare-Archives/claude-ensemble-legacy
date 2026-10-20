# Manual foreground service lifecycle

Claude Ensemble's supported runtime is the REST gateway plus Memory, Thompson, Learning, and Graph. Alert and Messenger are legacy components, not required CE services.

## Prepare an isolated development environment

From a source checkout, create the Python virtualenv and isolated state/profile root:

```bash
tools/ce dev setup
```

The virtualenv lives at `~/.FlossWare/venvs/claude-ensemble`; the profile lives at `~/.FlossWare/claude-ensemble-profile` and mutable service data lives at `~/.FlossWare/claude-ensemble-state`. Ordinary `~/.claude/` is not modified by default.

## Start the services

Run one command per terminal, loading the same environment each time. Start the four application services first, then the gateway:

```bash
eval "$(tools/ce dev env)"
tools/ce dev run memory
```

Repeat with `tools/ce dev run thompson`, `tools/ce dev run learning`, and `tools/ce dev run graph` in separate terminals. Start the REST gateway last with `tools/ce dev run gateway`.

The development mode uses loopback ports 18767 (Memory), 18766 (Graph), and 18080 (gateway) by default. These differ from the installed runtime ports to avoid accidental collisions. Set `ENSEMBLE_CE_DEV_MEMORY_PORT`, `ENSEMBLE_CE_DEV_GRAPH_PORT`, or `ENSEMBLE_CE_DEV_GATEWAY_PORT` before evaluating the environment to override them.

In each terminal, use Ctrl-C to stop its service. This is explicit foreground execution, not daemon supervision. CE clients do not silently start a replacement daemon. If Memory is unavailable, `MemoryClient.append()` returns `False`; there is no automatic replay or flush. Start the missing service and retry the operation explicitly.

Do not start a second process against the same state directory or ports. For managed systemd installation, upgrades, rollback, and uninstall, see [`../SERVICE_LIFECYCLE.md`](../SERVICE_LIFECYCLE.md).
