#!/usr/bin/env python3
"""Read-only inventory planner for the CE full-corpus ingestion workflow."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any


SOURCE_NAMES = {"CLAUDE.md", "AGENTS.md", "MEMORY.md"}
SOURCE_SUFFIXES = {".md", ".jsonl"}


def inspect_source(path: Path, root: Path) -> dict[str, Any]:
    stat = path.stat()
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return {
        "path": str(path.resolve()),
        "root": str(root.resolve()),
        "relative_path": str(path.resolve().relative_to(root.resolve())),
        "bytes": stat.st_size,
        "mtime_ns": stat.st_mtime_ns,
        "sha256": digest.hexdigest(),
        "kind": "transcript" if path.suffix == ".jsonl" else "markdown",
    }


def inventory(roots: list[Path]) -> dict[str, Any]:
    found: dict[str, dict[str, Any]] = {}
    errors: list[dict[str, str]] = []
    for supplied_root in roots:
        root = supplied_root.expanduser().resolve(strict=True)
        if not root.is_dir():
            errors.append({"path": str(root), "error": "not a directory"})
            continue
        candidates = set()
        for path in root.rglob("*"):
            if not path.is_file() or path.is_symlink():
                continue
            if path.suffix.lower() in SOURCE_SUFFIXES or path.name in SOURCE_NAMES:
                candidates.add(path)
        for path in sorted(candidates):
            try:
                item = inspect_source(path, root)
                # Same physical source supplied through overlapping roots is counted once.
                found[item["path"]] = item
            except OSError as exc:
                errors.append({"path": str(path), "error": type(exc).__name__})
    items = sorted(found.values(), key=lambda item: item["path"])
    return {
        "roots": [str(path.expanduser().resolve()) for path in roots],
        "sources": items,
        "summary": {
            "sources": len(items),
            "markdown": sum(item["kind"] == "markdown" for item in items),
            "transcripts": sum(item["kind"] == "transcript" for item in items),
            "bytes": sum(item["bytes"] for item in items),
            "errors": len(errors),
        },
        "errors": errors,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", action="append", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(inventory(args.root), indent=2, sort_keys=True))
    return 0 if not inventory(args.root)["errors"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
