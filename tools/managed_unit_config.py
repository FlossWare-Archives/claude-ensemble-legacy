#!/usr/bin/env python3
"""Render and validate managed CE systemd units without touching systemd."""
from __future__ import annotations

import re
import sys
from pathlib import Path

UNITS = {
    "claude-memory.service": {
        "ENSEMBLE_MEMORY_DIR": "data/memory",
        "ENSEMBLE_MEMORY_SOCKET": "runtime/memory.sock",
    },
    "claude-thompson.service": {
        "ENSEMBLE_THOMPSON_STATE_FILE": "data/thompson-sampling-state.json",
        "ENSEMBLE_THOMPSON_SOCKET": "runtime/thompson.sock",
    },
    "claude-learning.service": {
        "ENSEMBLE_LEARNING_DIR": "data/learning",
        "ENSEMBLE_LEARNING_SOCKET": "runtime/learning.sock",
    },
    "claude-graph.service": {
        "ENSEMBLE_GRAPH_STORE": "data/graph.json",
    },
    "claude-ensemble.service": {},
}


def _escape_env(value: str | Path) -> str:
    return str(value).replace("%", "%%").replace("\\", "\\\\").replace('"', '\\"')


def _escape_path(value: str | Path) -> str:
    return (
        str(value)
        .replace("%", "%%")
        .replace("\\", "\\\\")
        .replace(" ", "\\x20")
        .replace("\t", "\\x09")
    )


def configure(unit_dir: Path, root: Path, current: Path, source_root: Path | None) -> None:
    environment_file = root / "shared/config/environment"
    common = {
        "ENSEMBLE_LOG_DIR": root / "shared/logs",
        "ENSEMBLE_RUNTIME_DIR": root / "shared/runtime",
        "ENSEMBLE_REPO_ROOT": current,
        "ENSEMBLE_CLAUDE_CODE_CONFIG_DIR": root / "shared/claude-code",
        # Native Claude Code processes inherit this variable; the CE-prefixed
        # variable is retained for CE components that consume its contract.
        "CLAUDE_CONFIG_DIR": root / "shared/claude-code",
    }

    for unit, specific in UNITS.items():
        path = unit_dir / unit
        if not path.is_file():
            raise ValueError(f"missing generated service unit: {path}")
        text = path.read_text(encoding="utf-8")
        if source_root is not None:
            text = text.replace(str(source_root), str(current))
        lines = [line for line in text.splitlines() if not line.startswith("EnvironmentFile=")]
        try:
            service_index = lines.index("[Service]")
        except ValueError as exc:
            raise ValueError(f"generated unit has no [Service] section: {path}") from exc
        lines.insert(service_index + 1, f"EnvironmentFile=-{_escape_path(environment_file)}")

        values = {**common, **{name: root / "shared" / relative for name, relative in specific.items()}}
        names = set(values)
        lines = [
            line
            for line in lines
            if not any(line.startswith(f'Environment="{name}=') for name in names)
        ]
        env_lines = [f'Environment="{name}={_escape_env(value)}"' for name, value in values.items()]
        install_index = next((i for i, line in enumerate(lines) if line == "[Install]"), len(lines))
        lines[install_index:install_index] = env_lines
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    validate(unit_dir, root, current)


def validate(unit_dir: Path, root: Path, current: Path) -> None:
    environment_file = f"EnvironmentFile=-{_escape_path(root / 'shared/config/environment')}"
    required_common = (
        f'Environment="ENSEMBLE_LOG_DIR={_escape_env(root / "shared/logs")}"',
        f'Environment="ENSEMBLE_RUNTIME_DIR={_escape_env(root / "shared/runtime")}"',
        f'Environment="ENSEMBLE_REPO_ROOT={_escape_env(current)}"',
        f'Environment="ENSEMBLE_CLAUDE_CODE_CONFIG_DIR={_escape_env(root / "shared/claude-code")}"',
        f'Environment="CLAUDE_CONFIG_DIR={_escape_env(root / "shared/claude-code")}"',
        environment_file,
    )
    for unit, specific in UNITS.items():
        path = unit_dir / unit
        if not path.is_file():
            raise ValueError(f"missing generated service unit: {path}")
        text = path.read_text(encoding="utf-8")
        exec_lines = [line for line in text.splitlines() if line.startswith("ExecStart=")]
        if not exec_lines or not any(str(current) in line for line in exec_lines):
            raise ValueError(f"{unit} ExecStart does not use stable current path {current}")
        if re.search(r"/releases/[0-9]+\.[0-9]+(?:/|$)", text):
            raise ValueError(f"{unit} pins a versioned release directory")
        if "/.claude/" in text or "/.local/share/claude-ensemble" in text:
            raise ValueError(f"{unit} retains a legacy mutable-state path")
        for line in required_common:
            if line not in text:
                raise ValueError(f"{unit} is missing managed deployment setting: {line}")
        for name, relative in specific.items():
            expected = f'Environment="{name}={_escape_env(root / "shared" / relative)}"'
            if expected not in text:
                raise ValueError(f"{unit} is missing managed deployment setting: {expected}")


def main() -> int:
    if len(sys.argv) not in (5, 6) or sys.argv[1] != "configure":
        print(
            "usage: managed_unit_config.py configure UNIT_DIR ROOT CURRENT [SOURCE_ROOT]",
            file=sys.stderr,
        )
        return 2
    unit_dir, root, current = map(Path, sys.argv[2:5])
    source_root = Path(sys.argv[5]) if len(sys.argv) == 6 else None
    try:
        configure(unit_dir, root, current, source_root)
    except (OSError, ValueError) as exc:
        print(f"ERROR: managed service unit validation failed: {exc}", file=sys.stderr)
        return 1
    print("Managed service units passed preflight validation.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
