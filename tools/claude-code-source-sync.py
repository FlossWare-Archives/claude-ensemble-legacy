#!/usr/bin/env python3
"""Discover and synchronize Claude Code Markdown sources into CE Knowledge.

The command is dry-run by default. Use --apply only after reviewing the inventory.
Original Claude Code files are read-only inputs and are never moved or rewritten.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any


EXCLUDED_DIRS = {
    ".git", "node_modules", ".venv", "venv", "__pycache__", "dist", "build",
    ".next", ".cache",
}


@dataclass(frozen=True)
class Source:
    path: Path
    scope: Path
    kind: str
    scope_type: str
    project_root: str | None = None
    project_key: str | None = None

    def metadata(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "scope": str(self.scope),
            "scope_type": self.scope_type,
            "source_kind": self.kind,
            "sync_version": 1,
        }
        if self.project_root:
            result["project_root"] = self.project_root
        if self.project_key:
            result["project_key"] = self.project_key
        return result


def _markdown_files(root: Path):
    """Yield Markdown files without traversing generated/vendor directories."""
    if not root.is_dir():
        return
    for current, directories, filenames in os.walk(root, followlinks=False):
        directories[:] = sorted(
            name for name in directories
            if name not in EXCLUDED_DIRS and not (Path(current) / name).is_symlink()
        )
        for filename in sorted(filenames):
            if filename.lower().endswith(".md"):
                path = Path(current) / filename
                if path.is_file() and not path.is_symlink():
                    yield path


def encode_project_root(project_root: Path) -> str:
    """Return the common Claude Code project-directory encoding for an absolute path."""
    return str(project_root).replace("/", "-")


def discover_sources(claude_home: Path, project_roots: list[Path]) -> tuple[list[Source], set[Path], list[dict[str, str]]]:
    claude_home = claude_home.expanduser().absolute()
    sources: dict[str, Source] = {}
    reconcile_scopes: set[Path] = set()
    unmapped_auto_memory: list[dict[str, str]] = []

    def add(path: Path, scope: Path, kind: str, scope_type: str,
            project_root: Path | None = None, project_key: str | None = None) -> None:
        try:
            source_path = path.resolve(strict=True)
            scope_path = scope.resolve(strict=True)
        except (OSError, RuntimeError):
            return
        if not source_path.is_file() or not source_path.is_relative_to(scope_path):
            return
        sources[str(source_path)] = Source(
            path=source_path,
            scope=scope_path,
            kind=kind,
            scope_type=scope_type,
            project_root=str(project_root.resolve()) if project_root else None,
            project_key=project_key,
        )
        reconcile_scopes.add(scope_path)

    # Global instructions are shared across projects.
    global_file = claude_home / "CLAUDE.md"
    reconcile_scopes.add(claude_home.resolve(strict=False))
    if global_file.is_file():
        add(global_file, claude_home, "claude-md", "global")

    global_rules = claude_home / "rules"
    reconcile_scopes.add(global_rules.resolve(strict=False))
    for path in _markdown_files(global_rules):
        add(path, global_rules, "claude-rule", "global")

    normalized_roots: list[Path] = []
    project_map: dict[str, Path] = {}
    for root in project_roots:
        try:
            resolved = root.expanduser().resolve(strict=True)
        except (OSError, RuntimeError):
            continue
        if not resolved.is_dir():
            continue
        if resolved not in normalized_roots:
            normalized_roots.append(resolved)
        project_map[encode_project_root(resolved)] = resolved
        reconcile_scopes.add(resolved)
        for path in _markdown_files(resolved):
            name = path.name
            relative_parts = path.relative_to(resolved).parts
            if name == "CLAUDE.md":
                add(path, resolved, "claude-md", "project", resolved)
            elif name == "AGENTS.md":
                add(path, resolved, "agents-md", "project", resolved)
            elif ".claude" in relative_parts and "rules" in relative_parts:
                add(path, resolved, "claude-rule", "project", resolved)

    projects_dir = claude_home / "projects"
    if projects_dir.is_dir():
        for project_dir in sorted(projects_dir.iterdir()):
            memory_root = project_dir / "memory"
            if not memory_root.is_dir() or memory_root.is_symlink():
                continue
            project_key = project_dir.name
            project_root = project_map.get(project_key)
            reconcile_scopes.add(memory_root.resolve(strict=False))
            if project_root is None:
                unmapped_auto_memory.append({
                    "project_key": project_key,
                    "memory_dir": str(memory_root),
                    "reason": "no matching --project-root supplied; excluded from import to prevent cross-project leakage",
                })
                continue
            for path in _markdown_files(memory_root):
                add(path, memory_root, "auto-memory", "project", project_root, project_key)

    ordered = sorted(sources.values(), key=lambda source: (str(source.scope), str(source.path)))
    return ordered, reconcile_scopes, unmapped_auto_memory


def _request(base_url: str, route: str, payload: dict[str, Any], timeout: float) -> dict[str, Any]:
    url = base_url.rstrip("/") + route
    request = urllib.request.Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            value = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"{route} request failed: {exc}") from exc
    if not isinstance(value, dict) or value.get("ok") is not True:
        raise RuntimeError(f"{route} returned an unsuccessful response")
    return value


def source_record(source: Source) -> dict[str, Any]:
    content = source.path.read_text(encoding="utf-8")
    stat = source.path.stat()
    return {
        "path": str(source.path),
        "scope": str(source.scope),
        "kind": source.kind,
        "scope_type": source.scope_type,
        "project_root": source.project_root,
        "project_key": source.project_key,
        "bytes": stat.st_size,
        "mtime_ns": stat.st_mtime_ns,
        "sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
        "content": content,
        "metadata": source.metadata(),
    }


def source_identity(source_path: str) -> str:
    return hashlib.sha256(source_path.encode("utf-8")).hexdigest()


def source_revision_path(source: Source, destination_root: Path, digest: str) -> Path:
    return destination_root / source.kind / source_identity(str(source.path)) / "revisions" / f"{digest}.md"


def persist_source_copy(record: dict[str, Any], source: Source, destination_root: Path) -> Path:
    """Persist an immutable content-addressed source revision and update its manifest."""
    destination_root = destination_root.expanduser().absolute()
    source_dir = destination_root / source.kind / source_identity(record["path"])
    revisions_dir = source_dir / "revisions"
    revision_path = revisions_dir / f'{record["sha256"]}.md'
    manifest_path = source_dir / "source.json"

    if revision_path.exists():
        existing = hashlib.sha256(revision_path.read_bytes()).hexdigest()
        if existing != record["sha256"]:
            raise RuntimeError(f"existing revision has unexpected content: {revision_path}")
    else:
        revisions_dir.mkdir(parents=True, exist_ok=True)
        temporary = revision_path.with_suffix(".tmp")
        temporary.write_text(record["content"], encoding="utf-8")
        os.replace(temporary, revision_path)

    manifest: dict[str, Any] = {}
    if manifest_path.exists():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"cannot read source manifest {manifest_path}: {exc}") from exc
        if manifest.get("original_path") != record["path"]:
            raise RuntimeError(f"source identity collision at {manifest_path}")

    revisions = manifest.get("revisions", [])
    if not isinstance(revisions, list):
        raise RuntimeError(f"invalid revisions list in {manifest_path}")
    if record["sha256"] not in revisions:
        revisions.append(record["sha256"])
    updated = {
        "schema_version": 1,
        "original_path": record["path"],
        "source_kind": source.kind,
        "scope": record["scope"],
        "scope_type": source.scope_type,
        "project_root": source.project_root,
        "project_key": source.project_key,
        "latest_sha256": record["sha256"],
        "latest_mtime_ns": record["mtime_ns"],
        "latest_bytes": record["bytes"],
        "revisions": revisions,
        "last_synchronized_at": datetime.now(timezone.utc).isoformat(),
    }
    temporary_manifest = manifest_path.with_suffix(".tmp")
    temporary_manifest.write_text(json.dumps(updated, indent=2, sort_keys=True) + "\\n", encoding="utf-8")
    os.replace(temporary_manifest, manifest_path)
    return revision_path


def synchronize(sources: list[Source], scopes: set[Path], base_url: str, timeout: float, destination_root: Path) -> dict[str, Any]:
    records: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    successful_scopes: set[Path] = set(scopes)
    grouped_paths: dict[Path, list[str]] = {scope: [] for scope in scopes}

    for source in sources:
        try:
            record = source_record(source)
            records.append(record)
            persisted_path = persist_source_copy(record, source, destination_root)
            result = _request(base_url, "/memory/ingest", {
                "path": record["path"],
                "content": record["content"],
                "sha256": record["sha256"],
                "metadata": record["metadata"],
            }, timeout)
            grouped_paths[source.scope].append(record["path"])
            record["persisted_path"] = str(persisted_path)
            record["status"] = result.get("status", "ingested")
        except (OSError, UnicodeError, RuntimeError) as exc:
            errors.append({"path": str(source.path), "error": str(exc)})
            successful_scopes.discard(source.scope)

    reconciled: list[dict[str, Any]] = []
    for scope in sorted(successful_scopes, key=str):
        try:
            result = _request(base_url, "/memory/reconcile", {
                "source": "claude-code",
                "scope": str(scope),
                "paths": grouped_paths.get(scope, []),
            }, timeout)
            reconciled.append({"scope": str(scope), "stale_count": result.get("count", 0)})
        except RuntimeError as exc:
            errors.append({"scope": str(scope), "error": str(exc)})

    return {
        "mode": "apply",
        "discovered": len(sources),
        "ingested_or_unchanged": sum(1 for record in records if "status" in record),
        "reconciled_scopes": reconciled,
        "errors": errors,
        "ok": not errors,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--claude-home", type=Path, default=Path.home() / ".claude")
    parser.add_argument(
        "--project-root", type=Path, action="append", default=[],
        help="project root to scan; repeat for each project whose instructions/auto-memory should be ingested",
    )
    parser.add_argument("--url", default=os.environ.get("FLOSSWARE_MEMORY_URL", "http://127.0.0.1:8767"))
    parser.add_argument(
        "--destination", type=Path,
        default=Path.home() / ".FlossWare" / "claude-ensemble" / "shared" / "data" / "memory-sources",
        help="canonical source archive directory (content-addressed; originals are never modified)",
    )
    parser.add_argument("--timeout", type=float, default=5.0)
    parser.add_argument("--apply", action="store_true", help="perform import and reconciliation; default is dry-run")
    args = parser.parse_args(argv)

    if args.timeout <= 0:
        parser.error("--timeout must be positive")
    roots = args.project_root or [Path.cwd()]
    sources, scopes, unmapped = discover_sources(args.claude_home, roots)
    inventory: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    for source in sources:
        try:
            record = source_record(source)
            record.pop("content", None)
            record["destination_revision"] = str(source_revision_path(source, args.destination, record["sha256"]))
            inventory.append(record)
        except (OSError, UnicodeError) as exc:
            errors.append({"path": str(source.path), "error": str(exc)})

    report: dict[str, Any] = {
        "mode": "apply" if args.apply else "dry-run",
        "claude_home": str(args.claude_home.expanduser().absolute()),
        "project_roots": [str(root.expanduser().absolute()) for root in roots],
        "source_count": len(sources),
        "scope_count": len(scopes),
        "sources": inventory,
        "unmapped_auto_memory": unmapped,
        "inventory_errors": errors,
    }
    if args.apply:
        if errors:
            report["ok"] = False
            report["error"] = "inventory errors detected; refusing to apply partial discovery"
        else:
            result = synchronize(sources, scopes, args.url, args.timeout, args.destination)
            report["sync"] = result
            report["ok"] = result["ok"]
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report.get("ok", True) else 1


if __name__ == "__main__":
    sys.exit(main())
