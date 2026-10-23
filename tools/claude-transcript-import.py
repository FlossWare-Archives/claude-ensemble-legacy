#!/usr/bin/env python3
"""Inventory and safely import local Claude Code JSONL transcripts into CE.

The default is read-only inventory. Applying requires explicit project mapping and
archives raw transcripts before sending normalized text to Memory. Original files
are never changed or deleted.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator


def iter_records(path: Path) -> Iterator[dict[str, Any]]:
    """Yield JSON object records and report malformed input with line context."""
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


def _content_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    pieces: list[str] = []
    for item in content:
        if not isinstance(item, dict):
            continue
        kind = item.get("type")
        if kind == "text" and isinstance(item.get("text"), str):
            pieces.append(item["text"])
        elif kind == "tool_use":
            name = item.get("name") if isinstance(item.get("name"), str) else "unknown"
            value = item.get("input", {})
            pieces.append(f"[Tool call: {name}]\n" + json.dumps(value, ensure_ascii=False, sort_keys=True))
        elif kind == "tool_result":
            value = item.get("content", "")
            pieces.append("[Tool result]\n" + _content_text(value))
    return "\n".join(piece for piece in pieces if piece.strip())


def normalize_record(record: dict[str, Any], path: Path, line_number: int) -> dict[str, Any] | None:
    """Normalize message text while retaining role, timestamp, session and line provenance."""
    message = record.get("message")
    if not isinstance(message, dict):
        return None
    text = _content_text(message.get("content"))
    if not text.strip():
        return None
    return {
        "source_type": "claude-code-transcript",
        "source_path": str(path.resolve()),
        "line_number": line_number,
        "session_id": record.get("sessionId") or record.get("session_id"),
        "timestamp": record.get("timestamp"),
        "role": message.get("role") or record.get("type") or "unknown",
        "text": text,
    }


def normalized_markdown(path: Path) -> tuple[str, dict[str, Any]]:
    """Render a transcript as provenance-labelled Markdown for the Memory index."""
    identity = transcript_identity(path)
    sections: list[str] = [
        "# Claude Code transcript",
        "",
        f"- Original path: `{identity['source_path']}`",
        f"- SHA-256: `{identity['content_sha256']}`",
        f"- Size: {identity['size_bytes']} bytes",
        "",
    ]
    events = 0
    session_ids: set[str] = set()
    for line_number, record in enumerate(iter_records(path), 1):
        item = normalize_record(record, path, line_number)
        if not item:
            continue
        events += 1
        if item["session_id"]:
            session_ids.add(str(item["session_id"]))
        sections.extend([
            f"## {item['role']} | {item['timestamp'] or 'timestamp unavailable'} | source line {line_number}",
            "",
            item["text"],
            "",
        ])
    identity["events"] = events
    identity["session_ids"] = sorted(session_ids)
    return "\n".join(sections).rstrip() + "\n", identity


def discover(root: Path, project_roots: list[Path]) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    """Map transcript directories to explicitly supplied project roots; never guess."""
    root = root.expanduser().resolve(strict=True)
    project_map: dict[str, list[Path]] = {}
    for supplied in project_roots:
        try:
            resolved = supplied.expanduser().resolve(strict=True)
        except (OSError, RuntimeError):
            continue
        if resolved.is_dir():
            project_map.setdefault(str(resolved).replace("/", "-"), []).append(resolved)

    found: list[dict[str, Any]] = []
    unmapped: list[dict[str, str]] = []
    for path in sorted(root.rglob("*.jsonl")):
        if path.is_symlink() or not path.is_file():
            continue
        try:
            resolved = path.resolve(strict=True)
            relative = resolved.relative_to(root)
        except (OSError, RuntimeError, ValueError):
            continue
        if not relative.parts:
            continue
        project_key = relative.parts[0]
        candidates = project_map.get(project_key, [])
        if len(candidates) != 1:
            unmapped.append({
                "project_key": project_key,
                "path": str(resolved),
                "reason": "unmapped project key" if not candidates else "ambiguous project key",
            })
            continue
        project_root = candidates[0]
        found.append({
            "path": resolved,
            "project_root": project_root,
            "scope": root / project_key,
            "project_key": project_key,
        })
    return found, unmapped


def inventory(root: Path, project_roots: list[Path] | None = None) -> list[dict[str, Any]]:
    """Read-only inventory: never includes transcript text."""
    mapped, unmapped = discover(root, project_roots or [])
    records: list[dict[str, Any]] = []
    for item in mapped:
        try:
            identity = transcript_identity(item["path"], item["project_root"])
            identity["scope"] = str(item["scope"])
            identity["project_key"] = item["project_key"]
            identity["records"] = sum(1 for _ in iter_records(item["path"]))
            records.append(identity)
        except (OSError, ValueError) as exc:
            records.append({"source_path": str(item["path"]), "error": str(exc)})
    records.extend({"unmapped": True, **item} for item in unmapped)
    return records


def _request(base_url: str, route: str, payload: dict[str, Any], timeout: float) -> dict[str, Any]:
    request = urllib.request.Request(
        base_url.rstrip("/") + route,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            result = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"{route} request failed: {type(exc).__name__}") from exc
    if not isinstance(result, dict) or result.get("ok") is not True:
        raise RuntimeError(f"{route} returned an unsuccessful response")
    return result


def archive_transcript(path: Path, identity: dict[str, Any], destination: Path) -> Path:
    """Persist immutable raw revisions and provenance manifests before indexing."""
    destination_root = destination.expanduser().absolute()
    destination_root.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(destination_root, 0o700)
    source_id = hashlib.sha256(str(path.resolve()).encode("utf-8")).hexdigest()
    source_dir = destination_root / source_id
    revisions = source_dir / "revisions"
    revisions.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(source_dir, 0o700)
    digest = identity["content_sha256"]
    target = revisions / f"{digest}.jsonl"
    if target.exists():
        if hashlib.sha256(target.read_bytes()).hexdigest() != digest:
            raise RuntimeError(f"raw archive collision at {target}")
    else:
        temporary = target.with_suffix(".tmp")
        temporary.write_bytes(path.read_bytes())
        os.chmod(temporary, 0o600)
        os.replace(temporary, target)
    metadata = {
        **identity,
        "archived_at": datetime.now(timezone.utc).isoformat(),
        "schema_version": 1,
    }
    metadata_path = revisions / f"{digest}.json"
    if not metadata_path.exists():
        temporary = metadata_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(metadata, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        os.chmod(temporary, 0o600)
        os.replace(temporary, metadata_path)
    manifest_path = source_dir / "source.json"
    previous: dict[str, Any] = {}
    if manifest_path.exists():
        previous = json.loads(manifest_path.read_text(encoding="utf-8"))
        if previous.get("source_path") != str(path.resolve()):
            raise RuntimeError(f"source archive identity collision at {manifest_path}")
    hashes = previous.get("revisions", [])
    if digest not in hashes:
        hashes.append(digest)
    manifest = {
        "schema_version": 1,
        "source_path": str(path.resolve()),
        "project_root": identity.get("project_root"),
        "latest_sha256": digest,
        "revisions": hashes,
        "last_synchronized_at": datetime.now(timezone.utc).isoformat(),
    }
    temporary = manifest_path.with_suffix(".tmp")
    temporary.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.chmod(temporary, 0o600)
    os.replace(temporary, manifest_path)
    os.chmod(manifest_path, 0o600)
    return target


def synchronize(
    sources: list[dict[str, Any]],
    base_url: str,
    timeout: float,
    destination: Path,
    expected_memory_dir: Path,
) -> dict[str, Any]:
    errors: list[dict[str, str]] = []
    successful_scopes: dict[Path, list[str]] = {}
    failed_scopes: set[Path] = set()
    processed = 0
    for source in sources:
        path: Path = source["path"]
        scope: Path = source["scope"]
        try:
            markdown, identity = normalized_markdown(path)
            identity["project_root"] = str(source["project_root"])
            identity["project_key"] = source["project_key"]
            archive_transcript(path, identity, destination)
            digest = hashlib.sha256(markdown.encode("utf-8")).hexdigest()
            metadata = {
                "scope": str(scope),
                "scope_type": "project",
                "project_root": str(source["project_root"]),
                "project_key": source["project_key"],
                "source_kind": "claude-code-transcript",
                "source_sha256": identity["content_sha256"],
                "normalized_sha256": digest,
                "event_count": identity["events"],
                "session_ids": identity["session_ids"],
                "schema_version": 1,
            }
            _request(base_url, "/memory/ingest", {
                "path": str(path),
                "content": markdown,
                "sha256": digest,
                "metadata": metadata,
                "expected_memory_dir": str(expected_memory_dir.expanduser().absolute()),
            }, timeout)
            successful_scopes.setdefault(scope, []).append(str(path))
            processed += 1
        except (OSError, UnicodeError, ValueError, RuntimeError) as exc:
            errors.append({"path": str(path), "error": str(exc)})
            failed_scopes.add(scope)
    reconciled: list[dict[str, Any]] = []
    for scope, paths in sorted(successful_scopes.items(), key=lambda pair: str(pair[0])):
        if scope in failed_scopes:
            continue
        try:
            result = _request(base_url, "/memory/reconcile", {
                "source": "claude-code",
                "scope": str(scope),
                "paths": paths,
            }, timeout)
            reconciled.append({"scope": str(scope), "stale_count": result.get("count", 0)})
        except RuntimeError as exc:
            errors.append({"scope": str(scope), "error": str(exc)})
    return {
        "mode": "apply",
        "discovered": len(sources),
        "processed": processed,
        "reconciled_scopes": reconciled,
        "errors": errors,
        "ok": not errors,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.home() / ".claude" / "projects")
    parser.add_argument("--project-root", type=Path, action="append", default=[])
    parser.add_argument("--url", default=os.environ.get("FLOSSWARE_MEMORY_URL", "http://127.0.0.1:8767"))
    parser.add_argument("--destination", type=Path, default=Path.home() / ".FlossWare" / "claude-ensemble" / "shared" / "data" / "transcripts")
    parser.add_argument("--expected-memory-dir", type=Path, default=Path.home() / ".FlossWare" / "claude-ensemble" / "shared" / "data" / "memory")
    parser.add_argument("--timeout", type=float, default=10.0)
    parser.add_argument("--apply", action="store_true", help="archive and ingest; default is read-only inventory")
    args = parser.parse_args(argv)
    if args.timeout <= 0:
        parser.error("--timeout must be positive")
    try:
        sources, unmapped = discover(args.root, args.project_root)
    except (OSError, RuntimeError) as exc:
        print(json.dumps({"ok": False, "error": type(exc).__name__}))
        return 2
    report = {
        "mode": "apply" if args.apply else "dry-run",
        "sources": inventory(args.root, args.project_root),
        "mapped_sources": len(sources),
        "unmapped": unmapped,
    }
    if args.apply:
        if not args.project_root:
            parser.error("--apply requires at least one explicit --project-root to prevent incorrect project attribution")
        result = synchronize(sources, args.url, args.timeout, args.destination, args.expected_memory_dir)
        report["result"] = result
        print(json.dumps(report, indent=2, sort_keys=True))
        return 0 if result["ok"] else 1
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
