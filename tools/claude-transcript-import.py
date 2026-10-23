#!/usr/bin/env python3
"""Inventory and normalize Claude Code JSONL transcripts without modifying sources."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any, Iterator


def iter_records(path: Path) -> Iterator[dict[str, Any]]:
    """Yield valid JSON object records; fail with line context on malformed input."""
    with path.open("r", encoding="utf-8") as stream:
        for line_number, raw in enumerate(stream, 1):
            if not raw.strip():
                continue
            try:
                record = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{line_number}: invalid JSONL record") from exc
            if not isinstance(record, dict):
                raise ValueError(f"{path}:{line_number}: record must be an object")
            yield record


def transcript_identity(path: Path, project_root: Path | None = None) -> dict[str, Any]:
    resolved = path.resolve(strict=True)
    digest = hashlib.sha256()
    size = 0
    with resolved.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
            size += len(block)
    return {
        "source_type": "claude-code-transcript",
        "source_path": str(resolved),
        "project_root": str(project_root.resolve()) if project_root else None,
        "content_sha256": digest.hexdigest(),
        "size_bytes": size,
    }


def normalize_record(record: dict[str, Any], path: Path, line_number: int) -> dict[str, Any] | None:
    """Preserve event provenance and extract text only from known message shapes."""
    message = record.get("message")
    if not isinstance(message, dict):
        return None
    content = message.get("content")
    if isinstance(content, str):
        text = content
    elif isinstance(content, list):
        text = "\n".join(
            item["text"] for item in content
            if isinstance(item, dict) and item.get("type") == "text"
            and isinstance(item.get("text"), str)
        )
    else:
        return None
    if not text.strip():
        return None
    return {
        "source_type": "claude-code-transcript",
        "source_path": str(path.resolve()),
        "line_number": line_number,
        "session_id": record.get("sessionId") or record.get("session_id"),
        "timestamp": record.get("timestamp"),
        "role": message.get("role") or record.get("type"),
        "text": text,
    }


def inventory(root: Path) -> list[dict[str, Any]]:
    """Inventory transcript JSONL files; no writes and no transcript text in output."""
    records = []
    for path in sorted(root.expanduser().resolve().rglob("*.jsonl")):
        try:
            identity = transcript_identity(path)
            count = sum(1 for _ in iter_records(path))
            records.append({**identity, "records": count, "error": None})
        except (OSError, ValueError) as exc:
            records.append({"source_path": str(path), "error": str(exc)})
    return records


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path, help="Claude Code projects/transcripts root")
    args = parser.parse_args()
    print(json.dumps({"sources": inventory(args.root)}, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
