#!/usr/bin/env python3
"""Memory inventory and listing operations.

Provides complete enumeration of all memories without using search,
complementing the search-based retrieval that injects context into prompts.

When a user asks "what have you remembered", they want:
1. A complete list of what exists in the Memory service, not just what matched a query
2. Distinction between records that exist but weren't retrieved vs. never saved
3. Honest reporting about scope and completeness

Search is for context injection (fast, relevance-ranked).
Inventory is for answering "what exists" (complete, authoritative).
"""

import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# Allow execution from the repository without requiring package installation.
sys.path.insert(0, str(Path(__file__).parent.parent))


def memory_dir() -> Path:
    """Return the Memory service directory."""
    from shared.runtime_config import memory_dir as get_memory_dir
    return get_memory_dir()


def parse_frontmatter(content: str) -> Tuple[Dict[str, Any], str]:
    """Parse YAML frontmatter from a memory file.

    Returns tuple of (parsed_frontmatter, body_content).
    """
    if not content.startswith("---"):
        return {}, content

    end_marker = content.find("---", 3)
    if end_marker < 0:
        return {}, content

    frontmatter_str = content[3:end_marker].strip()
    body = content[end_marker + 3:].strip()

    # Simple YAML parsing for our subset
    parsed = {}
    for line in frontmatter_str.split("\n"):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if ":" in line:
            key, val = line.split(":", 1)
            key = key.strip()
            val = val.strip()
            # Remove quotes
            if val.startswith('"') and val.endswith('"'):
                val = val[1:-1]
            elif val.startswith("'") and val.endswith("'"):
                val = val[1:-1]
            parsed[key] = val

    return parsed, body


def inventory_all_memories() -> List[Dict[str, Any]]:
    """Return complete inventory of all memories in the Memory service.

    Returns list of records with:
    - file: filename
    - name: memory name (from frontmatter)
    - description: one-line hook
    - type: memory type (user, feedback, project, reference)
    - body_preview: first 200 chars of body content
    - file_size: size in bytes
    - status: "valid" or "invalid"
    """
    records = []
    base_dir = memory_dir()

    if not base_dir.exists():
        return records

    for md_file in sorted(base_dir.glob("*.md")):
        try:
            content = md_file.read_text(encoding="utf-8")
            frontmatter, body = parse_frontmatter(content)

            status = "valid" if frontmatter.get("name") else "invalid"

            record = {
                "file": md_file.name,
                "name": frontmatter.get("name", ""),
                "description": frontmatter.get("description", ""),
                "type": frontmatter.get("metadata", {}) if isinstance(
                    frontmatter.get("metadata"), dict) else "unknown",
                "body_preview": body[:200].replace("\n", " "),
                "file_size": md_file.stat().st_size,
                "status": status,
            }

            # Extract type from metadata if it's a string (YAML-like)
            if isinstance(record["type"], str):
                # Try to find type in original metadata line
                match = re.search(r'type:\s*(\w+)', content)
                if match:
                    record["type"] = match.group(1)

            records.append(record)
        except Exception as e:
            records.append({
                "file": md_file.name,
                "name": "",
                "description": "",
                "type": "error",
                "body_preview": f"Error reading file: {e}",
                "file_size": 0,
                "status": "invalid",
            })

    return records


def inventory_by_type() -> Dict[str, List[Dict[str, Any]]]:
    """Return inventory grouped by memory type."""
    all_memories = inventory_all_memories()
    grouped: Dict[str, List[Dict[str, Any]]] = {
        "user": [],
        "feedback": [],
        "project": [],
        "reference": [],
        "invalid": [],
        "error": [],
    }

    for record in all_memories:
        mem_type = record.get("type", "invalid")
        if mem_type in grouped:
            grouped[mem_type].append(record)
        else:
            grouped["invalid"].append(record)

    return {k: v for k, v in grouped.items() if v}


def format_inventory_for_display() -> str:
    """Format complete memory inventory for display to user."""
    grouped = inventory_by_type()

    lines = ["# Memory Inventory\n"]

    if not grouped:
        lines.append("No memories found in the Memory service.")
        return "\n".join(lines)

    total = sum(len(v) for v in grouped.values())
    lines.append(f"**Total memories:** {total}\n")

    for mem_type in ["user", "feedback", "project", "reference"]:
        if mem_type not in grouped:
            continue

        records = grouped[mem_type]
        lines.append(f"## {mem_type.title()} ({len(records)})")
        for record in records:
            name = record.get("name", record["file"])
            desc = record.get("description", "")
            if desc:
                lines.append(f"- **{name}:** {desc}")
            else:
                lines.append(f"- **{name}**")
        lines.append("")

    if "invalid" in grouped or "error" in grouped:
        lines.append("## Issues")
        for record in grouped.get("invalid", []) + grouped.get("error", []):
            lines.append(f"- {record['file']}: {record.get('body_preview', 'Invalid')}")

    return "\n".join(lines)


if __name__ == "__main__":
    print(format_inventory_for_display())
