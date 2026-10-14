#!/usr/bin/env python3
"""Read-only diagnostics for the Claude Ensemble Claude Code integration."""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

HOME = Path.home()
CLAUDE_HOME = HOME / ".claude"
REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_MEMORY_URL = "http://127.0.0.1:8767"
DEFAULT_GATEWAY_URL = "http://127.0.0.1:8080"


def check(
    checks: list[dict[str, Any]],
    code: str,
    title: str,
    status: str,
    detail: str,
    remediation: str = "",
    severity: str = "warning",
    duration_ms: int = 0,
) -> None:
    checks.append(
        {
            "code": code,
            "title": title,
            "status": status,
            "severity": severity if status in {"fail", "warn"} else "info",
            "detail": detail,
            "remediation": remediation,
            "duration_ms": duration_ms,
        }
    )


def timed_call(fn):
    start = time.monotonic()
    result = fn()
    return result, round((time.monotonic() - start) * 1000)


def http_health(url: str, timeout: float = 1.5) -> tuple[str, str]:
    request = urllib.request.Request(url, headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            if response.status != 200:
                return "fail", f"HTTP {response.status}"
            raw = response.read(64 * 1024)
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return "fail", "endpoint returned non-JSON content"
        if not isinstance(payload, dict) or payload.get("ok") is not True:
            return "fail", "endpoint did not return JSON with ok=true"
        return "pass", "HTTP 200; JSON ok=true"
    except urllib.error.HTTPError as exc:
        return "fail", f"HTTP {exc.code}"
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        reason = getattr(exc, "reason", exc)
        return "warn", f"unreachable ({type(reason).__name__})"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verbose", action="store_true", help="show passing checks too")
    parser.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    args = parser.parse_args()

    checks: list[dict[str, Any]] = []
    settings = CLAUDE_HOME / "settings.json"
    prompt_hook = CLAUDE_HOME / "hooks" / "memory-search-on-prompt.js"
    session_hook = CLAUDE_HOME / "hooks" / "session-end-memory-capture.js"
    manifest = CLAUDE_HOME / ".flossware-claude-config" / "manifest.json"
    memory_url = os.environ.get("FLOSSWARE_MEMORY_URL", DEFAULT_MEMORY_URL).rstrip("/")
    gateway_url = os.environ.get("ENSEMBLE_GATEWAY_URL", DEFAULT_GATEWAY_URL).rstrip("/")

    claude = shutil.which("claude")
    if claude:
        result, duration = timed_call(lambda: subprocess.run(
            [claude, "--version"], capture_output=True, text=True, timeout=2, check=False
        ))
        status = "pass" if result.returncode == 0 else "warn"
        check(checks, "CLAUDE_CLI", "Claude Code CLI", status,
              result.stdout.strip() or "version command failed",
              "Install Claude Code or repair PATH." if status != "pass" else "", duration_ms=duration)
    else:
        check(checks, "CLAUDE_CLI_MISSING", "Claude Code CLI", "fail",
              "claude executable not found on PATH", "Install Claude Code and ensure it is on PATH.", "error")

    check(checks, "CLAUDE_HOME", "Claude configuration directory",
          "pass" if CLAUDE_HOME.is_dir() else "fail",
          str(CLAUDE_HOME) if CLAUDE_HOME.is_dir() else f"missing: {CLAUDE_HOME}",
          "Create ~/.claude and rerun the CE configuration installer." if not CLAUDE_HOME.is_dir() else "",
          "error")

    for code, title, path, remediation in (
        ("SETTINGS_MISSING", "Claude settings", settings, "Run claude-config install, then review settings.json."),
        ("PROMPT_HOOK_MISSING", "Prompt Memory hook", prompt_hook, "Run claude-config install and verify the UserPromptSubmit hook."),
        ("SESSION_HOOK_MISSING", "SessionEnd capture hook", session_hook, "Run claude-config install and verify the SessionEnd hook."),
        ("MANIFEST_MISSING", "Managed-file manifest", manifest, "Run claude-config install to create or reconcile the managed-file manifest."),
    ):
        exists = path.is_file()
        check(checks, code, title, "pass" if exists else "fail",
              str(path) if exists else f"missing: {path}", "" if exists else remediation,
              "error")

    if settings.is_file():
        try:
            data = json.loads(settings.read_text(encoding="utf-8"))
            valid = isinstance(data, dict)
            detail = "valid JSON object" if valid else "settings root must be a JSON object"
        except (OSError, json.JSONDecodeError) as exc:
            valid, detail = False, f"settings cannot be parsed ({type(exc).__name__})"
        check(checks, "SETTINGS_JSON", "Settings JSON", "pass" if valid else "fail",
              detail, "" if valid else "Repair settings.json JSON syntax and rerun claude-config verify.",
              "error")

    validator = REPO_ROOT / "tools" / "claude-config" / "lib" / "json_tool.py"
    if settings.is_file() and prompt_hook.is_file() and session_hook.is_file() and validator.is_file():
        result, duration = timed_call(lambda: subprocess.run(
            [sys.executable, str(validator), "validate-settings", str(settings),
             str(prompt_hook), str(session_hook)],
            capture_output=True, text=True, timeout=2, check=False
        ))
        valid_hooks = result.returncode == 0
        check(checks, "HOOK_REGISTRATION", "Claude hook registration",
              "pass" if valid_hooks else "fail",
              "managed UserPromptSubmit and unconditional SessionEnd hooks are registered"
              if valid_hooks else "settings do not register the managed prompt and SessionEnd hooks correctly",
              "" if valid_hooks else "Run claude-config install, then review settings.json hook registration.",
              "error", duration)
    elif not validator.is_file():
        check(checks, "HOOK_VALIDATOR_MISSING", "Claude hook registration", "skip",
              "settings validator is missing from this checkout")

    if shutil.which("systemctl"):
        result, duration = timed_call(lambda: subprocess.run(
            ["systemctl", "--user", "is-active", "--quiet", "claude-ensemble.service"],
            capture_output=True, timeout=2, check=False
        ))
        active = result.returncode == 0
        check(checks, "GATEWAY_UNIT", "Gateway systemd user unit",
              "pass" if active else "warn",
              "active" if active else "not active (the gateway may be managed another way)",
              "" if active else "Start claude-ensemble.service if this installation uses systemd.",
              duration_ms=duration)
    else:
        check(checks, "SYSTEMCTL_UNAVAILABLE", "Gateway systemd user unit", "skip",
              "systemctl is unavailable; unit state was not checked")

    for code, title, url, remediation in (
        ("MEMORY_HEALTH", "Memory REST health", f"{memory_url}/health",
         "Check the Memory service and FLOSSWARE_MEMORY_URL. Prompt hooks are designed to fail open."),
        ("GATEWAY_HEALTH", "Gateway REST health", f"{gateway_url}/api/v1/health",
         "Check claude-ensemble.service, gateway logs, and ENSEMBLE_GATEWAY_URL."),
        ("GATEWAY_CAPABILITIES", "Gateway capability health", f"{gateway_url}/api/v1/capabilities/health",
         "Check the gateway capability registry and configured provider setup."),
    ):
        (status, detail), duration = timed_call(lambda url=url: http_health(url))
        severity = "error" if status == "fail" and code == "GATEWAY_HEALTH" else "warning"
        check(checks, code, title, status, detail,
              "" if status == "pass" else remediation, severity, duration)

    failures = [item for item in checks if item["status"] == "fail"]
    warnings = [item for item in checks if item["status"] == "warn"]
    result = {
        "ok": not failures,
        "summary": {"passed": sum(c["status"] == "pass" for c in checks),
                    "failed": len(failures), "warnings": len(warnings),
                    "skipped": sum(c["status"] == "skip" for c in checks)},
        "checks": checks,
    }
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        shown = checks if args.verbose else [c for c in checks if c["status"] in {"fail", "warn", "skip"}]
        if not shown:
            print("CE doctor: no actionable problems found.")
        else:
            for item in shown:
                mark = {"pass": "✓", "fail": "✗", "warn": "!", "skip": "-"}.get(item["status"], "?")
                print(f"{mark} [{item['code']}] {item['title']}: {item['detail']}")
                if item["remediation"]:
                    print(f"    Fix: {item['remediation']}")
        print("Summary: {passed} passed, {failed} failed, {warnings} warnings, {skipped} skipped".format(**result["summary"]))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
