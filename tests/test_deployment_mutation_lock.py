"""Subprocess regression tests for deployment mutation serialization."""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
DEPLOYMENT_SCRIPT = REPO_ROOT / "tools" / "ce-deployment"


def _wait_for(path: Path, process: subprocess.Popen[str], timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.exists():
            return
        if process.poll() is not None:
            raise AssertionError(f"deployment exited before reaching barrier: {process.returncode}")
        time.sleep(0.02)
    raise AssertionError(f"timed out waiting for barrier {path}")


def test_concurrent_uninstalls_are_serialized(tmp_path: Path) -> None:
    home = tmp_path / "home"
    flossware = home / ".FlossWare"
    root = flossware / "claude-ensemble"
    unit_dir = home / ".config" / "systemd" / "user"
    bin_dir = tmp_path / "bin"
    calls = tmp_path / "systemctl-calls.log"
    ready = tmp_path / "first-stop-ready"
    release = tmp_path / "release-first-stop"
    for path in (flossware, root, unit_dir, bin_dir):
        path.mkdir(parents=True, exist_ok=True)

    fake_systemctl = bin_dir / "systemctl"
    fake_systemctl.write_text(
        "#!/usr/bin/env python3\n"
        "import os, pathlib, sys, time\n"
        f"calls = pathlib.Path({str(calls)!r})\n"
        f"ready = pathlib.Path({str(ready)!r})\n"
        f"release = pathlib.Path({str(release)!r})\n"
        "with calls.open('a', encoding='utf-8') as out:\n"
        "    out.write(' '.join(sys.argv[1:]) + '\\n')\n"
        "args = sys.argv[1:]\n"
        "if len(args) >= 3 and args[1] == 'stop' and not ready.exists():\n"
        "    ready.touch()\n"
        "    deadline = time.monotonic() + 10\n"
        "    while not release.exists() and time.monotonic() < deadline: time.sleep(0.01)\n"
        "    if not release.exists(): raise SystemExit('test barrier timed out')\n"
        "if len(args) >= 2 and args[1] == 'is-active': raise SystemExit(1)\n"
        "raise SystemExit(0)\n",
        encoding="utf-8",
    )
    fake_systemctl.chmod(0o755)

    env = os.environ.copy()
    env.update(
        {
            "HOME": str(home),
            "ENSEMBLE_INSTALL_ROOT": str(root),
            "ENSEMBLE_USER_UNIT_DIR": str(unit_dir),
            "ENSEMBLE_SYSTEMCTL_BIN": str(fake_systemctl),
        }
    )

    first = subprocess.Popen(
        ["bash", str(DEPLOYMENT_SCRIPT), "uninstall"],
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    second: subprocess.Popen[str] | None = None
    try:
        _wait_for(ready, first)
        second = subprocess.Popen(
            ["bash", str(DEPLOYMENT_SCRIPT), "uninstall"],
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

        # The second process must not reach systemctl while the first owns the
        # lock and is stopped at the barrier inside its first stop operation.
        time.sleep(0.2)
        logged = calls.read_text(encoding="utf-8").splitlines()
        assert sum(line.endswith("show-environment") for line in logged) == 1
        assert sum(" stop " in f" {line} " for line in logged) == 1
    finally:
        release.touch()
        first_out, first_err = first.communicate(timeout=10)
        second_out, second_err = second.communicate(timeout=10) if second else ("", "")
    assert first.returncode == 0, (first_out, first_err)
    assert second is not None and second.returncode == 0, (second_out, second_err)
    logged = calls.read_text(encoding="utf-8").splitlines()
    assert sum(line.endswith("show-environment") for line in logged) == 2


def test_all_mutating_commands_use_the_same_lock_protocol() -> None:
    source = DEPLOYMENT_SCRIPT.read_text(encoding="utf-8")
    for command in ("activate", "rollback", "recover", "prune", "uninstall"):
        assert command in source.split("case \"$cmd\" in", 1)[1].split(";;", 1)[0]
    assert 'LOCK_FILE="$ROOT_PARENT/.${ROOT_BASE}.deployment.lock"' in source
    assert "flock -x 9" in source
    assert "FLOSSWARE_DEPLOYMENT_LOCK_HELD" in source
