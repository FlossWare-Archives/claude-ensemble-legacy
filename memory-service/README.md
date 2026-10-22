# RH Memory Service

Central memory authority for all Claude Code sessions. Runs as a systemd user service and manages concurrent access to memory files across multiple simultaneous sessions.

## Architecture

```
[System Startup]
     ↓
[Memory Service Daemon] ← runs continuously
     ↑
     ├─ Claude Code Session 1 (connects via Unix socket)
     ├─ Claude Code Session 2 (connects via Unix socket)
     └─ Claude Code Session N (connects via Unix socket)
```

**Key properties:**
- **Single instance:** Only one memory service per user (systemd manages it)
- **Concurrent-safe:** All file operations protected by lock
- **Always available:** Auto-restarts on crash, survives machine reboot
- **Graceful degradation:** If service down, sessions continue with cached memory
- **Private IPC:** Socket lives under `$XDG_RUNTIME_DIR/claude-ensemble/memory.sock` when `XDG_RUNTIME_DIR` is available
- **Restricted permissions:** Runtime directory is `0700`; socket is `0600`
- **Validated names:** Memory names allow only letters, numbers, `.`, `_`, and `-`

## Installation

```bash
cd /path/to/repo
./memory-service/install.sh
```

This:
1. Symlinks `rh-memory.service` to `~/.config/systemd/user/`
2. Reloads systemd
3. Enables service (auto-start on login)
4. Starts service immediately

## Operations

**View service status:**
```bash
systemctl --user status rh-memory
```

**View logs:**
```bash
journalctl --user -u rh-memory -f
```

**Restart service:**
```bash
systemctl --user restart rh-memory
```

**Stop service:**
```bash
systemctl --user stop rh-memory
```

**Disable auto-start:**
```bash
systemctl --user disable rh-memory
```

## API

Services communicate via JSON over the private Unix socket `$XDG_RUNTIME_DIR/claude-ensemble/memory.sock` when `XDG_RUNTIME_DIR` is available. If it is unavailable, the service uses `~/.cache/claude-ensemble/memory.sock`. The fallback also uses a `0700` parent directory and `0600` socket permissions.

### Liveness, readiness, and index warm-up

- `GET /health` is liveness. It reports that the Memory process is responding and remains HTTP 200 while the search index warms.
- `GET /ready` is readiness. It returns HTTP 200 only after a complete index pass over a stable snapshot of the Markdown files; while warming or after an incomplete pass it returns HTTP 503 with per-file outcomes and index diagnostics.
- `ENSEMBLE_MEMORY_WARMUP_TIMEOUT_SECONDS` controls the warm-up readiness budget and defaults to 30 seconds. It must be finite and greater than zero. Exceeding it reports `timed_out` and logs a warning once; the service remains live, and readiness can recover if the active warm-up later completes.
- Startup warm-up retries incomplete or failed passes up to three times with bounded delays. If all attempts fail, Memory remains not-ready. The terminal `degraded`/`failed` state returns `retryable: false`; restart the Memory service after correcting the underlying problem. Requests never trigger an unbounded cold rebuild.
- Semantic and hybrid search requests in `not_started`, `warming`, `timed_out`, `degraded`, or `failed` state return HTTP 503 with `error_code: index_not_ready`. Only `warming` and `timed_out` responses are marked `retryable: true`, because the active warm-up can still complete.
- The gateway's `GET /api/v1/ready` uses Memory readiness, while `GET /api/v1/memory/health` remains a liveness probe.

Readiness is derived from per-file indexing outcomes and a stable directory membership snapshot, not from matching file counts. The first search after a restart should not perform a full corpus build on the request path. Files added or changed after startup are indexed on demand; readiness describes completion of the initial pass, not a guarantee that every later query has a fixed latency.

### Ping
```json
{"op": "ping"}
→ {"ok": true, "message": "pong"}
```

### Read
```json
{"op": "read", "name": "project_rh_arbitration"}
→ {"ok": true, "content": "...file contents..."}
```

### Write (overwrite)
```json
{"op": "write", "name": "my_memory", "content": "..."}
→ {"ok": true}
```

### Append (JSONL)
```json
{"op": "append", "name": "decisions", "entry": {"decision": "use arbitration", "date": "2026-09-26"}}
→ {"ok": true}
```

### List
```json
{"op": "list"}
→ {"ok": true, "files": ["project_rh_toolkit_activation", "feedback_model_selection", ...]}
```

### Search
```json
{"op": "search", "keywords": ["thompson", "routing"]}
→ {"ok": true, "results": [{"file": "project_rh_toolkit_activation", "matched_keywords": 2, "size_bytes": 1234}, ...]}
```

## Memory names

Memory names are deliberately conservative because they become filesystem path components.

Allowed:
```
[A-Za-z0-9._-]+
```

Rejected names include path separators and traversal forms such as `../escape`, `foo/bar`, and absolute paths.

## Client Usage

In `rh-tools-init.sh`:

```python
from rh_memory_service.memory_client import MemoryClient

# Connect (fails gracefully if service down)
client = MemoryClient()
if client.connect():
    content = client.read('project_rh_arbitration')
    results = client.search(['arbitration', 'phases'])
```

## Failure Modes

**Service crashes:**
- Systemd auto-restarts after 10 seconds
- Sessions that were connected degrade to read-only (cached at startup)
- Next request reconnects automatically

**Service stuck:**
```bash
systemctl --user restart rh-memory
```

**Socket stale:**
The service safely removes its own stale socket on startup. It refuses to remove an unexpected non-socket or socket owned by another user.

## Files

- `memory_service.py` — Daemon (Unix socket, file I/O, threading)
- `memory_client.py` — Client library for sessions
- `rh-memory.service.template` — Systemd user service template
- `install.sh` — Installation script
- `test_memory_service.py` — Socket, permission, and name-validation tests

## Thread Safety

All file operations use `threading.Lock()`:
- Concurrent reads: OK (multiple threads)
- Write + read: Serialized (lock blocks)
- Write + write: Serialized (lock blocks)

## Security boundary

The memory service is intended to be a per-user local service. The Unix socket is not exposed through the shared `/tmp` namespace, the socket and runtime directory use restrictive permissions, and memory names are validated before becoming filesystem paths.


## Idempotent event capture

For retryable event capture, use the loopback REST endpoint `POST /memory/append-once` with a stable `event_id` and an `entry` object:

```json
{
  "name": "claude_code_events",
  "event_id": "<stable-session-event-id>",
  "entry": {
    "event": "SessionEnd",
    "session_id": "<claude-session-id>"
  }
}
```

The response reports `stored` or `duplicate`. Reusing an event ID with different content returns HTTP 409; invalid requests return HTTP 400, and an unreadable or malformed existing JSONL log returns HTTP 500. A malformed existing JSONL record blocks the append rather than silently risking duplicate capture. Before returning either `stored` or `duplicate`, the service flushes and calls `fsync()` on the log file; if a previous write succeeded but its `fsync()` failed, a retry must successfully synchronize the file before it can acknowledge `duplicate`. This follows the filesystem's sync contract, but does not claim a cross-platform guarantee for newly created directory entries after sudden power loss. The digest represents the caller's entry plus `event_id`, before service metadata is added. Caller-supplied `timestamp` is preserved; `captured_at` records service capture time, and `timestamp` is populated from capture time only when the caller omitted it. `payload_sha256` is reserved for the service and is rejected if supplied by the caller. The lock protects threads within one `MemoryStore` instance; the supported deployment is one Memory service process owning a given JSONL log, not multiple independent processes writing the same file. The event ID must be derived from the originating Claude Code event, never the current time.
