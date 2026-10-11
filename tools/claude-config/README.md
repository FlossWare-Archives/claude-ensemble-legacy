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
