"""Shared provenance primitives for corpus-derived Knowledge and Graph records.

Extraction code must link every claim/entity/relationship to source evidence.
This module intentionally does not guess facts from arbitrary prose.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


def stable_id(kind: str, identity: dict[str, Any]) -> str:
    if not kind or not isinstance(identity, dict):
        raise ValueError("kind and identity object are required")
    encoded = json.dumps(identity, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    digest = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
    return f"{kind}_{digest}"


def source_evidence(
    source_path: str | Path,
    content: str,
    *,
    project_root: str | Path | None = None,
    section: str | None = None,
    line_start: int | None = None,
    line_end: int | None = None,
) -> dict[str, Any]:
    """Build a stable source reference without copying the source text."""
    path = Path(source_path).expanduser().resolve(strict=False)
    evidence: dict[str, Any] = {
        "source_path": str(path),
        "content_sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
    }
    if project_root is not None:
        evidence["project_root"] = str(Path(project_root).expanduser().resolve(strict=False))
    if section:
        evidence["section"] = section
    if line_start is not None:
        evidence["line_start"] = line_start
    if line_end is not None:
        evidence["line_end"] = line_end
    return evidence


def knowledge_record(
    claim: str,
    claim_type: str,
    evidence: dict[str, Any],
    *,
    status: str = "candidate",
    scope: str = "global",
) -> dict[str, Any]:
    """Create a reviewable claim; candidates are not silently asserted as truth."""
    allowed = {"fact", "preference", "decision", "constraint", "instruction", "procedure"}
    statuses = {"candidate", "accepted", "disputed", "superseded"}
    if claim_type not in allowed:
        raise ValueError(f"unsupported claim type: {claim_type}")
    if status not in statuses:
        raise ValueError(f"unsupported claim status: {status}")
    if not claim.strip() or not isinstance(evidence, dict) or not evidence.get("source_path"):
        raise ValueError("claim and source evidence are required")
    identity = {"claim": claim.strip(), "type": claim_type, "evidence": evidence, "scope": scope}
    return {
        "id": stable_id("knowledge", identity),
        **identity,
        "status": status,
    }
