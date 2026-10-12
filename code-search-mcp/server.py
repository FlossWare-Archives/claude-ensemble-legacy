#!/usr/bin/env python3
"""Dependency-free MCP code search server backed by ripgrep."""

import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

CACHE_TTL = 86400
MAX_RESULTS = 200
MAX_PATTERN = 2048
CACHE_NAME = "code-search-cache.jsonl"


def repo_root() -> Path:
    result = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        text=True, capture_output=True, check=True,
    )
    return Path(result.stdout.strip()).resolve()


def cache_path() -> Path:
    base = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))
    path = base / "claude-ensemble"
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(path, 0o700)
    return path / CACHE_NAME


def safe_path(root: Path, value: str | None) -> str:
    if not value:
        return "."
    candidate = (root / value).resolve()
    if candidate != root and root not in candidate.parents:
        raise ValueError("search path must remain inside the repository")
    return str(candidate.relative_to(root) or ".")


def cache_key(root: Path, arguments: dict[str, Any]) -> str:
    payload = {"repository_root": str(root), **arguments}
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode()).hexdigest()


def cache_get(key: str) -> Any | None:
    path = cache_path()
    if not path.exists():
        return None
    try:
        with path.open() as handle:
            for line in handle:
                entry = json.loads(line)
                if entry.get("key") == key and time.time() - entry["timestamp"] < CACHE_TTL:
                    return entry["result"]
    except (OSError, json.JSONDecodeError, KeyError, TypeError):
        return None
    return None


def cache_put(key: str, result: Any) -> None:
    try:
        with cache_path().open("a") as handle:
            handle.write(json.dumps({"key": key, "timestamp": time.time(), "result": result}) + "\n")
    except OSError:
        pass


def search_code(arguments: dict[str, Any]) -> dict[str, Any]:
    pattern = arguments.get("pattern")
    if not isinstance(pattern, str) or not pattern:
        raise ValueError("pattern must be a non-empty string")
    if len(pattern) > MAX_PATTERN:
        raise ValueError(f"pattern exceeds {MAX_PATTERN} characters")

    root = repo_root()
    path = safe_path(root, arguments.get("path"))
    max_results = min(max(int(arguments.get("max_results", 50)), 1), MAX_RESULTS)
    glob = arguments.get("glob")
    fixed = bool(arguments.get("fixed_string", False))
    case_sensitive = bool(arguments.get("case_sensitive", True))

    query = {
        "pattern": pattern, "path": path, "max_results": max_results,
        "glob": glob, "fixed_string": fixed, "case_sensitive": case_sensitive,
    }
    key = cache_key(root, query)
    cached = cache_get(key)
    if cached is not None:
        return {"cached": True, **cached}

    command = ["rg", "--json", "--hidden", "--glob", "!.git", "--max-count", str(max_results)]
    if fixed:
        command.append("--fixed-strings")
    if not case_sensitive:
        command.append("--ignore-case")
    if isinstance(glob, str) and glob:
        command.extend(["--glob", glob])
    # End option parsing before either caller-controlled positional argument.
    command.extend(["--", pattern, path])

    completed = subprocess.run(
        command, cwd=root, text=True, capture_output=True, timeout=30,
    )
    if completed.returncode not in (0, 1):
        raise RuntimeError(completed.stderr.strip() or f"rg exited {completed.returncode}")

    matches = []
    for line in completed.stdout.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("type") != "match":
            continue
        data = event.get("data", {})
        matches.append({
            "path": data.get("path", {}).get("text"),
            "line": data.get("line_number"),
            "text": data.get("lines", {}).get("text", "").rstrip("\n"),
        })
        if len(matches) >= max_results:
            break

    result = {"matches": matches, "count": len(matches), "truncated": len(matches) >= max_results}
    cache_put(key, result)
    return result


def tool_result(value: Any) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": json.dumps(value, indent=2)}]}


def handle(request: dict[str, Any]) -> dict[str, Any] | None:
    method = request.get("method")
    request_id = request.get("id")

    if method == "initialize":
        return {
            "jsonrpc": "2.0", "id": request_id,
            "result": {
                "protocolVersion": request.get("params", {}).get("protocolVersion", "2024-11-05"),
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "claude-ensemble-code-search", "version": "0.1"},
            },
        }
    if method == "notifications/initialized":
        return None
    if method == "tools/list":
        return {
            "jsonrpc": "2.0", "id": request_id,
            "result": {"tools": [{
                "name": "search_code",
                "description": "Search repository code with ripgrep. Results are cached for 24 hours.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "pattern": {"type": "string"},
                        "path": {"type": "string"},
                        "glob": {"type": "string"},
                        "max_results": {"type": "integer", "minimum": 1, "maximum": MAX_RESULTS, "default": 50},
                        "fixed_string": {"type": "boolean", "default": False},
                        "case_sensitive": {"type": "boolean", "default": True},
                    },
                    "required": ["pattern"],
                },
            }]},
        }
    if method == "tools/call":
        params = request.get("params", {})
        if params.get("name") != "search_code":
            error = {"code": -32602, "message": "unknown tool"}
        else:
            try:
                return {"jsonrpc": "2.0", "id": request_id,
                        "result": tool_result(search_code(params.get("arguments", {})))}
            except Exception as exc:
                error = {"code": -32000, "message": str(exc)}
        return {"jsonrpc": "2.0", "id": request_id, "error": error}
    if request_id is None:
        return None
    return {"jsonrpc": "2.0", "id": request_id,
            "error": {"code": -32601, "message": f"method not found: {method}"}}


def main() -> None:
    for line in sys.stdin:
        try:
            response = handle(json.loads(line))
            if response is not None:
                print(json.dumps(response), flush=True)
        except json.JSONDecodeError as exc:
            print(json.dumps({"jsonrpc": "2.0", "id": None,
                              "error": {"code": -32700, "message": str(exc)}}), flush=True)


if __name__ == "__main__":
    main()
