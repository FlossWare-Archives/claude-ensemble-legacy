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

## Hybrid retrieval

Natural-language searches use hybrid retrieval by default: SQLite-backed BM25
plus local semantic embeddings. SQLite is an embedded file, not another service;
the index is stored beside the memory documents as
`.hybrid-retrieval.sqlite3` and is rebuilt incrementally as documents change.
Markdown is chunked at heading and paragraph boundaries, with heading paths
retained as retrieval metadata. Structured identifiers continue through the
existing exact-identifier path. The two indexes are intentionally split for now:
the in-memory lexical cache preserves the exact identifier path, while SQLite
owns persistent BM25 and semantic retrieval for natural-language prose. Warm-up
and Memory writes update both; prompt-time searches do not re-embed documents.

The default local embedding model is
`sentence-transformers/all-MiniLM-L6-v2`. It runs through FastEmbed, which uses
quantized ONNX Runtime without importing PyTorch, to keep resident memory within
the Memory service's 512 MiB limit. The standard Claude Ensemble dependency
installation includes FastEmbed, and model weights are acquired automatically
on first use if they are not already cached. FastEmbed supports this model ID
and produces the same 384-dimensional vector shape used by the persistent index.
Set `ENSEMBLE_MEMORY_EMBEDDING_MODEL` only when you need to select another
model supported by FastEmbed. Normal operation does not require that setting.

Existing lexical indexes are backfilled during startup warm-up and as
documents are written or ingested. Restart the Memory service to incorporate
external Markdown edits or to retry a failed index write; prompt-time prose
search deliberately does not repair the index. The companion reindex-operations
change adds explicit full, incremental, and embedding-only maintenance commands.
Vectors and model identity are stored with each chunk in SQLite. The first model
load and initial backfill take longer; later searches reuse stored document
vectors. Prompt-time searches read the persisted SQLite index and embed only the
query, not the document corpus.

The model is local inference. Memory content is not sent to an external
embedding API.

For disconnected environments, provide a local wheelhouse and pre-cache the
selected FastEmbed model before enabling offline mode. The versioned installer
supports an offline package path through pip's standard PIP_NO_INDEX=1 and
PIP_FIND_LINKS=/path/to/wheelhouse settings, and
ENSEMBLE_SKIP_MODEL_PROVISION=1 skips model acquisition during installation.
Set HF_HUB_OFFLINE=1 to forbid Hugging Face network access. The selected model
must already exist in FastEmbed's local cache. For a source
checkout whose .venv is already provisioned, ENSEMBLE_SKIP_PYTHON_PROVISION=1
skips all package installation and requires an existing .venv/bin/python.

Activation, recovery, and rollback reuse the immutable release runtime. They do
not run pip or download model weights.

The real-model acceptance test is part of the required Memory test suite.
It uses the configured FastEmbed model ID from
`ENSEMBLE_MEMORY_EMBEDDING_MODEL`, or the default
`sentence-transformers/all-MiniLM-L6-v2`. The test is not skipped by default
and requires model download access on a clean CI runner.

The required acceptance test verifies semantic discrimination against lexical
and topical distractors, persisted-vector reuse after constructing a fresh
index instance, and concurrent query fan-out. It prints maximum query latency
and process peak RSS, and fails against configurable budgets:

- `ENSEMBLE_MEMORY_ACCEPTANCE_LATENCY_SECONDS` (default `1.5`)
- `ENSEMBLE_MEMORY_ACCEPTANCE_MEMORY_KIB` (default `524288`, Linux `ru_maxrss` units)

Run it on the installed host with the same local model and under the actual
Memory service resource limits. The standalone test process cannot prove that
systemd's `MemoryLimit=512M` and `CPUQuota=50%` are enforced merely because its
assertions pass; retain the service-level multi-query acceptance as a deployment
gate, including first-start warm-up timing and a restart against persisted vectors.

## Operator-controlled reindex and embedding backfill

The Memory service includes a repeatable maintenance CLI that processes existing Markdown files, not just memories created after hybrid retrieval was introduced. It can rebuild chunks, repair missing embeddings, or regenerate embeddings for already-indexed chunks. Run it from the installed release with the same memory directory and embedding-model configuration used by the service.

Stop the service before maintenance to avoid concurrent writes and stale in-process caches. Preview first:

```bash
CE_ROOT="$HOME/.FlossWare/claude-ensemble"
cd "$CE_ROOT/current"
systemctl --user stop claude-memory.service
trap 'systemctl --user start claude-memory.service' EXIT
export ENSEMBLE_MEMORY_DIR="$CE_ROOT/shared/data/memory"

"$CE_ROOT/current/.venv/bin/python" memory-service/memory_service.py \\
  --reindex full --source all --dry-run

# After reviewing the JSON plan, rebuild all eligible existing files:
"$CE_ROOT/current/.venv/bin/python" memory-service/memory_service.py \\
  --reindex full --source all
```

Modes are `full` (rebuild chunks and embeddings), `incremental` (changed files and incomplete embeddings), and `embeddings` (regenerate vectors without changing existing chunk boundaries). Sources can be `all`, `memory`, or `claude-code`. The command emits JSON counts and per-document progress. Do not proceed if the report contains failures or planning errors. Keep the Markdown source files intact; the SQLite index is derived data.

Embedding generation uses the configured local Sentence Transformers model. Ensure the model is installed/cached and that any service-specific `ENSEMBLE_MEMORY_EMBEDDING_MODEL` override is exported in the maintenance environment. The dry-run does not load the model or modify the index.

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
- If a durable Markdown write succeeds but SQLite/vector indexing fails, Memory records the file and error in `.hybrid-index-pending.json`. The readiness response becomes `degraded` and search fails closed until the pending work succeeds. Repeating the write/ingest retries indexing; restarting Memory retries the full startup warm-up. A successful file write alone is not proof that its content is searchable.
- The gateway's `GET /api/v1/ready` uses Memory readiness, while `GET /api/v1/memory/health` remains a liveness probe.

Readiness is derived from per-file indexing outcomes and a stable directory membership snapshot, not from matching file counts. The first search after a restart should not perform a full corpus build on the request path. Files written through the Memory service are indexed at write time. External edits are incorporated during the next startup warm-up or explicit maintenance reindex. Prompt-time retrieval does not rebuild document chunks or write the hybrid index; readiness describes completion of the initial pass, not a guarantee that every later query has a fixed latency.

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
