#!/usr/bin/env python3
"""Exercise the real top-level installer using only disposable home/config state."""

from __future__ import annotations

import atexit
import http.server
import json
import os
import shutil
import subprocess
import tempfile
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INSTALLER_TEXT = (ROOT / "install.sh").read_text(encoding="utf-8")
if 'configuration."\\\\n' in INSTALLER_TEXT:
    raise SystemExit("FAIL: installer prints a literal \\n escape")
if "config.sh" in INSTALLER_TEXT:
    raise SystemExit("FAIL: installer still advertises removed config.sh")
if "--copies" not in INSTALLER_TEXT or "ENSEMBLE_SKIP_MODEL_PROVISION" not in INSTALLER_TEXT:
    raise SystemExit("FAIL: installer runtime provisioning lacks safe/offline controls")

# The installer integration test must never download dependencies or model
# weights. Use a disposable executable placeholder only when the checkout has
# no pre-provisioned runtime, and remove it on process exit.
TEST_VENV = ROOT / ".venv"
CREATED_TEST_VENV = False
if not TEST_VENV.exists():
    (TEST_VENV / "bin").mkdir(parents=True)
    fake_python = TEST_VENV / "bin" / "python"
    fake_python.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    fake_python.chmod(0o755)
    CREATED_TEST_VENV = True


def _cleanup_test_venv():
    if CREATED_TEST_VENV:
        shutil.rmtree(TEST_VENV, ignore_errors=True)


atexit.register(_cleanup_test_venv)
if "sys.version_info >= (3, 11)" not in INSTALLER_TEXT:
    raise SystemExit("FAIL: installer does not enforce Python 3.11 minimum")
if "major >= 22" not in INSTALLER_TEXT:
    raise SystemExit("FAIL: installer does not enforce Node.js 22 minimum")
if "for required in git python3 node systemctl" not in INSTALLER_TEXT:
    raise SystemExit("FAIL: installer prerequisite list omits Node.js")
EXPECTED = (
    "claude-memory.service",
    "claude-thompson.service",
    "claude-learning.service",
    "claude-graph.service",
    "claude-ensemble.service",
)

class ReadyHandler(http.server.BaseHTTPRequestHandler):
    """Stand in for the gateway readiness endpoint in this isolated test."""

    def do_GET(self) -> None:
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"ok")

    def log_message(self, format: str, *args: object) -> None:
        pass


# The installer correctly waits for gateway readiness. This test mocks that
# dependency with a local HTTP endpoint because systemd itself is mocked.
readiness_server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), ReadyHandler)
threading.Thread(target=readiness_server.serve_forever, daemon=True).start()


def fail(message: str) -> None:
    raise SystemExit(f"FAIL: {message}")


def make_harness(base: Path, *, claude: bool = True) -> tuple[Path, dict[str, str]]:
    home = base / "home"
    bin_dir = base / "bin"
    home.mkdir(parents=True)
    bin_dir.mkdir()
    systemctl_log = base / "systemctl.log"
    systemctl = bin_dir / "systemctl"
    systemctl.write_text(
        '#!/usr/bin/env bash\n'
        'printf "%s\\n" "$*" >> "$SYSTEMCTL_LOG"\n'
        'if [[ "$*" == *"show-environment"* ]]; then exit 0; fi\n'
        'if [[ "$*" == *"is-active"* && "${FAIL_ACTIVE:-0}" == "1" ]]; then exit 1; fi\n'
        'if [[ "$*" == *"is-active"* ]]; then exit 0; fi\n'
        'exit 0\n',
        encoding="utf-8",
    )
    systemctl.chmod(0o755)
    # The CI Node.js binary may live outside /usr/bin and /bin. Make it
    # visible through the isolated PATH without replacing its real runtime.
    node_binary = shutil.which("node")
    if node_binary:
        (bin_dir / "node").symlink_to(node_binary)
    if claude:
        cli = bin_dir / "claude"
        cli.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
        cli.chmod(0o755)
    env = os.environ.copy()
    env["HOME"] = str(home)
    env["ENSEMBLE_CLAUDE_CODE_CONFIG_DIR"] = str(home / ".FlossWare" / "claude-ensemble-profile")
    env["CLAUDE_CONFIG_DIR"] = env["ENSEMBLE_CLAUDE_CODE_CONFIG_DIR"]
    env["PATH"] = f"{bin_dir}:/usr/bin:/bin"
    env["SYSTEMCTL_LOG"] = str(systemctl_log)
    env["ENSEMBLE_SKIP_PYTHON_PROVISION"] = "1"
    env["SYSTEMCTL_BIN"] = str(systemctl)
    env["ENSEMBLE_READINESS_URL"] = (
        f"http://127.0.0.1:{readiness_server.server_port}/api/v1/ready"
    )
    env["ENSEMBLE_READINESS_TIMEOUT_SECONDS"] = "3"
    return home, env


def run_installer(env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(ROOT / "install.sh"), str(ROOT)],
        cwd=ROOT,
        env=env,
        input="n\n",
        text=True,
        capture_output=True,
        timeout=90,
    )


with tempfile.TemporaryDirectory(prefix="ce-install-e2e-") as temp:
    base = Path(temp)

    # Missing Claude CLI must fail before creating or modifying user state.
    missing_base = base / "missing-cli"
    missing_base.mkdir()
    missing_home, missing_env = make_harness(missing_base, claude=False)
    missing = run_installer(missing_env)
    if missing.returncode == 0 or "Claude Code CLI was not found" not in missing.stderr:
        fail(f"missing CLI did not fail clearly: {missing.stdout} {missing.stderr}")
    if (missing_home / ".claude").exists() or (missing_home / ".config").exists():
        fail("missing prerequisite mutated the disposable home")

    # Successful full install with realistic pre-existing user configuration.
    success_base = base / "success"
    success_base.mkdir()
    home, env = make_harness(success_base)
    legacy_unit_dir = home / ".config" / "systemd" / "user"
    legacy_unit_dir.mkdir(parents=True)
    for legacy_unit in ("claude-alert.service", "claude-messenger.service"):
        (legacy_unit_dir / legacy_unit).write_text("[Unit]\\nDescription=legacy CE unit\\n", encoding="utf-8")
    claude_home = home / ".FlossWare" / "claude-ensemble-profile"
    hooks = claude_home / "hooks"
    hooks.mkdir(parents=True)

    identical_source = ROOT / "hooks/memory-rag-search.js"
    identical_deployment = hooks / identical_source.name
    original_hook_bytes = identical_source.read_bytes()
    identical_deployment.write_bytes(original_hook_bytes)
    identical_deployment.chmod(0o644)

    differing_source = next(
        path for path in sorted((ROOT / "hooks").glob("*.js"))
        if path.name != identical_source.name
    )
    differing_deployment = hooks / differing_source.name
    custom_hook_bytes = b"// User-owned hook. Preserve this content.\n"
    differing_deployment.write_bytes(custom_hook_bytes)
    differing_deployment.chmod(0o700)

    # Ordinary ~/.claude is unrelated to CE and must remain untouched.
    ordinary_claude = home / ".claude"
    ordinary_claude.mkdir(parents=True)
    ordinary_sentinel = ordinary_claude / "sentinel"
    ordinary_sentinel.write_text("ordinary-profile-must-remain-untouched\\n", encoding="utf-8")

    # Legacy service data is copied to the isolated state root, never moved or edited.
    legacy_memory = ordinary_claude / "projects" / "memory"
    legacy_memory.mkdir(parents=True)
    (legacy_memory / "legacy-memory.json").write_text('{"event":"preserve"}\\n', encoding="utf-8")
    legacy_learning = ordinary_claude / "projects" / "learning"
    legacy_learning.mkdir(parents=True)
    (legacy_learning / "outcome.json").write_text('{"outcome":"preserve"}\\n', encoding="utf-8")
    (legacy_learning / "thompson-sampling-state.json").write_text('{"alpha":2}\\n', encoding="utf-8")
    legacy_graph = home / ".local" / "share" / "claude-ensemble" / "graph.json"
    legacy_graph.parent.mkdir(parents=True)
    legacy_graph.write_text('{"nodes":[]}\\n', encoding="utf-8")

    # Existing settings in the selected CE profile contain unrelated user-owned configuration.
    settings = claude_home / "settings.json"
    settings_data = {
        "permissions": {"allow": ["Bash(git status:*)"]},
        "hooks": {"Notification": [{"matcher": "", "hooks": [{"type": "command", "command": "user-notification"}]}]},
        "userSetting": {"keep": True},
    }
    settings.write_text(json.dumps(settings_data, indent=2) + "\n", encoding="utf-8")

    # Paths managed as symlinks by older installer versions must not be
    # deleted when a user has placed their own file or link there.
    init_path = claude_home / "ensemble-init.sh"
    init_bytes = b"#!/bin/sh\n# custom init: preserve\n"
    init_path.write_bytes(init_bytes)
    init_path.chmod(0o700)
    config_path = claude_home / "config.sh"
    external_target = success_base / "my-config.sh"
    external_bytes = b"# external user config\n"
    external_target.write_bytes(external_bytes)
    config_path.symlink_to(external_target)

    # Existing unrelated files should not be replaced by template defaults.
    mcp = claude_home / ".mcp.json"
    mcp.write_text('{"mcpServers":{"user-server":{"command":"user-tool"}}}\n', encoding="utf-8")
    models = claude_home / "toolkit-models.yaml"
    models.write_text("# user model config\n", encoding="utf-8")
    secrets_dir = home / ".FlossWare"
    secrets_dir.mkdir(parents=True, exist_ok=True)
    secrets = secrets_dir / "secrets.env"
    secrets.write_text("# user secrets placeholder\n", encoding="utf-8")
    bashrc = home / ".bashrc"
    bashrc.write_text("# existing shell config\n", encoding="utf-8")

    result = run_installer(env)
    if result.returncode != 0:
        fail(f"full installer failed ({result.returncode}):\n{result.stdout}\n{result.stderr}")

    if "Skipping network provisioning" not in result.stdout:
        fail("top-level installer did not honor ENSEMBLE_SKIP_PYTHON_PROVISION")

    if identical_deployment.read_bytes() != original_hook_bytes or not identical_deployment.stat().st_mode & 0o111:
        fail("identical non-executable hook was not preserved and repaired")
    if differing_deployment.read_bytes() != custom_hook_bytes:
        fail("differing user-owned hook content was overwritten")
    if not differing_deployment.stat().st_mode & 0o111:
        fail("differing executable user-owned hook lost execute permission")
    if init_path.is_symlink() or init_path.read_bytes() != init_bytes:
        fail("existing user-owned ensemble-init.sh was replaced")
    if not config_path.is_symlink() or config_path.resolve() != external_target:
        fail("existing user-owned config.sh symlink was replaced")
    if external_target.read_bytes() != external_bytes:
        fail("external symlink target was changed")

    installed_settings = json.loads(settings.read_text(encoding="utf-8"))
    if installed_settings.get("permissions") != settings_data["permissions"]:
        fail("installer changed unrelated permissions settings")
    if installed_settings.get("hooks", {}).get("Notification") != settings_data["hooks"]["Notification"]:
        fail("installer changed unrelated Notification hook settings")
    if installed_settings.get("userSetting") != {"keep": True}:
        fail("installer removed unrelated userSetting")
    if mcp.read_text(encoding="utf-8") != '{"mcpServers":{"user-server":{"command":"user-tool"}}}\n':
        fail("installer overwrote existing .mcp.json")
    if models.read_text(encoding="utf-8") != "# user model config\n":
        fail("installer overwrote existing model configuration")
    if secrets.read_text(encoding="utf-8") != "# user secrets placeholder\n":
        fail("installer overwrote existing secrets file")
    if ordinary_sentinel.read_text(encoding="utf-8") != "ordinary-profile-must-remain-untouched\\n":
        fail("installer modified ordinary ~/.claude")
    if (ordinary_claude / "settings.json").exists() or (ordinary_claude / "hooks").exists():
        fail("installer wrote CE configuration into ordinary ~/.claude")
    state_root = home / ".FlossWare" / "claude-ensemble-state"
    migrated_files = (
        state_root / "data/memory/legacy-memory.json",
        state_root / "data/learning/outcome.json",
        state_root / "data/learning/thompson-sampling-state.json",
        state_root / "data/graph.json",
    )
    for migrated in migrated_files:
        if not migrated.is_file():
            fail(f"legacy service state was not copied to the isolated state root: {migrated}")
    if (legacy_memory / "legacy-memory.json").read_text(encoding="utf-8") != '{"event":"preserve"}\\n':
        fail("legacy Memory source was modified during migration")
    if legacy_graph.read_text(encoding="utf-8") != '{"nodes":[]}\\n':
        fail("legacy Graph source was modified during migration")
    if "claude-ensemble/tools" not in bashrc.read_text(encoding="utf-8"):
        fail("installer did not add its PATH entry to existing shell config")

    service_dir = home / ".config/systemd/user"
    for legacy_unit in ("claude-alert.service", "claude-messenger.service"):
        if (service_dir / legacy_unit).exists():
            fail(f"legacy service unit was not retired: {legacy_unit}")
    for unit in EXPECTED:
        if not (service_dir / unit).is_file():
            fail(f"top-level installer did not create {unit}")
    log = Path(env["SYSTEMCTL_LOG"]).read_text(encoding="utf-8")
    for unit in EXPECTED:
        if f"enable {unit}" not in log:
            fail(f"top-level installer did not request enable for {unit}")
        if f"start {unit}" not in log:
            fail(f"top-level installer did not request start for {unit}")
        if f"is-active --quiet {unit}" not in log:
            fail(f"top-level installer did not verify active state for {unit}")

    # A service manager that reports inactive must make the full installer fail.
    failure_base = base / "service-failure"
    failure_base.mkdir()
    failure_home, failure_env = make_harness(failure_base)
    failure_env["FAIL_ACTIVE"] = "1"
    failed = run_installer(failure_env)
    if failed.returncode == 0 or "failed to start" not in (failed.stdout + failed.stderr).lower():
        fail(f"inactive service did not fail the install: {failed.stdout} {failed.stderr}")
    if not (failure_home / ".config/systemd/user/claude-memory.service").is_file():
        fail("service failure scenario did not reach the service installation stage")

print("PASS: isolated top-level installer end-to-end tests")
