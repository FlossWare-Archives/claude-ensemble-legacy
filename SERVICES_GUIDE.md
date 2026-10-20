# Claude Ensemble Service Guide

Claude Ensemble manages **four application services plus its REST gateway**. This is intentionally smaller than the reusable service platform planned for Loom.

## Managed runtime

| Component | Responsibility | Default endpoint |
|---|---|---|
| Gateway (`claude-ensemble.service`) | REST entry point and orchestration integration | `http://127.0.0.1:8080` |
| Memory (`claude-memory.service`) | Persistent episodic memory and durable Knowledge retrieval | `http://127.0.0.1:8767` |
| Graph (`claude-graph.service`) | Relationships between projects, decisions, and documents | `http://127.0.0.1:8766` |
| Thompson (`claude-thompson.service`) | Model/workflow selection statistics and recommendations | Deployment-managed Unix socket |
| Learning (`claude-learning.service`) | Evidence-backed outcome capture and learner updates | Deployment-managed Unix socket |

For a versioned install, CE owns its mutable profile and service state beneath
`~/.FlossWare/claude-ensemble/shared/`. In particular, the dedicated Claude Code
profile is `shared/claude-code/`; the normal `~/.claude/` profile is not used by
default. Source-checkout installs use the isolated
`~/.FlossWare/claude-ensemble-profile/` profile and keep service data under
`~/.FlossWare/claude-ensemble-state/` unless explicitly overridden.

## Source-checkout development mode

For an isolated Python virtualenv and foreground services, run:

```bash
tools/ce dev setup
```

Then, in each service terminal, load the same profile and runtime environment and start one process:

```bash
eval "$(tools/ce dev env)"
tools/ce dev run memory
```

Repeat with `thompson`, `learning`, and `graph` in separate terminals; start `gateway` last. Setup installs the hook configuration into the dedicated CE profile. This mode uses alternate loopback ports and does not modify ordinary `~/.claude/`.

## Status and diagnostics

Check the service units:

```bash
systemctl --user status claude-ensemble.service
systemctl --user status claude-memory.service
systemctl --user status claude-graph.service
systemctl --user status claude-thompson.service
systemctl --user status claude-learning.service
```

Run the bounded diagnostics:

```bash
tools/ce diagnose --json
tools/ce diagnose --deep --json
tools/ce diagnose --prompt-context-smoke --json
```

The prompt-context smoke exercises the installed hook against live Memory and
Graph services and requires at least one durable Knowledge result. It does not
invoke a model, so it proves retrieval and hook output, not that Claude received
or acted on the injected context. SessionEnd capture is optional, metadata-only,
and writes directly to Memory using append-once semantics.

## Lifecycle

The versioned deployment installer manages the gateway and four application
services as one unit set. Upgrades stop the previous runtime, activate a
validated release, start the managed units in dependency order, and check
readiness. Existing Alert and Messenger units from older CE installations are
stopped and their managed unit files retired during activation. Their source
directories may remain in the repository for reference, but they are not part
of the supported CE runtime.

For rollback and uninstall details, see
[`SERVICE_LIFECYCLE.md`](SERVICE_LIFECYCLE.md) and
[`docs/PROFILES_AND_BACKUPS.md`](docs/PROFILES_AND_BACKUPS.md).
