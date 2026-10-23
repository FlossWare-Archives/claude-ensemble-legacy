#!/usr/bin/env python3
"""Run one dry-run-first Claude Code corpus sync for instructions and transcripts.

The default only inventories metadata. --apply requires explicit project roots,
preflights both source families, then archives and ingests them into Memory.
Original Claude Code files are never modified.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any


def _run_json(command: list[str]) -> tuple[int, dict[str, Any]]:
    completed = subprocess.run(command, capture_output=True, text=True, check=False)
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        detail = completed.stderr.strip().splitlines()[-1:] or ["no diagnostic output"]
        raise RuntimeError(
            f"{Path(command[1]).name} did not return JSON (exit {completed.returncode}): {detail[0]}"
        ) from exc
    if not isinstance(payload, dict):
        raise RuntimeError(f"{Path(command[1]).name} returned a non-object JSON report")
    return completed.returncode, payload


def _has_inventory_errors(report: dict[str, Any], kind: str) -> bool:
    if report.get("ok") is False:
        return True
    if kind == "instructions":
        return bool(report.get("inventory_errors"))
    return any(isinstance(item, dict) and item.get("error") for item in report.get("sources", []))


def run_sync(
    *,
    repo_root: Path,
    claude_home: Path,
    project_roots: list[Path],
    memory_url: str,
    memory_dir: Path,
    source_archive: Path,
    transcript_archive: Path,
    timeout: float,
    apply: bool,
) -> dict[str, Any]:
    python = sys.executable
    source_script = repo_root / "tools" / "claude-code-source-sync.py"
    transcript_script = repo_root / "tools" / "claude-transcript-import.py"
    common = [
        "--url", memory_url,
        "--expected-memory-dir", str(memory_dir),
        "--timeout", str(timeout),
    ]
    source_command = [
        python, str(source_script),
        "--claude-home", str(claude_home),
        "--destination", str(source_archive),
        *common,
    ]
    transcript_command = [
        python, str(transcript_script),
        "--root", str(claude_home / "projects"),
        "--destination", str(transcript_archive),
        *common,
    ]
    for root in project_roots:
        source_command.extend(["--project-root", str(root)])
        transcript_command.extend(["--project-root", str(root)])

    source_code, source_report = _run_json(source_command)
    transcript_code, transcript_report = _run_json(transcript_command)
    preflight_errors = []
    if source_code != 0 or _has_inventory_errors(source_report, "instructions"):
        preflight_errors.append("instruction/auto-memory inventory reported errors")
    if transcript_code != 0 or _has_inventory_errors(transcript_report, "transcripts"):
        preflight_errors.append("transcript inventory reported errors")

    result: dict[str, Any] = {
        "mode": "apply" if apply else "dry-run",
        "claude_home": str(claude_home),
        "project_roots": [str(root) for root in project_roots],
        "memory_url": memory_url,
        "expected_memory_dir": str(memory_dir),
        "instruction_inventory": {
            "source_count": source_report.get("source_count", 0),
            "scope_count": source_report.get("scope_count", 0),
            "unmapped_auto_memory_count": len(source_report.get("unmapped_auto_memory", [])),
            "inventory_errors": source_report.get("inventory_errors", []),
        },
        "transcript_inventory": {
            "mapped_sources": transcript_report.get("mapped_sources", 0),
            "unmapped_sources": len(transcript_report.get("unmapped", [])),
            "source_errors": sum(
                1 for item in transcript_report.get("sources", [])
                if isinstance(item, dict) and item.get("error")
            ),
        },
        "preflight_errors": preflight_errors,
        "ok": not preflight_errors,
    }
    if not apply or preflight_errors:
        return result

    # Both source families were inventoried successfully before the first write.
    # Each child performs its own scope-safe reconciliation and archives originals.
    transcript_command.append("--apply")
    transcript_apply_code, transcript_apply = _run_json(transcript_command)
    result["transcript_apply"] = transcript_apply
    if transcript_apply_code != 0 or transcript_apply.get("result", {}).get("ok") is False:
        result["ok"] = False
        result.setdefault("apply_errors", []).append("transcript import failed")
        return result

    source_command.append("--apply")
    source_apply_code, source_apply = _run_json(source_command)
    result["instruction_apply"] = source_apply
    if source_apply_code != 0 or source_apply.get("ok") is False:
        result["ok"] = False
        result.setdefault("apply_errors", []).append("instruction/auto-memory import failed")
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--claude-home", type=Path, default=Path.home() / ".claude")
    parser.add_argument("--project-root", type=Path, action="append", default=[])
    install_root = Path(os.environ.get(
        "ENSEMBLE_INSTALL_ROOT", Path.home() / ".FlossWare" / "claude-ensemble"
    )).expanduser()
    parser.add_argument("--url", default=os.environ.get("FLOSSWARE_MEMORY_URL", "http://127.0.0.1:8767"))
    parser.add_argument(
        "--memory-dir", type=Path,
        default=Path(os.environ.get("ENSEMBLE_MEMORY_DIR", install_root / "shared" / "data" / "memory")).expanduser(),
    )
    parser.add_argument(
        "--source-archive", type=Path,
        default=Path(os.environ.get("ENSEMBLE_MEMORY_SOURCES_DIR", install_root / "shared" / "data" / "memory-sources")).expanduser(),
    )
    parser.add_argument(
        "--transcript-archive", type=Path,
        default=install_root / "shared" / "data" / "transcripts",
    )
    parser.add_argument("--timeout", type=float, default=10.0)
    parser.add_argument("--apply", action="store_true", help="archive and ingest after both inventories pass")
    args = parser.parse_args(argv)
    if args.timeout <= 0:
        parser.error("--timeout must be positive")
    if args.apply and not args.project_root:
        parser.error("--apply requires one or more explicit --project-root arguments")
    roots = [root.expanduser().resolve(strict=True) for root in args.project_root]
    try:
        result = run_sync(
            repo_root=args.repo_root.expanduser().resolve(strict=True),
            claude_home=args.claude_home.expanduser().resolve(strict=True),
            project_roots=roots,
            memory_url=args.url,
            memory_dir=args.memory_dir,
            source_archive=args.source_archive,
            transcript_archive=args.transcript_archive,
            timeout=args.timeout,
            apply=args.apply,
        )
    except (OSError, RuntimeError, ValueError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, indent=2, sort_keys=True))
        return 2
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
