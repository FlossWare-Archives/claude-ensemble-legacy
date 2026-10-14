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


def run_bounded(command: list[str], timeout: float = 2.0, text: bool = True, env: dict[str, str] | None = None):
    try:
        return subprocess.run(
            command, capture_output=True, text=text, timeout=timeout, check=False, env=env
        ), None
    except (OSError, subprocess.TimeoutExpired) as exc:
        return None, type(exc).__name__


def provider_health(
    claude: str | None,
    profile: str | None,
    expected_provider: str = "anthropic",
    timeout: float = 3.0,
) -> tuple[str, str]:
    """Check only an explicitly selected Claude profile; never inspect the default profile implicitly."""
    if not profile:
        return "skip", (
            "no dedicated Claude Code profile configured; the default profile was not inspected "
            "because it may belong to a separately managed work environment"
        )
    if not claude:
        return "fail", "Claude Code executable not found"
    profile_path = Path(profile).expanduser()
    if not profile_path.is_dir():
        return "fail", "configured Claude Code profile directory does not exist"
    env = os.environ.copy()
    env["CLAUDE_CONFIG_DIR"] = str(profile_path)
    result, error = run_bounded([claude, "auth", "status"], timeout=timeout, env=env)
    if result is None:
        return "fail", f"auth status could not be checked ({error})"
    if result.returncode != 0:
        return "fail", "auth status failed for the configured CE profile"
    try:
        status = json.loads(result.stdout)
    except json.JSONDecodeError:
        return "fail", "auth status returned invalid JSON"
    actual = status.get("apiProvider") if isinstance(status, dict) else None
    if not isinstance(actual, str) or not actual.strip():
        return "fail", "auth status did not identify the upstream provider"
    actual_provider = actual.strip().lower()
    expected = expected_provider.strip().lower()
    # Claude Code's native Anthropic route is labelled "firstParty" by auth status.
    if actual_provider == "firstparty":
        actual_provider = "anthropic"
    if expected == "firstparty":
        expected = "anthropic"
    if actual_provider != expected:
        return "fail", (
            f"configured CE profile uses provider {actual_provider!r}; "
            f"expected {expected!r}"
        )
    return "pass", f"configured CE profile uses expected provider {expected!r}"


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
        started = time.monotonic()
        result, error = run_bounded([claude, "--version"], timeout=2)
        duration = round((time.monotonic() - started) * 1000)
        status = "pass" if result is not None and result.returncode == 0 else "warn"
        detail = (result.stdout.strip() or "version command failed") if result is not None else f"version command unavailable ({error})"
        check(checks, "CLAUDE_CLI", "Claude Code CLI", status,
              detail, "Install Claude Code or repair PATH." if status != "pass" else "", duration_ms=duration)
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
        started = time.monotonic()
        result, error = run_bounded(
            [sys.executable, str(validator), "validate-settings", str(settings),
             str(prompt_hook), str(session_hook)], timeout=2
        )
        duration = round((time.monotonic() - started) * 1000)
        valid_hooks = result is not None and result.returncode == 0
        check(checks, "HOOK_REGISTRATION", "Claude hook registration",
              "pass" if valid_hooks else "fail",
              "managed UserPromptSubmit and unconditional SessionEnd hooks are registered"
              if valid_hooks else "settings do not register the managed prompt and SessionEnd hooks correctly",
              "" if valid_hooks else "Run claude-config install, then review settings.json hook registration.",
              "error", duration)
    elif not validator.is_file():
        check(checks, "HOOK_VALIDATOR_MISSING", "Claude hook registration", "skip",
              "settings validator is missing from this checkout")

    provider_profile = (
        os.environ.get("ENSEMBLE_CLAUDE_CODE_CONFIG_DIR")
        or os.environ.get("CLAUDE_CONFIG_DIR")
    )
    expected_provider = os.environ.get("ENSEMBLE_CLAUDE_CODE_EXPECTED_PROVIDER", "anthropic")
    provider_status, provider_detail = provider_health(claude, provider_profile, expected_provider)
    check(
        checks, "CLAUDE_PROVIDER_POLICY", "Claude Code provider policy",
        provider_status, provider_detail,
        "Configure ENSEMBLE_CLAUDE_CODE_CONFIG_DIR to a dedicated CE profile authenticated with Anthropic; do not repoint a separately managed work profile.",
        "error",
    )

    if shutil.which("systemctl"):
        started = time.monotonic()
        result, error = run_bounded(
            ["systemctl", "--user", "is-active", "--quiet", "claude-ensemble.service"],
            timeout=2, text=False
        )
        duration = round((time.monotonic() - started) * 1000)
        active = result is not None and result.returncode == 0
        check(checks, "GATEWAY_UNIT", "Gateway systemd user unit",
              "pass" if active else "warn",
              "active" if active else (f"unit state unavailable ({error})" if result is None else "not active (the gateway may be managed another way)"),
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
