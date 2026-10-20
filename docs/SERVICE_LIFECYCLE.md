# Manual foreground service lifecycle

Claude Ensemble's managed deployment uses seven systemd user units. On an environment without a systemd user manager, the supported fallback is to run each service directly in the foreground, one terminal per service. There is no automatic daemonization, shell-startup hook, nohup, or hidden background process.

This procedure is for a source checkout and an explicitly chosen private state directory. It does not modify the user's ordinary ~/.claude profile. Use a disposable state root first; do not run this against a live deployment while host acceptance #505 is gated.

## Prepare an isolated runtime

Use an absolute path for CE_CODE and CE_STATE_ROOT. The state root must be separate from the source checkout and from ~/.claude.

~~~bash
export CE_CODE="/absolute/path/to/claude-ensemble"
export CE_STATE_ROOT="$HOME/.FlossWare/ce-manual-runtime"

mkdir -p "$CE_STATE_ROOT"/{runtime,logs,claude-code,data/memory,data/learning,data/alerts}
chmod 700 "$CE_STATE_ROOT" "$CE_STATE_ROOT/runtime" "$CE_STATE_ROOT/claude-code"

export ENSEMBLE_REPO_ROOT="$CE_CODE"
export ENSEMBLE_RUNTIME_DIR="$CE_STATE_ROOT/runtime"
export ENSEMBLE_LOG_DIR="$CE_STATE_ROOT/logs"
export ENSEMBLE_CLAUDE_CODE_CONFIG_DIR="$CE_STATE_ROOT/claude-code"
export CLAUDE_CONFIG_DIR="$CE_STATE_ROOT/claude-code"

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
export ENSEMBLE_ALERT_DIR="$CE_STATE_ROOT/data/alerts"
export ENSEMBLE_ALERT_SOCKET="$CE_STATE_ROOT/runtime/alert.sock"
export CLAUDE_MESSENGER_SOCKET="$CE_STATE_ROOT/runtime/messenger.sock"

export ENSEMBLE_HTTP_HOST=127.0.0.1
export ENSEMBLE_HTTP_PORT=18080
~~~

These non-default loopback ports reduce the chance of colliding with a separately managed CE instance. The gateway is configured to use the Memory and Graph URLs above. Do not expose these development services on a non-loopback interface.

## Start services in separate terminals

Run each command in its own terminal after exporting the same environment in each terminal (or use a private shell environment file that you have reviewed). Keep each process in the foreground.

1. Memory, required for prompt retrieval and operational memory:

   ~~~bash
   python3 "$CE_CODE/memory-service/memory_service.py"
   ~~~
2. Thompson, required for the Learning outcome path:

   ~~~bash
   python3 "$CE_CODE/thompson-service/thompson_service.py"
   ~~~
3. Learning:

   ~~~bash
   python3 "$CE_CODE/learning-service/learning_service.py"
   ~~~
4. Graph, used for project relationships:

   ~~~bash
   python3 "$CE_CODE/graph-service/graph_service.py"
   ~~~
5. Alert:

   ~~~bash
   python3 "$CE_CODE/alert_service/alert_service.py"
   ~~~
6. Messenger:

   ~~~bash
   python3 "$CE_CODE/session-messaging/messenger_service.py"
   ~~~
7. REST gateway, start last:

   ~~~bash
   python3 "$CE_CODE/server/ensemble_server.py"
   ~~~

Stop a service with Ctrl-C in its own terminal. Stop the gateway first, then Messenger/Alert/Graph/Learning/Thompson/Memory. A service that exits or fails to bind is not running; do not treat a surviving process or a successful health response from another service as proof that the entire stack is ready.

## Diagnose missing services

From the same environment, run:

~~~bash
bash "$CE_CODE/tools/ce" doctor --json
bash "$CE_CODE/tools/ce" diagnose --json
bash "$CE_CODE/tools/ce" diagnose --deep --json
~~~

Use the JSON checks or results entries to identify the exact unavailable dependency and remediation. A failed required operation returns a nonzero status. Warnings and skipped optional checks remain explicit; they are not silently reported as successful operations. The --deep option exercises the isolated Learning-to-Thompson path using temporary state, while --session-end-smoke is different: it writes one synthetic metadata event to Memory and requires the explicit --allow-memory-write option. Do not use that option merely to diagnose a missing service.

Automated coverage includes unavailable HTTP/socket checks and injected partial failures in scripts/test_ce_diagnose.py, tests/test_ce_diagnose.py, and the hook/diagnostic regression tests. These validate error reporting and fail-open prompt behavior; they do not replace testing the selected machine's environment.

## Limits

- Foreground mode is manual orchestration, not a replacement for systemd restart policies, boot startup, or service supervision.
- Do not start a second copy against the same state directory or ports.
- A missing systemd user manager is not itself proof that service logic is unavailable; run the service directly and use diagnostics to check its actual dependencies.
- Do not use the ordinary ~/.claude profile or default service data paths for a no-systemd experiment.
- Versioned host staging and activation remain gated by [issue #505](https://github.com/FlossWare/claude-ensemble/issues/505).
