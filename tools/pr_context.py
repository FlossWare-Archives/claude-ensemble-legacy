"""Load a pull request's diff and source at its immutable head commit."""

from __future__ import annotations

import base64
import json
import logging
import subprocess
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)
MAX_FILES = 30
MAX_FILE_BYTES = 200_000
MAX_TOTAL_BYTES = 1_000_000


def _gh(args: list[str], cwd: Path, purpose: str) -> str:
    result = subprocess.run(
        ["gh", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode:
        detail = (result.stderr or result.stdout).strip()
        raise RuntimeError(
            f"{purpose}: {detail or 'GitHub CLI command failed'}"
        )
    return result.stdout


def load_pull_request_context(
    orch: Any,
    repo_path: Path,
    number: str,
) -> None:
    """Load the PR diff and changed source at the exact head commit."""
    diff = _gh(
        ["pr", "diff", number],
        repo_path,
        f"Unable to fetch PR #{number} diff",
    )
    raw_metadata = _gh(
        [
            "pr",
            "view",
            number,
            "--json",
            "files,headRefOid,headRepository,headRepositoryOwner",
        ],
        repo_path,
        f"Unable to fetch PR #{number} metadata",
    )
    try:
        metadata = json.loads(raw_metadata)
        sha = metadata["headRefOid"]
        repo = metadata["headRepository"]["name"]
        owner = metadata["headRepositoryOwner"]["login"]
        changed = metadata["files"]
        if not isinstance(sha, str) or not sha or not isinstance(changed, list):
            raise ValueError("missing head SHA or changed-file list")
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise RuntimeError(
            f"GitHub returned incomplete metadata for PR #{number}"
        ) from exc

    context = orch.context_manager
    context.git_diff = diff
    context.changed_files = []
    context.files.clear()
    total = 0
    loaded = 0

    for entry in changed:
        if not isinstance(entry, dict) or not isinstance(entry.get("path"), str):
            continue
        name = entry["path"]
        context.changed_files.append(repo_path / name)
        if entry.get("status") == "removed":
            continue
        if loaded >= MAX_FILES:
            logger.warning(
                "PR #%s source context capped at %s files",
                number,
                MAX_FILES,
            )
            break

        response = _gh(
            [
                "api",
                "-X",
                "GET",
                f"repos/{owner}/{repo}/contents/{name}?ref={sha}",
            ],
            repo_path,
            f"Unable to fetch PR #{number} source at {sha}: {name}",
        )
        try:
            payload: Any = json.loads(response)
            if not isinstance(payload, dict):
                logger.warning(
                    "Skipping unexpected GitHub source response: %s",
                    name,
                )
                continue
            if payload.get("encoding") != "base64" or not isinstance(
                payload.get("content"), str
            ):
                logger.warning(
                    "Skipping source not returned inline by GitHub: %s",
                    name,
                )
                continue
            data = base64.b64decode(payload["content"])
            if len(data) > MAX_FILE_BYTES:
                logger.warning("Skipping oversized PR source file: %s", name)
                continue
            if total + len(data) > MAX_TOTAL_BYTES:
                logger.warning(
                    "PR source context reached %s-byte cap",
                    MAX_TOTAL_BYTES,
                )
                break
            source = data.decode("utf-8")
        except (ValueError, TypeError, UnicodeDecodeError) as exc:
            logger.warning("Skipping non-text PR source file %s: %s", name, exc)
            continue

        context.files[f"PR_HEAD:{sha}:{name}"] = source
        total += len(data)
        loaded += 1

    if not context.files and any(
        isinstance(item, dict) and item.get("status") != "removed"
        for item in changed
    ):
        raise RuntimeError(
            f"Could not load changed source for PR #{number}; "
            "refusing diff-only review"
        )
    logger.info(
        "Loaded PR #%s at %s: %d/%d source files (%d bytes)",
        number,
        sha[:12],
        loaded,
        len(changed),
        total,
    )
