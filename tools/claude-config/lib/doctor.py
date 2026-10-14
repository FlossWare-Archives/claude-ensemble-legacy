#!/usr/bin/env python3
"""Read-only diagnostics for the Claude Ensemble Claude Code integration."""
from __future__ import annotations

import argparse
from dataclasses import dataclass, field
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Mapping

DEFAULT_MEMORY_URL = "http://127.0.0.1:8767"
DEFAULT_GATEWAY_URL = "http://127.0.0.1:8080"

@dataclass(frozen=True)
class DoctorConfig:
    """Injectable filesystem and environment context for doctor diagnostics."""

    home: Path = field(default_factory=Path.home)
    claude_home: Path | None = None
    repo_root: Path = field(default_factory=lambda: Path(__file__).resolve().parents[3])
    environ: Mapping[str, str] = field(default_factory=lambda: os.environ)

    def __post_init__(self) -> None:
        if self.claude_home is None:
            object.__setattr__(self, "claude_home", self.home / ".claude")


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
    expected_provider: str = "vertex",
    timeout: float = 3.0,
    *,
    environ: Mapping[str, str] | None = None,
    home: Path | None = None,
) -> tuple[str, str]:
    """Check only an explicitly selected Claude profile; never inspect the default profile implicitly."""
    if not profile:
        return "skip", (
            "no dedicated Claude Code profile configured; the default profile was not inspected "
            "because it may belong to a separately managed work environment"
        )
    if not claude:
        return "fail", "Claude Code executable not found"
    profile_path = Path(profile)
    if str(profile).startswith("~/") and home is not None:
        profile_path = home / str(profile)[2:]
    else:
        profile_path = profile_path.expanduser()
    if not profile_path.is_dir():
        return "fail", "configured Claude Code profile directory does not exist"
    env = dict(os.environ if environ is None else environ)
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
    if not isinstance(status, dict) or status.get("loggedIn") is not True:
        return "fail", "configured CE profile is not authenticated"
    actual = status.get("apiProvider") if isinstance(status, dict) else None
    if not isinstance(actual, str) or not actual.strip():
        return "fail", "auth status did not identify the upstream provider"
    actual_provider = actual.strip().lower()
    if not isinstance(expected_provider, str) or not expected_provider.strip():
        return "fail", "expected provider is blank; set ENSEMBLE_CLAUDE_CODE_EXPECTED_PROVIDER to a valid provider"
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
            # Read one byte beyond the cap so oversized responses fail explicitly.
            raw = response.read((64 * 1024) + 1)
        if len(raw) > 64 * 1024:
            return "fail", "endpoint response exceeded the 64 KiB limit"
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return "fail", "endpoint returned non-JSON content"
        if not isinstance(payload, dict) or payload.get("ok") is not True:
            return "fail", "endpoint did not return JSON with ok=true"
        return "pass", "HTTP 200; JSON ok=true"
    except urllib.error.HTTPError as exc:
        # HTTPError is also a response object; close it to release the socket promptly.
        exc.close()
        return "fail", f"HTTP {exc.code}"
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        reason = getattr(exc, "reason", exc)
        return "warn", f"unreachable ({type(reason).__name__})"


def main(config: DoctorConfig | None = None) -> int:
    config = config or DoctorConfig()
    repo_root = config.repo_root
    environ = config.environ
    # Only inspect Claude profile files when a dedicated profile is explicitly selected.
    # The default ~/.claude may be managed by a separate work environment (for example Vertex).
    profile_value = (
        environ.get("ENSEMBLE_CLAUDE_CODE_CONFIG_DIR")
        or environ.get("CLAUDE_CONFIG_DIR")
    )
    claude_home: Path | None = None
    if profile_value:
        candidate = Path(profile_value)
        if str(profile_value).startswith("~/"):
            claude_home = config.home / str(profile_value)[2:]
        else:
            claude_home = candidate.expanduser()
    elif config.claude_home is not None and environ.get("CE_DOCTOR_ALLOW_INJECTED_PROFILE") == "1":
        # Explicit test-only injection; production runs never infer ~/.claude as the CE profile.
        claude_home = config.claude_home

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verbose", action="store_true", help="show passing checks too")
    parser.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    args = parser.parse_args()

    checks: list[dict[str, Any]] = []
    settings = claude_home / "settings.json" if claude_home is not None else None
    prompt_hook = claude_home / "hooks" / "memory-search-on-prompt.js" if claude_home is not None else None
    session_hook = claude_home / "hooks" / "session-end-memory-capture.js" if claude_home is not None else None
    manifest = claude_home / ".flossware-claude-config" / "manifest.json" if claude_home is not None else None
    memory_url = environ.get("FLOSSWARE_MEMORY_URL", DEFAULT_MEMORY_URL).rstrip("/")
    gateway_url = environ.get("ENSEMBLE_GATEWAY_URL", DEFAULT_GATEWAY_URL).rstrip("/")

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

    if claude_home is None:
        for code, title in (
            ("CLAUDE_HOME", "Claude configuration directory"),
            ("SETTINGS_MISSING", "Claude settings"),
            ("PROMPT_HOOK_MISSING", "Prompt Memory hook"),
            ("SESSION_HOOK_MISSING", "SessionEnd capture hook"),
            ("MANIFEST_MISSING", "Managed-file manifest"),
            ("SETTINGS_JSON", "Settings JSON"),
            ("HOOK_REGISTRATION", "Claude hook registration"),
        ):
            check(
                checks, code, title, "skip",
                "no dedicated CE profile configured; the default Claude profile was not inspected",
            )
    else:
        assert settings is not None and prompt_hook is not None
        assert session_hook is not None and manifest is not None
        check(checks, "CLAUDE_HOME", "Claude configuration directory",
              "pass" if claude_home.is_dir() else "fail",
              str(claude_home) if claude_home.is_dir() else f"missing: {claude_home}",
              "Create the configured Claude directory and rerun the CE configuration installer." if not claude_home.is_dir() else "",
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

        validator = repo_root / "tools" / "claude-config" / "lib" / "json_tool.py"
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

    provider_profile = profile_value
    expected_provider = environ.get("ENSEMBLE_CLAUDE_CODE_EXPECTED_PROVIDER", "vertex")
    (provider_status, provider_detail), provider_duration = timed_call(
        lambda: provider_health(claude, provider_profile, expected_provider, environ=environ, home=config.home)
    )
    check(
        checks, "CLAUDE_PROVIDER_POLICY", "Claude Code provider policy",
        provider_status, provider_detail,
        "Configure the selected CE profile for the expected provider; do not repoint a separately managed work profile."
        if provider_status == "fail" else "",
        "error", provider_duration,
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
