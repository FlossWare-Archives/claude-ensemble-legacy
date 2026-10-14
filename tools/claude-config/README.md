# Claude Config

Safe, idempotent Claude Code configuration for the FlossWare Claude Ensemble integration.

CLI and TUI share the same installation engine. The TUI uses FlossWare curses-tui for terminal rendering and delegates mutations to the CLI/core.

CLI:
  tools/claude-config/install.sh
  tools/claude-config/bin/claude-config detect
  tools/claude-config/bin/claude-config plan
  tools/claude-config/bin/claude-config verify
  tools/claude-config/bin/claude-config doctor

TUI:
  tools/claude-config/tui.sh

Safety:
- preserve existing Claude configuration
- merge UserPromptSubmit hooks
- track managed files in ~/.claude/.flossware-claude-config/manifest.json
- back up before mutation
- refuse unknown hook conflicts unless --force is supplied
- fail open when Memory REST is unavailable
- support dry-run and non-interactive operation
- serialize mutations with a lock

Memory defaults to http://127.0.0.1:8767. The UserPromptSubmit hook uses POST /memory/search; the SessionEnd capture hook uses POST /memory/append-once with a stable session-derived event ID. Override the service URL with FLOSSWARE_MEMORY_URL.

The Memory REST service is a loopback-only service. The hook sends the extracted query to that local service and fails open if it is unavailable. Do not expose the Memory endpoint directly on a non-loopback interface. If a trusted local proxy is used for another deployment topology, put authentication and TLS at that boundary rather than adding credentials to the Claude Code hook.

The `/memory/search` contract returns JSON with `ok: true` and a `results` array. Each result used by the hook provides a `content` string; the hook ignores other result shapes instead of guessing at service internals.

Integration version: 0.2.

The canonical REST gateway is installed as `claude-ensemble.service` and listens on loopback at `http://127.0.0.1:8080` by default. `claude-config doctor` reports gateway deployment state (systemd unit active/inactive) separately from gateway HTTP health (`/api/v1/health` reachable/unreachable). Override the diagnostic URL with `ENSEMBLE_GATEWAY_URL`; these checks are informational and do not confuse hook verification with gateway availability.


## Workflow outcome integration

The Claude Config installer deliberately registers only the supported prompt-context and SessionEnd capture hooks. Neither generic hook event proves task quality, so they must not be wired directly to Thompson.

Workflow orchestrators that have a stable run ID and a real, evidence-backed 1-5 evaluation should invoke `tools/claude-config/bin/claude-config outcome` from the repository root (or run `python3 tools/record_workflow_outcome.py` directly) and provide the JSON contract documented in [the lifecycle guide](../../docs/CLAUDE_CONTEXT_HOOK_LIFECYCLE.md#evidence-gated-workflow-outcome-adapter). The adapter writes the evidence-backed learning artifact through Learning first, then requests idempotent outcome processing through Learning's Unix-socket client. It never talks to Thompson directly. A failed Learning-level acknowledgement exits nonzero for retry with the same `run_id` and `task_id`. The success field `learning_outcome: acknowledged` means Learning returned `ok: true`; it does not claim that the adapter independently verified downstream Thompson persistence/checkpoint status. Retry behavior is at-least-once, not a guarantee of exactly-once learner updates.

This command is not registered as a Claude Code hook because Claude Code's generic completion events do not contain an independently validated quality rating. Registering it without a real evaluator would manufacture learning signals.
