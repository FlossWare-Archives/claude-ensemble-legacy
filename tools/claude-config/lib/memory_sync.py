#!/usr/bin/env python3
"""Claude Code Markdown synchronization for the Ensemble Memory service."""
from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import os
import select
import struct
import sys
import time
from pathlib import Path
from urllib import error, request

DEFAULT_URL = "http://127.0.0.1:8767"
EXCLUDED_NAMES = {"CHANGELOG.md"}
EVENT_STRUCT = struct.Struct("iIII")


def memory_url() -> str:
    return os.environ.get("FLOSSWARE_MEMORY_URL", DEFAULT_URL).rstrip("/")


def claude_root() -> Path:
    return Path(os.environ.get("CLAUDE_CONFIG_ROOT", Path.home() / ".claude")).expanduser()


def markdown_files(root: Path) -> list[Path]:
    projects = root / "projects"
    if not projects.is_dir():
        return []
    found: list[Path] = []
    for project in projects.iterdir():
        if not project.is_dir():
            continue
        memory = project / "memory"
        if memory.is_dir():
            found.extend(p for p in memory.rglob("*.md") if p.name not in EXCLUDED_NAMES)
        top = project / "MEMORY.md"
        if top.is_file():
            found.append(top)
    return sorted(set(found))


def project_name(root: Path, path: Path) -> str:
    try:
        relative = path.relative_to(root / "projects")
        return relative.parts[0] if relative.parts else "unknown"
    except ValueError:
        return "unknown"


def payload(root: Path, path: Path) -> dict:
    content = path.read_text(encoding="utf-8")
    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
    return {
        "source": "claude-code",
        "path": str(path.resolve()),
        "content": content,
        "sha256": digest,
        "metadata": {
            "project": project_name(root, path),
            "kind": "markdown",
            "scope": str(root.resolve()),
            "relative_path": str(path.relative_to(root / "projects")),
        },
    }


def post(path: str, body: dict, timeout: float = 2.0) -> dict:
    data = json.dumps(body).encode("utf-8")
    req = request.Request(
        memory_url() + path,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with request.urlopen(req, timeout=timeout) as response:
        value = json.loads(response.read().decode("utf-8"))
    if not isinstance(value, dict) or not value.get("ok"):
        raise RuntimeError(value.get("error", "Memory request failed") if isinstance(value, dict) else "invalid response")
    return value


def sync_once(root: Path) -> int:
    if not root.is_dir():
        print(f"warning: Claude Code root does not exist: {root}", file=sys.stderr)
        return 1
    scope = str(root.resolve())
    paths = markdown_files(root)
    succeeded = 0
    for path in paths:
        try:
            result = post("/memory/ingest", payload(root, path))
            print(f"{result.get('status', 'ingested')}: {path}")
            succeeded += 1
        except (OSError, ValueError, RuntimeError, error.URLError) as exc:
            print(f"warning: Memory ingest failed for {path}: {exc}", file=sys.stderr)
    try:
        result = post(
            "/memory/reconcile",
            {"source": "claude-code", "scope": scope, "paths": [str(p.resolve()) for p in paths]},
        )
        print(f"reconciled: {result.get('count', 0)} stale document(s)")
    except (OSError, ValueError, RuntimeError, error.URLError) as exc:
        print(f"warning: Memory reconciliation failed: {exc}", file=sys.stderr)
        return 1
    return 0 if succeeded == len(paths) else 1


def sync_knowledge_root(root: Path) -> int:
    """Index Markdown documentation under an explicitly selected project root.

    This is intentionally a separate command from syncing ~/.claude memory:
    project documentation is durable Knowledge, while session notes remain
    episodic Memory. Reconciliation is scoped to the selected root and occurs
    only after every current document was ingested successfully.
    """
    if not root.is_dir():
        print(f"warning: Knowledge root does not exist: {root}", file=sys.stderr)
        return 1

    scope = str(root.resolve())
    excluded_dirs = {".git", ".venv", "venv", "node_modules", "__pycache__", "build", "dist"}
    paths = []
    for directory, dirs, files in os.walk(root):
        dirs[:] = sorted(name for name in dirs if name not in excluded_dirs)
        for name in files:
            path = Path(directory) / name
            if path.suffix.lower() == ".md" and name not in EXCLUDED_NAMES:
                paths.append(path)
    paths = sorted(path.resolve() for path in paths)
    succeeded = 0
    for path in paths:
        try:
            content = path.read_text(encoding="utf-8")
            body = {
                "source": "claude-code",
                "path": str(path),
                "content": content,
                "sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
                "metadata": {
                    "project": root.name,
                    "kind": "markdown",
                    "scope": scope,
                    "relative_path": str(path.relative_to(root.resolve())),
                },
            }
            result = post("/memory/ingest", body)
            print(f"{result.get('status', 'ingested')}: {path}")
            succeeded += 1
        except (OSError, ValueError, RuntimeError, error.URLError) as exc:
            print(f"warning: Knowledge ingest failed for {path}: {exc}", file=sys.stderr)

    if succeeded != len(paths):
        print("warning: Knowledge reconciliation skipped because one or more ingests failed", file=sys.stderr)
        return 1
    try:
        result = post(
            "/memory/reconcile",
            {"source": "claude-code", "scope": scope, "paths": [str(path) for path in paths]},
        )
        print(f"reconciled: {result.get('count', 0)} stale Knowledge document(s)")
    except (OSError, ValueError, RuntimeError, error.URLError) as exc:
        print(f"warning: Knowledge reconciliation failed: {exc}", file=sys.stderr)
        return 1
    return 0


def snapshot(root: Path) -> dict[str, tuple[int, int]]:
    result = {}
    for path in markdown_files(root):
        try:
            stat = path.stat()
            result[str(path)] = (stat.st_mtime_ns, stat.st_size)
        except OSError:
            pass
    return result


def inotify_fds(root: Path) -> tuple[int, dict[int, Path]] | None:
    if sys.platform != "linux":
        return None
    try:
        libc = ctypes.CDLL("libc.so.6", use_errno=True)
        fd = libc.inotify_init1(os.O_NONBLOCK | os.O_CLOEXEC)
        if fd < 0:
            return None
        mask = 0x00000100 | 0x00000200 | 0x00000002 | 0x00000004 | 0x00000008 | 0x00000040 | 0x00000080
        watches: dict[int, Path] = {}
        projects = root / "projects"
        if not projects.is_dir():
            os.close(fd)
            return None
        for directory, dirs, _ in os.walk(projects):
            directory_path = Path(directory)
            watch_id = libc.inotify_add_watch(fd, os.fsencode(str(directory_path)), mask)
            if watch_id >= 0:
                watches[watch_id] = directory_path
        if not watches:
            os.close(fd)
            return None
        return (fd, watches)
    except (OSError, AttributeError):
        return None



MAX_RETRY_DELAY = 60.0


def polling_watch(
    root: Path,
    interval: float,
    previous: dict[str, tuple[int, int]] | None = None,
    pending: bool = True,
) -> int:
    previous = snapshot(root) if previous is None else previous
    retry_delay = max(interval, 0.1)
    next_retry = 0.0
    while True:
        time.sleep(interval)
        current = snapshot(root)
        now = time.monotonic()
        if (pending or current != previous) and now >= next_retry:
            if sync_once(root) == 0:
                previous = current
                pending = False
                retry_delay = max(interval, 0.1)
                next_retry = 0.0
            else:
                pending = True
                next_retry = now + retry_delay
                retry_delay = min(retry_delay * 2, MAX_RETRY_DELAY)


def watch(root: Path, interval: float) -> int:
    if not root.is_dir():
        print(f"warning: Claude Code root does not exist: {root}", file=sys.stderr)
        return 1

    initial = sync_once(root)
    previous = snapshot(root)
    pending = initial != 0
    retry_delay = max(interval, 0.1)
    next_retry = time.monotonic() + retry_delay if pending else 0.0

    watched = inotify_fds(root)
    if watched is None:
        print("inotify unavailable; using polling watcher", file=sys.stderr)
        return polling_watch(root, interval, previous, pending)

    fd, watches = watched
    print("watching Claude Code Markdown with recursive inotify")
    try:
        while True:
            ready, _, _ = select.select([fd], [], [], interval)
            current = snapshot(root)
            now = time.monotonic()
            if (pending or current != previous) and now >= next_retry:
                if sync_once(root) == 0:
                    previous = current
                    pending = False
                    retry_delay = max(interval, 0.1)
                    next_retry = 0.0
                else:
                    pending = True
                    next_retry = now + retry_delay
                    retry_delay = min(retry_delay * 2, MAX_RETRY_DELAY)
            if ready:
                os.read(fd, 65536)
                os.close(fd)
                fd = -1
                refreshed = inotify_fds(root)
                if refreshed is None:
                    print("inotify watch refresh unavailable; continuing with polling", file=sys.stderr)
                    return polling_watch(root, interval, previous)
                fd, watches = refreshed
    finally:
        if fd >= 0:
            os.close(fd)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("sync", "watch", "knowledge-sync"))
    parser.add_argument("--root", default=None)
    parser.add_argument("--interval", type=float, default=2.0)
    args = parser.parse_args()
    if args.command == "knowledge-sync":
        if not args.root:
            parser.error("knowledge-sync requires --root to select a project documentation directory")
        return sync_knowledge_root(Path(args.root).expanduser())
    root = Path(args.root).expanduser() if args.root else claude_root()
    if args.command == "sync":
        return sync_once(root)
    return watch(root, args.interval)


if __name__ == "__main__":
    raise SystemExit(main())
