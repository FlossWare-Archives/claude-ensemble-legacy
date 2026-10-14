#!/usr/bin/env python3
"""Exercise the real top-level installer using only disposable home/config state."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXPECTED = (
    "claude-memory.service",
    "claude-thompson.service",
    "claude-learning.service",
    "claude-alert.service",
    "claude-messenger.service",
    "claude-graph.service",
)


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
    if claude:
        cli = bin_dir / "claude"
        cli.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
        cli.chmod(0o755)
    env = os.environ.copy()
    env["HOME"] = str(home)
    env["PATH"] = f"{bin_dir}:/usr/bin:/bin"
    env["SYSTEMCTL_LOG"] = str(systemctl_log)
    env["SYSTEMCTL_BIN"] = str(systemctl)
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
    claude_home = home / ".claude"
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

    # Existing settings contain unrelated user-owned configuration.
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
    mcp = home / ".mcp.json"
    mcp.write_text('{"mcpServers":{"user-server":{"command":"user-tool"}}}\n', encoding="utf-8")
    models = claude_home / "toolkit-models.yaml"
    models.write_text("# user model config\n", encoding="utf-8")
    secrets_dir = home / ".FlossWare"
    secrets_dir.mkdir()
    secrets = secrets_dir / "secrets.env"
    secrets.write_text("# user secrets placeholder\n", encoding="utf-8")
    bashrc = home / ".bashrc"
    bashrc.write_text("# existing shell config\n", encoding="utf-8")

    result = run_installer(env)
    if result.returncode != 0:
        fail(f"full installer failed ({result.returncode}):\n{result.stdout}\n{result.stderr}")

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
    if "claude-ensemble/tools" not in bashrc.read_text(encoding="utf-8"):
        fail("installer did not add its PATH entry to existing shell config")

    service_dir = home / ".config/systemd/user"
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
