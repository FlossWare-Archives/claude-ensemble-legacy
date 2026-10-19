# Claude Config

Safe, idempotent Claude Code configuration for the FlossWare Claude Ensemble integration.

CLI and TUI share the same installation engine. The TUI uses FlossWare curses-tui for terminal rendering and delegates mutations to the CLI/core.

CLI:
  tools/claude-config/install.sh
  tools/claude-config/bin/claude-config detect
  tools/claude-config/bin/claude-config plan
  tools/claude-config/bin/claude-config verify
  tools/claude-config/bin/claude-config doctor
  tools/claude-config/bin/claude-config doctor --verbose
  tools/claude-config/bin/claude-config doctor --json

TUI:
  tools/claude-config/tui.sh

Safety:
- preserve existing Claude configuration
- merge UserPromptSubmit hooks
- resolve settings, hooks, manifest, backups, and lock under the selected Claude profile
- require an explicit profile for profile-scoped operations, selected with ENSEMBLE_CLAUDE_CODE_CONFIG_DIR (preferred for CE) or CLAUDE_CONFIG_DIR; without one, mutation and verification commands fail closed rather than inspect or modify ~/.claude
- refuse symlinked hooks directories, settings files, or installer state directories to prevent writes escaping the selected profile
- track managed files in <selected-profile>/.flossware-claude-config/manifest.json
- back up before mutation
- refuse unknown hook conflicts unless --force is supplied
- fail open when Memory REST is unavailable
- support dry-run and non-interactive operation
- serialize mutations with a lock

Memory defaults to http://127.0.0.1:8767. The UserPromptSubmit hook uses POST /memory/search; the SessionEnd capture hook uses POST /memory/append-once with a stable session-derived event ID. Override the service URL with FLOSSWARE_MEMORY_URL.

The Memory REST service is a loopback-only service. The hook sends the extracted query to that local service and fails open if it is unavailable. Do not expose the Memory endpoint directly on a non-loopback interface. If a trusted local proxy is used for another deployment topology, put authentication and TLS at that boundary rather than adding credentials to the Claude Code hook.

The `/memory/search` contract returns JSON with `ok: true` and a `results` array. Each result used by the hook provides a `content` string; the hook ignores other result shapes instead of guessing at service internals.

### Configuring prompt-time RAG

The canonical UserPromptSubmit hook reads profile-scoped settings from `<selected-profile>/.flossware-claude-config/rag.json`. Use the CLI instead of editing managed hook scripts:

```bash
tools/claude-config/bin/claude-config rag-config show
tools/claude-config/bin/claude-config rag-config set memory.enabled=false graph.enabled=false
tools/claude-config/bin/claude-config rag-config set knowledge.max_queries=2 memory.timeout_ms=900
tools/claude-config/bin/claude-config rag-config reset
```

Set `ENSEMBLE_CLAUDE_CODE_CONFIG_DIR` to the dedicated CE profile (or `CLAUDE_CONFIG_DIR`) before running these commands. The command refuses to fall back to `~/.claude`, rejects symlinked profile/state directories, validates every value, writes atomically, and keeps the config private. If a manually edited file is malformed or contains unsupported values, the hook reports a configuration error and skips retrieval rather than silently applying defaults. Missing configuration preserves existing behavior. Environment timeout overrides remain available for isolated test/deployment tuning.

Supported settings:

| Setting | Default | Valid range |
|---|---:|---:|
| `memory.enabled` | `true` | `true` / `false` |
| `memory.timeout_ms` | `1500` | 50–10000 |
| `memory.max_results` | `10` | 1–10 |
| `knowledge.enabled` | `true` | `true` / `false` |
| `knowledge.timeout_ms` | `1500` | 50–10000 |
| `knowledge.max_results` | `10` | 1–10 |
| `knowledge.max_queries` | `3` | 1–3 |
| `graph.enabled` | `true` | `true` / `false` |
| `graph.timeout_ms` | `400` | 50–5000 |
| `graph.max_roots` | `3` | 1–3 |
| `graph.max_nodes` | `8` | 1–8 |
| `graph.max_context_chars` | `3000` | 100–3000 |

### Indexing project documentation as Knowledge

The normal `sync`/`watch` commands ingest Claude Code's own `~/.claude/projects/*/memory` notes and `MEMORY.md` files. They do not automatically ingest arbitrary repository documentation. To make a project's durable Markdown documentation available to prompt-time Knowledge retrieval, explicitly run:

```bash
python3 tools/claude-config/lib/memory_sync.py knowledge-sync --root /path/to/project
```

This indexes Markdown below the selected project root, skips common generated/vendor directories, and reconciles stale entries only after all current files were ingested successfully. The root is the reconciliation scope, so choose the project directory deliberately. It does not start a watcher; rerun after documentation changes.

Integration version: 0.2.

The canonical REST gateway is installed as `claude-ensemble.service` and listens on loopback at `http://127.0.0.1:8080` by default. `claude-config doctor` reports configuration/runtime findings, including gateway deployment state, Memory health, gateway health, capability health, provider policy, and managed hook registration. Use `--verbose` to show passing checks or `--json` for machine-readable output. Override the gateway URL with `ENSEMBLE_GATEWAY_URL`; Memory uses `FLOSSWARE_MEMORY_URL`.

**Doctor exit semantics:** exit code `1` means one or more checks have status `fail`; exit code `0` means no checks failed. Warnings (`warn`) and skipped checks (`skip`) are reported but do not change the exit code. Warnings commonly mean an optional service or systemd unit is unavailable, while failures identify a configured requirement that is not satisfied. In JSON output, inspect `ok`, `summary`, and each check's `status` and `remediation`. A skipped provider-policy check is informational and has no remediation because no dedicated CE profile was explicitly selected.

To check CE profile configuration and provider policy without inspecting a separately managed default profile, set `ENSEMBLE_CLAUDE_CODE_CONFIG_DIR` to the dedicated CE profile directory (or set `CLAUDE_CONFIG_DIR` in the CE service environment) and optionally set `ENSEMBLE_CLAUDE_CODE_EXPECTED_PROVIDER` (defaults to `vertex`; a blank value is rejected). **Without an explicitly selected profile, all profile-scoped checks are skipped**: doctor does not stat, read, validate, or otherwise inspect the default `~/.claude` directory or its files. The check runs `claude auth status` only against the explicitly configured profile and never sends a model prompt. HTTP health checks read at most 64 KiB plus one detection byte; responses larger than 64 KiB fail with an explicit size-limit diagnostic rather than being parsed as truncated JSON.


## Workflow outcome integration

The Claude Config installer deliberately registers only the supported prompt-context and SessionEnd capture hooks. Neither generic hook event proves task quality, so they must not be wired directly to Thompson.

Workflow orchestrators that have a stable run ID and a real, evidence-backed 1-5 evaluation should invoke `tools/claude-config/bin/claude-config outcome` from the repository root (or run `python3 tools/record_workflow_outcome.py` directly) and provide the JSON contract documented in [the lifecycle guide](../../docs/CLAUDE_CONTEXT_HOOK_LIFECYCLE.md#evidence-gated-workflow-outcome-adapter). The adapter writes the evidence-backed learning artifact through Learning first, then requests idempotent outcome processing through Learning's Unix-socket client. It never talks to Thompson directly. A failed Learning-level acknowledgement exits nonzero for retry with the same `run_id` and `task_id`. The success field `learning_outcome: acknowledged` means Learning returned `ok: true`; it does not claim that the adapter independently verified downstream Thompson persistence/checkpoint status. Retry behavior is at-least-once, not a guarantee of exactly-once learner updates.

This command is not registered as a Claude Code hook because Claude Code's generic completion events do not contain an independently validated quality rating. Registering it without a real evaluator would manufacture learning signals.
