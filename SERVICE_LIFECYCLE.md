# Service Lifecycle Ownership

## Ownership model

Claude Ensemble services are **systemd user services**. Each daemon owns its
own process lifecycle. There is no top-level Ensemble daemon that starts,
stops, or supervises sibling services.

This is intentional:

- systemd starts and restarts each service.
- A service may depend on another service through systemd dependency
  declarations.
- A service must not spawn a sibling daemon as part of normal startup.
- Clients communicate with services through their documented sockets or
  interfaces.
- Installation enables individual systemd user units; it does not create a
  second process supervisor.

## Current baseline

The verified recovery baseline contains these independently managed services:

| Service | Lifecycle owner |
| --- | --- |
| Memory | systemd user manager |
| Thompson | systemd user manager |
| Learning | systemd user manager |
| Alert | systemd user manager |
| Messenger | systemd user manager |
| Graph | systemd user manager |

Learning declares a systemd dependency on Thompson because Learning can use
Thompson for outcome updates. That dependency does not transfer process
ownership to Learning.

The verified baseline does **not** contain a separate Ensemble application
server. The Graph capability is now represented by its own standalone HTTP
service and systemd unit; it remains independent of every sibling service.

## Startup and shutdown semantics

Starting a service means starting that service's own process:

```text
systemd --user
    |
    +-- claude-memory.service
    +-- claude-thompson.service
    +-- claude-learning.service
    +-- claude-alert.service
    +-- claude-messenger.service
    +-- claude-graph.service
```

Stopping or restarting one service must not implicitly create a second copy
of another service.

Systemd remains responsible for restart policy, process termination, and
service ordering. Service code is responsible for its own socket/resource
cleanup when it receives termination.

## Health and status

Operators can inspect the state of each systemd user service without a separate Ensemble supervisor:

```bash
systemctl --user is-active claude-memory.service
systemctl --user status claude-memory.service
```

Use the same commands with the relevant unit name for Thompson, Learning, Alert,
and Messenger. `is-active` provides a concise active/inactive result; `status`
provides the unit state, recent lifecycle messages, and the process identifier
when available.

For services that expose a socket or other documented interface, service health
also includes availability of that interface. A running systemd unit is not by
itself proof that the application protocol is responding correctly.

Learning has a declared dependency on Thompson. If Thompson is unavailable,
Learning does not take ownership of Thompson or start a replacement process.
Systemd remains responsible for the declared dependency and restart behavior;
Learning must report failures from unavailable Thompson functionality rather
than silently creating another Thompson instance.

## Verification

`test/test_service_lifecycle.py` verifies the baseline service templates
for the ownership contract:

- each service template is a simple systemd service with its own
  `ExecStart`;
- no service template invokes `systemctl` or an orchestration wrapper;
- the only declared inter-service dependency in the baseline is Learning's
  dependency on Thompson;
- all six baseline services are represented, including Graph;
- Graph is loopback-only and has no sibling-process supervision path.

Installer path correctness remains a separate concern for #102.
Termination propagation and platform-specific process-group behavior remain
separate concerns for #94 and #96.


## Running without systemd

The supported installers and automatic restart policy require a working systemd
user manager. On Linux systems without one, CE does **not** install services,
start background processes, or supervise/restart them. A foreground/manual path
is available for development and explicit operator-managed sessions:

Run each command in its own terminal from the repository root. Start Thompson
before Learning; Learning can use Thompson for outcome updates. Start Alert
after Learning if you want alert checks to consume learning outcomes. Memory,
Messenger, and Graph can run independently.

```bash
python3 thompson-service/thompson_service.py
python3 memory-service/memory_service.py
python3 learning-service/learning_service.py
python3 alert_service/alert_service.py
CLAUDE_MESSENGER_SOCKET="$HOME/.cache/claude-messenger/claude-messenger.sock" \
  python3 session-messaging/messenger_service.py
ENSEMBLE_GRAPH_HOST=127.0.0.1 \
ENSEMBLE_GRAPH_PORT=8766 \
ENSEMBLE_GRAPH_STORE="$HOME/.local/share/claude-ensemble/graph.json" \
  python3 graph-service/graph_service.py
```

These commands are foreground processes, not a multi-service launcher. Keep each
terminal open and use Ctrl-C to stop its service. They do not enable startup on
login, restart crashed services, or load the optional systemd environment file
automatically. Export any required `ENSEMBLE_*` configuration in the terminal
before launching. Graph must remain bound to loopback; do not override its host
to a public interface.

### Diagnosing unavailable services

On systemd systems, inspect the specific unit and its logs:

```bash
systemctl --user status claude-thompson.service
journalctl --user -u claude-thompson.service -n 80 --no-pager
```

Without systemd, check the relevant Unix socket or Graph listener and read the
foreground process's stderr/stdout. Default socket locations are:

| Service | Default endpoint |
| --- | --- |
| Memory | `$XDG_RUNTIME_DIR/claude-ensemble/memory.sock`, or `~/.cache/claude-ensemble/memory.sock` |
| Thompson | `/tmp/claude-thompson.sock` |
| Learning | `/tmp/claude-learning.sock` |
| Alert | `/tmp/claude-alert.sock` |
| Messenger | `/run/user/$UID/claude-messenger/claude-messenger.sock` when `XDG_RUNTIME_DIR` is set; use the explicit override above otherwise |
| Graph | `127.0.0.1:8766` |

Socket paths can be overridden with the service's documented environment
variables. A missing endpoint means the corresponding service is unavailable;
CE clients do not silently start a replacement daemon. Client degradation is
operation-specific: for example, Learning returns an explicit unavailable/error
result. When Memory is unavailable, `MemoryClient.append()` returns `False`
if the entry is retained only in its process-local `offline_cache`; this does
not mean the append was persisted. The cache has no automatic replay or flush
path, so callers must retry the append after Memory recovers if the data is
still needed. Check return values and logs, start the service explicitly, and
verify persistence before relying on a retried operation.

This manual path is intended for development and operator-managed sessions. It
does not provide systemd-equivalent supervision or claim production lifecycle
parity on non-systemd platforms.
