#!/usr/bin/env python3
"""Run non-GA Python tests with isolated imports and explicit script checks."""
from __future__ import annotations
import os
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXCLUDED_PARTS = {".git", ".venv", "node_modules", "ga_tuning"}
EXCLUDED_FILES = {"learning-service/test_ga_artifact_contract.py", "caching/test_cases.py"}
SCRIPT_TESTS = {
    "caching/test_blocker_fixes.py",
    "graph-service/test_graph_service.py",
    "learning-service/test_learning_service.py",
    "server/test_ensemble_server.py",
    "shared/test_circuit_breaker.py",
    "test/test_service_installers.py",
    "test/test_service_lifecycle.py",
}
LIVE_SCRIPT_TESTS = {"caching/test_anthropic_api.py"}
LIVE_REVIEWER_SCRIPT = "reviewer-mcp/smoke_live_reviewers.py"
PER_TEST_TIMEOUT = 20
PER_FILE_TIMEOUT = 45
PER_SCRIPT_TIMEOUT = 120


def discover_tests() -> list[Path]:
    candidates = set(ROOT.rglob("test_*.py")) | set(ROOT.rglob("*_test.py"))
    candidates.add(ROOT / LIVE_REVIEWER_SCRIPT)
    return sorted(p for p in candidates if p.is_file()
                  and not EXCLUDED_PARTS.intersection(p.relative_to(ROOT).parts)
                  and p.relative_to(ROOT).as_posix() not in EXCLUDED_FILES)


def has_pytest_tests(source: str) -> bool:
    return bool(
        re.search(r"^\s*def test_[A-Za-z0-9_]+\s*\(", source, re.MULTILINE)
        or re.search(r"^\s*class\s+\w+\s*\([^)]*(?:unittest\.)?TestCase[^)]*\)\s*:", source, re.MULTILINE)
    )


def run(relative: str, command: list[str], timeout: int) -> tuple[str, str, float]:
    started = time.monotonic()
    try:
        result = subprocess.run(command, cwd=ROOT, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True, timeout=timeout, check=False)
    except subprocess.TimeoutExpired as exc:
        output = exc.stdout.decode(errors="replace") if isinstance(exc.stdout, bytes) else (exc.stdout or "")
        return "timeout", output, time.monotonic() - started
    return ("pass" if result.returncode == 0 else f"exit {result.returncode}"), result.stdout or "", time.monotonic() - started


def main() -> int:
    tests = discover_tests()
    if not tests:
        print("ERROR: no non-GA test files discovered", file=sys.stderr)
        return 2
    failures: list[tuple[str, str]] = []
    skipped: list[tuple[str, str]] = []
    started = time.monotonic()
    print(f"Discovered {len(tests)} non-GA test files.", flush=True)

    for index, path in enumerate(tests, start=1):
        relative = path.relative_to(ROOT).as_posix()
        source = path.read_text(encoding="utf-8")
        print(f"\n=== [{index}/{len(tests)}] {relative} ===", flush=True)
        if relative == LIVE_REVIEWER_SCRIPT:
            reviewer_keys = ("XAI_API_KEY", "PERPLEXITY_API_KEY", "JULES_API_KEY")
            if os.environ.get("ENSEMBLE_LIVE_REVIEWER_TESTS") != "1":
                reason = "live reviewer calls are opt-in"
            elif not os.environ.get("REVIEWER_SMOKE_REPOSITORY") or not os.environ.get("REVIEWER_SMOKE_PR_NUMBER"):
                reason = "missing REVIEWER_SMOKE_REPOSITORY or REVIEWER_SMOKE_PR_NUMBER"
            elif not any(os.environ.get(key) for key in reviewer_keys):
                reason = "missing reviewer API credentials"
            else:
                command, timeout = [sys.executable, relative], PER_SCRIPT_TIMEOUT
                reason = ""
            if reason:
                print(f"NOT RUN: {reason}", flush=True)
                skipped.append((relative, reason))
                continue
        elif relative in LIVE_SCRIPT_TESTS:
            if os.environ.get("ENSEMBLE_LIVE_CACHE_TESTS") != "1" or not os.environ.get("ANTHROPIC_API_KEY"):
                reason = "missing ANTHROPIC_API_KEY" if os.environ.get("ENSEMBLE_LIVE_CACHE_TESTS") == "1" else "live API test is opt-in"
                print(f"NOT RUN: {reason}", flush=True)
                skipped.append((relative, reason))
                continue
            command, timeout = [sys.executable, relative], PER_SCRIPT_TIMEOUT
        elif relative in SCRIPT_TESTS:
            command, timeout = [sys.executable, relative], PER_SCRIPT_TIMEOUT
        elif has_pytest_tests(source):
            command = [sys.executable, "-m", "pytest", "--import-mode=importlib", "-vv",
                       f"--timeout={PER_TEST_TIMEOUT}", relative]
            timeout = PER_FILE_TIMEOUT
        else:
            reason = "no pytest tests and no explicit script runner configured"
            print(f"ERROR: {reason}", flush=True)
            failures.append((relative, reason))
            continue

        status, output, elapsed = run(relative, command, timeout)
        if output:
            print(output.rstrip(), flush=True)
        all_skipped = (
            status == "pass"
            and re.search(r"\b\d+ skipped\b", output)
            and not re.search(r"\b\d+ passed\b", output)
        )
        if all_skipped:
            reason = "all tests skipped (live integration not enabled or credentials unavailable)"
            print(f"NOT RUN ({elapsed:.1f}s): {relative} ({reason})", flush=True)
            skipped.append((relative, reason))
        elif status == "pass":
            print(f"PASS ({elapsed:.1f}s): {relative}", flush=True)
        else:
            print(f"FAIL ({status}, {elapsed:.1f}s): {relative}", flush=True)
            failures.append((relative, status))

    print("\n=== Non-GA Python test inventory summary ===")
    print(f"Files considered: {len(tests)}")
    print(f"Passed: {len(tests) - len(failures) - len(skipped)}")
    print(f"Not run: {len(skipped)}")
    print(f"Failures/timeouts: {len(failures)}")
    print(f"Elapsed: {time.monotonic() - started:.1f}s")
    for path, reason in skipped:
        print(f"NOT RUN: {path} ({reason})")
    for path, reason in failures:
        print(f"FAIL: {path} ({reason})")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
