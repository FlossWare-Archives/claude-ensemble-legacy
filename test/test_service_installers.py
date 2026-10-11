#!/usr/bin/env python3
"""Verify Linux service installers render valid units from a clean checkout."""

import os
import shlex
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

SERVICES = (
    ("memory-service", "claude-memory.service"),
    ("thompson-service", "claude-thompson.service"),
    ("learning-service", "claude-learning.service"),
    ("alert_service", "claude-alert.service"),
    ("session-messaging", "claude-messenger.service"),
    ("graph-service", "claude-graph.service"),
    ("server", "claude-ensemble.service"),
)


def fail(message: str) -> None:
    raise SystemExit(f"FAIL: {message}")


with tempfile.TemporaryDirectory() as tmp:
    home = Path(tmp) / "home"
    bin_dir = Path(tmp) / "bin"
    home.mkdir()
    bin_dir.mkdir()

    fake_systemctl = bin_dir / "systemctl"
    fake_systemctl.write_text(
        '''#!/usr/bin/env bash
echo "$*" >> "$SYSTEMCTL_LOG"
if [[ "$*" == *"is-active"* ]]; then
  if [[ -n "${FAIL_INACTIVE_UNIT:-}" && "$*" == *"$FAIL_INACTIVE_UNIT"* ]]; then exit 3; fi
  exit 0
fi
exit 0
''',
        encoding="utf-8",
    )
    fake_systemctl.chmod(0o755)

    # The top-level installer deliberately requires Claude Code to already
    # exist. Stub it in PATH so this test remains isolated and does not install
    # or depend on a real Claude Code CLI.
    fake_claude = bin_dir / "claude"
    fake_claude.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    fake_claude.chmod(0o755)

    env = os.environ.copy()
    env["HOME"] = str(home)
    env["PATH"] = f"{bin_dir}:{env['PATH']}"
    env["SYSTEMCTL_LOG"] = str(Path(tmp) / "systemctl.log")
    env["SYSTEMCTL_BIN"] = str(fake_systemctl)

    for service_dir, service_name in SERVICES:
        script = ROOT / service_dir / "install.sh"
        if not script.is_file():
            fail(f"missing installer: {script}")

        result = subprocess.run(
            ["bash", str(script)],
            cwd=ROOT,
            env=env,
            text=True,
            capture_output=True,
        )
        if result.returncode != 0:
            fail(
                f"{script} failed with exit {result.returncode}: "
                f"{result.stdout}\n{result.stderr}"
            )

        installed = home / ".config/systemd/user" / service_name
        if not installed.is_file():
            fail(f"{script} did not install {installed}")

        text = installed.read_text(encoding="utf-8")
        if "%REPO_PATH%" in text:
            fail(f"{installed} still contains %REPO_PATH%")
        if "[Service]" not in text or "ExecStart=" not in text:
            fail(f"{installed} is not a usable systemd unit")
        if "Type=simple" not in text:
            fail(f"{installed} is not Type=simple")

        exec_line = next(
            line for line in text.splitlines() if line.startswith("ExecStart=")
        )
        exec_tokens = shlex.split(exec_line.split("=", 1)[1])
        executable = next((token for token in reversed(exec_tokens) if token.endswith(".py")), None)
        if executable is None or not Path(executable).is_file():
            fail(f"{installed} ExecStart does not reference a repository Python executable: {exec_line}")

    # Regression case 1: an identical existing hook with mode 0644 must
    # retain its content while becoming executable.
    hook_home = home / ".claude/hooks"
    hook_home.mkdir(parents=True, exist_ok=True)
    identical_source = ROOT / "hooks/memory-rag-search.js"
    identical_deployment = hook_home / identical_source.name
    identical_bytes = identical_source.read_bytes()
    identical_deployment.write_bytes(identical_bytes)
    identical_deployment.chmod(0o644)

    # Regression case 2: a recognized repository symlink to a non-executable
    # source must become an independent executable copy without chmodding source.
    symlink_source = ROOT / "hooks/session-end-comprehensive-capture.sh"
    symlink_mode = symlink_source.stat().st_mode & 0o777
    symlink_source.chmod(symlink_mode & ~0o111)
    symlink_deployment = hook_home / symlink_source.name
    symlink_deployment.symlink_to(symlink_source)

    try:
        top_level = subprocess.run(
            ["bash", str(ROOT / "install.sh"), str(ROOT)],
            cwd=ROOT,
            env=env,
            input="n\n",
            text=True,
            capture_output=True,
        )
        if top_level.returncode != 0:
            fail(
                f"top-level install.sh failed with exit {top_level.returncode}: "
                f"{top_level.stdout}\n{top_level.stderr}"
            )
        if symlink_source.stat().st_mode & 0o111:
            fail("installer changed repository source permissions for symlinked hook")
    finally:
        symlink_source.chmod(symlink_mode)

    if identical_deployment.read_bytes() != identical_bytes:
        fail("installer changed the content of an identical existing hook")
    if not identical_deployment.is_file() or not identical_deployment.stat().st_mode & 0o111:
        fail("installer did not repair execute permissions on identical existing hook")
    if symlink_deployment.is_symlink():
        fail("installer left a symlink to a non-executable repository hook")
    if not symlink_deployment.is_file() or not symlink_deployment.stat().st_mode & 0o111:
        fail("installer did not deploy an executable copy of the symlinked hook")
    if symlink_deployment.read_bytes() != symlink_source.read_bytes():
        fail("deployed copy differs from repository hook source")

    systemctl_log = Path(env["SYSTEMCTL_LOG"]).read_text(encoding="utf-8")
    for service_dir, service_name in SERVICES:
        installed = home / ".config/systemd/user" / service_name
        if not installed.is_file():
            fail(f"top-level installer did not install {installed}")
        if f"enable {service_name}" not in systemctl_log:
            fail(f"top-level installer did not enable {service_name}")
        if f"start {service_name}" not in systemctl_log:
            fail(f"top-level installer did not start {service_name}")

    # The default installer must fail closed if the gateway unit is inactive.
    inactive_env = env.copy()
    inactive_env["FAIL_INACTIVE_UNIT"] = "claude-ensemble.service"
    inactive = subprocess.run(
        ["bash", str(ROOT / "install.sh"), str(ROOT)],
        cwd=ROOT,
        env=inactive_env,
        input="n\n",
        text=True,
        capture_output=True,
    )
    if inactive.returncode == 0:
        fail("top-level installer succeeded while the REST gateway was inactive")
    if "REST gateway failed to start" not in inactive.stderr:
        fail(f"inactive gateway failure was not diagnosed: {inactive.stdout}\n{inactive.stderr}")

if not (ROOT / "toolkit-models.yaml.default").is_file():
    fail("missing toolkit-models.yaml.default")

print("PASS: Linux service installers render valid systemd units")
