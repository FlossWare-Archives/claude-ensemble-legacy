# Canonical Claude Code hooks

Claude Ensemble's supported Claude Code integration uses two managed hooks. The canonical installer registers them in the explicitly selected CE profile; it does not install the legacy workflow/learning scripts described in older revisions of this document.

## Supported managed hooks

### `UserPromptSubmit`: `memory-search-on-prompt.js`

Retrieves relevant context before a prompt is submitted:

- Searches the Memory REST service for relevant episodic Memory and durable Knowledge records.
- Optionally adds bounded, one-hop project Graph context when Claude Code supplies a working directory.
- Deduplicates results and bounds context size.
- Returns context through Claude Code's `hookSpecificOutput.additionalContext` contract.
- Fails open when a service is unavailable; the hook does not invoke a model, write Memory, submit learning outcomes, or send notifications.

Configuration is via environment variables inherited by the hook process:

| Variable | Default | Purpose |
|---|---|---|
| `FLOSSWARE_MEMORY_URL` | `http://127.0.0.1:8767` | Memory/Knowledge REST endpoint |
| `FLOSSWARE_MEMORY_TIMEOUT_MS` | `1500` | Memory request timeout |
| `FLOSSWARE_GRAPH_URL` | `http://127.0.0.1:8766` | Project Graph REST endpoint |
| `FLOSSWARE_GRAPH_TIMEOUT_MS` | `400` | Graph request timeout |

Context and result limits are intentionally bounded in the hook implementation. Changing those limits requires an explicit code change and regression tests; they are not currently user configuration options.

### `SessionEnd`: `session-end-memory-capture.js`

Captures stable, non-transcript session metadata:

- Appends one idempotent event to Memory using the append-once REST contract.
- Publishes a transient metadata-only session event to Messenger.
- Uses the session ID to derive a stable event ID, so retries do not duplicate a successful capture.
- Does not read the transcript, capture prompts or tool output, trigger Learning, mutate learner state, or promote Knowledge.

Configuration:

| Variable | Default | Purpose |
|---|---|---|
| `FLOSSWARE_MEMORY_URL` | `http://127.0.0.1:8767` | Memory REST endpoint |
| `FLOSSWARE_MEMORY_TIMEOUT_MS` | `1500` | Memory request timeout |
| `CLAUDE_MESSENGER_SOCKET` | Derived from `XDG_RUNTIME_DIR` or `/run/user/<uid>` | Messenger Unix socket |

Memory and Messenger failures are reported to stderr without printing the event payload. The SessionEnd hook is intentionally registered without an exit-reason matcher so it applies across session-ending reasons.

## Profile isolation and installation

The managed hook installer honors `ENSEMBLE_CLAUDE_CODE_CONFIG_DIR`. For example:

```bash
ENSEMBLE_CLAUDE_CODE_CONFIG_DIR="$HOME/.config/claude-ensemble/acceptance-profile" \
  bash tools/install-ce-skills.sh
```

For the versioned standalone deployment, the service-managed profile lives under `~/.FlossWare/claude-ensemble/shared/claude-code/`. Ordinary `~/.claude/` is a separate profile and must not be assumed to belong to CE. The installer preserves unrelated user-owned hooks and settings, and validates managed hook registration through `claude-config verify` / `ce doctor`.

Do not copy an entire Claude profile between work and personal environments: profiles can contain provider-specific configuration and authentication state.

## Diagnostics

From a source checkout:

```bash
bash tools/ce doctor --verbose
bash tools/ce diagnose --hook-smoke --json
bash tools/ce diagnose --prompt-context-smoke --json
```

The prompt-context smoke test runs the selected installed hook against configured services. It does not invoke Claude or prove that a model received the context. Hook lookup uses `CLAUDE_HOOK_DIR`, then the explicitly selected `ENSEMBLE_CLAUDE_CODE_CONFIG_DIR` or `CLAUDE_CONFIG_DIR`, and otherwise the standalone CE profile's `shared/claude-code/hooks` directory.

See [the diagnostics contract](../docs/DIAGNOSTICS.md) for JSON schemas, exit semantics, and side-effect boundaries.

## Other files in this directory

This directory also contains historical, experimental, or separately invoked workflow helpers. Their presence does not mean they are installed or registered by the canonical CE Claude Code configuration. Do not wire them into Claude settings without reviewing their dependencies, persistence behavior, security boundaries, and current tests.
