#!/usr/bin/env python3
"""Read-only, correlated diagnostics for the running Claude Ensemble stack.

This never writes production Memory/Graph data or invokes a model. Deep mode can run a separate Learning-to-Thompson test that writes only to
temporary test state and sockets.
"""
from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import shutil
import sys
import time
import urllib.error
import urllib.request
import uuid
from typing import Any
from pathlib import Path

DEFAULT_GATEWAY = "http://127.0.0.1:8080"
MAX_DIAGNOSTIC_RESPONSE_BYTES = 128 * 1024


def default_hook_dir() -> str:
    """Select CE's explicit profile; never silently probe the ordinary ~/.claude profile."""
    configured = os.environ.get("CLAUDE_HOOK_DIR")
    if configured:
        return str(Path(configured).expanduser())
    profile = os.environ.get("ENSEMBLE_CLAUDE_CODE_CONFIG_DIR") or os.environ.get("CLAUDE_CONFIG_DIR")
    if profile:
        return str(Path(profile).expanduser() / "hooks")
    return str(Path.home() / ".FlossWare" / "claude-ensemble" / "shared" / "claude-code" / "hooks")


def record(results: list[dict[str, Any]], name: str, status: str, detail: str,
           duration_ms: int, endpoint: str = "", remediation: str = "") -> None:
    results.append({
        "step": name,
        "status": status,
        "endpoint": endpoint,
        "duration_ms": duration_ms,
        "detail": detail,
        "remediation": remediation,
    })


def http_request(base: str, path: str, correlation_id: str, method: str = "GET",
                 body: dict[str, Any] | None = None, timeout: float = 2.0,
                 accept_no_data: bool = False) -> tuple[str, str, dict[str, Any] | None, int]:
    headers = {
        "Accept": "application/json",
        "X-Correlation-ID": correlation_id,
        "X-Request-ID": correlation_id,
    }
    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(base.rstrip("/") + path, data=data,
                                     headers=headers, method=method)
    start = time.monotonic()
    headers = {}
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            status_code = response.status
            headers = response.headers
            raw = response.read(MAX_DIAGNOSTIC_RESPONSE_BYTES + 1)
    except urllib.error.HTTPError as exc:
        status_code = exc.code
        headers = exc.headers
        try:
            raw = exc.read(MAX_DIAGNOSTIC_RESPONSE_BYTES + 1)
        except OSError:
            raw = b""
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        elapsed = round((time.monotonic() - start) * 1000)
        reason = getattr(exc, "reason", exc)
        return "fail", f"request failed ({type(reason).__name__})", None, elapsed

    elapsed = round((time.monotonic() - start) * 1000)
    if len(raw) > MAX_DIAGNOSTIC_RESPONSE_BYTES:
        content_type = headers.get("Content-Type", "unknown") if headers else "unknown"
        server = headers.get("Server", "") if headers else ""
        content_length = headers.get("Content-Length", "") if headers else ""
        hint = f" (Content-Type={content_type}"
        if content_length:
            hint += f"; Content-Length={content_length}"
        if server:
            hint += f"; Server={server}"
        hint += ")"
        return (
            "fail",
            f"HTTP {status_code}; response exceeded diagnostic body limit "
            f"({MAX_DIAGNOSTIC_RESPONSE_BYTES} bytes; body is larger than the limit){hint}",
            None,
            elapsed,
        )
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        # Report response metadata, not the body: HTML/proxy pages can contain
        # credentials or other host-specific information. Headers help identify
        # a wrong listener or intermediary without leaking response contents.
        content_type = headers.get("Content-Type", "unknown") if headers else "unknown"
        server = headers.get("Server", "") if headers else ""
        hint = f" (Content-Type={content_type}"
        if server:
            hint += f"; Server={server}"
        hint += ")"
        return "fail", f"HTTP {status_code}; response was not JSON{hint}", None, elapsed
    if not isinstance(payload, dict):
        return "fail", f"HTTP {status_code}; response was not a JSON object", None, elapsed
    if accept_no_data and status_code == 422 and payload.get("error_code") == "no_data":
        return "pass", "decision endpoint responded; no historical data is available", payload, elapsed
    if status_code < 200 or status_code >= 300:
        code = payload.get("error_code")
        safe_reason = code if isinstance(code, str) and code else "request failed"
        return "fail", f"HTTP {status_code}; {safe_reason}", payload, elapsed
    if payload.get("ok") is not True:
        return "fail", f"HTTP {status_code}; response did not acknowledge ok=true", payload, elapsed
    return "pass", f"HTTP {status_code}; ok=true", payload, elapsed


def socket_ping(path: str, service: str, correlation_id: str, timeout: float = 2.0) -> tuple[str, str, int]:
    start = time.monotonic()
    action_key = "action" if service == "thompson" else "op"
    request = {action_key: "ping", "correlation_id": correlation_id}
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.settimeout(timeout)
            client.connect(path)
            client.sendall((json.dumps(request) + "\n").encode("utf-8"))
            chunks = bytearray()
            while b"\n" not in chunks and len(chunks) < 65536:
                chunk = client.recv(4096)
                if not chunk:
                    break
                chunks.extend(chunk)
        response = json.loads(bytes(chunks).split(b"\n", 1)[0].decode("utf-8"))
        elapsed = round((time.monotonic() - start) * 1000)
        if isinstance(response, dict) and response.get("ok") is True:
            return "pass", "Unix-socket ping acknowledged", elapsed
        return "fail", "service returned a negative or malformed ping acknowledgment", elapsed
    except (OSError, TimeoutError, ValueError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        return "fail", f"socket request failed ({type(exc).__name__})", round((time.monotonic() - start) * 1000)


def read_json_line(client: socket.socket) -> dict[str, Any]:
    data = bytearray()
    while b"\n" not in data and len(data) < 65536:
        chunk = client.recv(4096)
        if not chunk:
            break
        data.extend(chunk)
    if not data:
        raise ConnectionError("socket closed without a response")
    value = json.loads(bytes(data).split(b"\n", 1)[0].decode("utf-8"))
    if not isinstance(value, dict):
        raise ValueError("socket response must be a JSON object")
    return value


def installed_hook_smoke(hook_dir: Path, correlation_id: str, timeout: float,
                        include_session_end: bool = False,
                        require_context: bool = False,
                        require_knowledge: bool = False) -> list[dict[str, Any]]:
    """Run the installed prompt hook; optionally run metadata-only SessionEnd capture."""
    results: list[dict[str, Any]] = []
    node = shutil.which("node")
    if not node:
        record(results, "hooks.node", "fail", "Node.js is unavailable", 0,
               remediation="Install Node.js to execute the installed Claude Code hooks.")
        return results

    prompt_hook = hook_dir / "memory-search-on-prompt.js"
    session_hook = hook_dir / "session-end-memory-capture.js"
    required = [prompt_hook, session_hook] if include_session_end else [prompt_hook]
    if any(not path.is_file() for path in required):
        record(results, "hooks.installed", "fail",
               "required installed hook script is missing", 0, str(hook_dir),
               "Install/sync the canonical hook scripts or set CLAUDE_HOOK_DIR.")
        return results

    env = os.environ.copy()
    env.setdefault("FLOSSWARE_MEMORY_URL", "http://127.0.0.1:8767")
    env.setdefault("FLOSSWARE_GRAPH_URL", "http://127.0.0.1:8766")
    env["FLOSSWARE_MEMORY_TIMEOUT_MS"] = str(int(timeout * 1000))
    # The hook has different supported limits: Memory allows 10s, Graph 5s.
    # Keep the diagnostic request timeout independent from Graph's config cap.
    env["FLOSSWARE_GRAPH_TIMEOUT_MS"] = str(min(int(timeout * 1000), 5000))
    if require_context:
        env["FLOSSWARE_CE_HOOK_DIAGNOSTICS"] = "1"

    prompt_event = {
        "hook_event_name": "UserPromptSubmit",
        "session_id": f"ce-diagnostic-prompt-{correlation_id}",
        "cwd": str(Path.cwd()),
        "prompt": "What are the current CE project decisions and architecture?",
    }
    started = time.monotonic()
    try:
        result = subprocess.run(
            [node, str(prompt_hook)], input=json.dumps(prompt_event),
            capture_output=True, text=True, timeout=max(5.0, timeout * 3),
            env=env, check=False,
        )
        elapsed = round((time.monotonic() - started) * 1000)
        failed = result.returncode != 0 or "Memory search unavailable:" in result.stderr or "Graph context unavailable" in result.stderr
        context_emitted = False
        retrieval: dict[str, Any] = {}
        if not failed and result.stdout.strip():
            try:
                payload = json.loads(result.stdout)
                output = payload.get("hookSpecificOutput", {})
                failed = (
                    not isinstance(output, dict)
                    or output.get("hookEventName") != "UserPromptSubmit"
                    or not isinstance(output.get("additionalContext"), str)
                    or not output.get("additionalContext", "").strip()
                )
                if not failed:
                    context_emitted = True
                    raw_retrieval = output.get("contextRetrieval", {})
                    retrieval = raw_retrieval if isinstance(raw_retrieval, dict) else {}
            except (json.JSONDecodeError, AttributeError):
                failed = True
        diagnostic_events = []
        for line in result.stderr.splitlines():
            prefix = "CE_HOOK_DIAGNOSTICS "
            if line.startswith(prefix):
                try:
                    diagnostic_events.append(json.loads(line[len(prefix):]))
                except json.JSONDecodeError:
                    pass
        completed = next((event for event in reversed(diagnostic_events)
                          if event.get("event") == "completed"), {})
        no_context = not failed and not context_emitted
        knowledge_count = retrieval.get("knowledgeCount", 0)
        missing_knowledge = (
            not failed and context_emitted and require_knowledge
            and (isinstance(knowledge_count, bool) or not isinstance(knowledge_count, int) or knowledge_count < 1)
        )
        if (require_context and no_context) or missing_knowledge:
            failed = True
        detail = (
            "installed prompt hook failed its live service contract"
            if failed and not missing_knowledge else
            "context was emitted, but no Knowledge documents were retrieved; operational Memory alone does not prove durable project knowledge is available"
            if missing_knowledge else
            f"installed prompt hook emitted context (Memory={retrieval.get('memoryCount', '?')}, "
            f"Knowledge={retrieval.get('knowledgeCount', '?')}, Graph={retrieval.get('graphCount', '?')})"
            if context_emitted else
            f"hook ran but emitted no context (Memory status={completed.get('memoryStatus', 'unknown')}, "
            f"results={completed.get('memoryResultCount', 'unknown')}, Graph status={completed.get('graphStatus', 'unknown')}); "
            "service health alone does not prove relevant context exists"
        )
        status = "fail" if failed else ("warn" if no_context else "pass")
        remediation = (
            "Index the intended project Markdown as Knowledge with python3 tools/claude-config/lib/memory_sync.py knowledge-sync --root /path/to/project, then rerun the prompt-context smoke."
            if missing_knowledge else
            "Check the selected hook path, Memory search results, and live Memory/Graph service configuration."
            if failed or no_context else ""
        )
        record(results, "hooks.user_prompt_submit", status, detail,
               elapsed, str(prompt_hook), remediation)
    except (OSError, subprocess.TimeoutExpired) as exc:
        elapsed = round((time.monotonic() - started) * 1000)
        record(results, "hooks.user_prompt_submit", "fail",
               f"installed prompt hook could not complete ({type(exc).__name__})",
               elapsed, str(prompt_hook), "Check Node.js and Memory/Graph service health.")

    if include_session_end:
        session_id = f"ce-diagnostic-session-{correlation_id}"
        event = {"hook_event_name": "SessionEnd", "session_id": session_id,
                 "reason": "diagnostic_smoke_test"}
        started = time.monotonic()
        try:
            result = subprocess.run(
                [node, str(session_hook)], input=json.dumps(event),
                capture_output=True, text=True, timeout=max(5.0, timeout * 3),
                env=env, check=False,
            )
            elapsed = round((time.monotonic() - started) * 1000)
            failed = (
                result.returncode != 0
                or "Memory capture unavailable:" in result.stderr
            )
            record(results, "hooks.session_end", "fail" if failed else "pass",
                   "SessionEnd reported a Memory capture failure" if failed
                   else "metadata-only SessionEnd capture completed",
                   elapsed, str(session_hook),
                   "Check Memory append-once and Memory service health." if failed else "")
        except (OSError, subprocess.TimeoutExpired) as exc:
            elapsed = round((time.monotonic() - started) * 1000)
            record(results, "hooks.session_end", "fail",
                   f"installed SessionEnd hook could not complete ({type(exc).__name__})",
                   elapsed, str(session_hook), "Check Node.js and Memory service health.")
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--deep", action="store_true",
                        help="exercise read-only Memory/Graph/Decision operations and the isolated Learning-to-Thompson pipeline")
    parser.add_argument("--hook-smoke", action="store_true",
                        help="run the installed UserPromptSubmit hook against live Memory/Graph services")
    parser.add_argument("--prompt-context-smoke", action="store_true",
                        help="require the selected installed prompt hook to emit non-empty context and at least one Knowledge result; does not invoke Claude or prove model receipt")
    parser.add_argument("--session-end-smoke", action="store_true",
                        help="also run SessionEnd; writes one metadata-only event to production Memory")
    parser.add_argument("--allow-memory-write", action="store_true",
                        help="explicitly authorize the metadata-only SessionEnd smoke event")
    parser.add_argument("--hook-dir", default=default_hook_dir(),
                        help="installed Claude hook directory (default: dedicated CE profile, never implicitly ~/.claude/hooks)")
    parser.add_argument("--pipeline-test", action="store_true",
                        help="run the isolated Learning-to-Thompson integration test using temporary state")
    parser.add_argument("--service", choices=("gateway", "memory", "graph", "decision", "learning", "thompson"),
                        help="run only the selected service/integration checks")
    parser.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    parser.add_argument("--timeout", type=float, default=2.0,
                        help="per-request timeout in seconds (default: 2; maximum: 10)")
    args = parser.parse_args()
    if args.session_end_smoke and not args.allow_memory_write:
        parser.error("--session-end-smoke requires --allow-memory-write because it appends a synthetic metadata event to Memory")
    if args.session_end_smoke or args.prompt_context_smoke:
        args.hook_smoke = True
    if not 0.1 <= args.timeout <= 10:
        parser.error("--timeout must be between 0.1 and 10 seconds")

    gateway = os.environ.get("ENSEMBLE_GATEWAY_URL", DEFAULT_GATEWAY).rstrip("/")
    correlation_id = str(uuid.uuid4())
    results: list[dict[str, Any]] = []

    def selected(name: str) -> bool:
        return args.service is None or args.service == name

    def http_step(name: str, path: str, method: str = "GET",
                  body: dict[str, Any] | None = None, remediation: str = "",
                  accept_no_data: bool = False) -> None:
        status, detail, _payload, elapsed = http_request(
            gateway, path, correlation_id, method, body, args.timeout, accept_no_data
        )
        record(results, name, status, detail, elapsed, f"{method} {gateway}{path}", remediation)

    if selected("gateway"):
        http_step("gateway.liveness", "/api/v1/health",
                  remediation="Check claude-ensemble.service and ENSEMBLE_GATEWAY_URL.")
        http_step("gateway.readiness", "/api/v1/ready",
                  remediation="Inspect the readiness response for Graph and Memory failures.")

    if selected("memory"):
        http_step("memory.health.via_gateway", "/api/v1/memory/health",
                  remediation="Check Memory service health and the gateway's memory URL.")
        if args.deep:
            http_step("memory.read.via_gateway", "/api/v1/memory/list", "POST", {},
                      "Check the Memory REST contract and configured memory service.")

    if selected("graph"):
        http_step("graph.health.via_gateway", "/api/v1/graph/health",
                  remediation="Check Graph service health and the gateway's graph URL.")
        if args.deep:
            http_step("graph.read.via_gateway", "/api/v1/graph/query", "POST",
                      {"type": "__ce_diagnostic_probe__", "properties": {}},
                      "Check the Graph query API and service logs.")

    if selected("decision"):
        http_step("decision.read", "/api/v1/decision/query/problems",
                  remediation="Check the gateway decision handler and learning analytics.",
                  accept_no_data=True)

    if selected("learning"):
        http_step("learning.health", "/api/v1/learning/health",
                  remediation="Check claude-learning.service, ENSEMBLE_LEARNING_URL, and the service HTTP port.")
        http_step("learning.ping", "/api/v1/learning/ping",
                  remediation="Check the Learning REST adapter and its configured gateway URL.")

    if selected("thompson"):
        http_step("thompson.health", "/api/v1/thompson/health",
                  remediation="Check claude-thompson.service, ENSEMBLE_THOMPSON_URL, and the service HTTP port.")
        http_step("thompson.ping", "/api/v1/thompson/ping",
                  remediation="Check the Thompson REST adapter and its configured gateway URL.")

    if args.pipeline_test or (args.deep and args.service is None):
        started = time.monotonic()
        pipeline_test = os.path.join(os.path.dirname(os.path.abspath(__file__)), "test_ce_pipeline.py")
        try:
            pipeline = subprocess.run(
                [sys.executable, pipeline_test],
                capture_output=True,
                text=True,
                timeout=20,
                check=False,
            )
            elapsed = round((time.monotonic() - started) * 1000)
            pipeline_ok = pipeline.returncode == 0
            detail = (
                "isolated Learning request → Thompson outcome path passed"
                if pipeline_ok else f"isolated pipeline test failed (exit {pipeline.returncode})"
            )
            remediation = "" if pipeline_ok else "Run python3 scripts/test_ce_pipeline.py and inspect the isolated test failure."
            record(results, "pipeline.learning_to_thompson", "pass" if pipeline_ok else "fail",
                   detail, elapsed, "temporary state and socket; no production state touched", remediation)
        except (OSError, subprocess.TimeoutExpired) as exc:
            elapsed = round((time.monotonic() - started) * 1000)
            record(results, "pipeline.learning_to_thompson", "fail",
                   f"isolated pipeline test could not complete ({type(exc).__name__})",
                   elapsed, "temporary state and socket; no production state touched",
                   "Run python3 scripts/test_ce_pipeline.py and inspect the test environment.")

    if args.hook_smoke:
        results.extend(installed_hook_smoke(
            Path(args.hook_dir).expanduser(), correlation_id, args.timeout,
            include_session_end=args.session_end_smoke,
            require_context=args.prompt_context_smoke,
            require_knowledge=args.prompt_context_smoke,
        ))

    # Carry correlation metadata on every step, not only the report envelope.
    # `detail` is the bounded evidence summary; do not include raw response bodies.
    for item in results:
        item["correlation_id"] = correlation_id
        item["evidence"] = item["detail"]

    failures = [item for item in results if item["status"] == "fail"]
    warnings = [item for item in results if item["status"] == "warn"]
    summary = {
        "ok": not failures,
        "correlation_id": correlation_id,
        "checks": len(results),
        "passed": sum(item["status"] == "pass" for item in results),
        "failed": len(failures),
        "warnings": len(warnings),
        "skipped": sum(item["status"] == "skip" for item in results),
        "mode": "prompt-context-smoke" if args.prompt_context_smoke else ("hook-smoke" if args.hook_smoke else ("deep" if args.deep else ("pipeline-test" if args.pipeline_test else "standard"))),
        "side_effects": ("SessionEnd smoke writes one synthetic metadata-only event to production Memory" if args.session_end_smoke else ("installed UserPromptSubmit hook is read-only; no production writes" if args.hook_smoke else ("isolated pipeline test writes only temporary Learning/Thompson state; no production writes, model calls, or notifications" if args.pipeline_test or (args.deep and args.service is None) else "read-only service probes; no production writes, model calls, or notifications"))),
        "results": results,
    }
    if args.json:
        print(json.dumps(summary, indent=2, sort_keys=True))
    else:
        print(f"CE diagnostic correlation_id={correlation_id} mode={summary['mode']}")
        for item in results:
            mark = "✓" if item["status"] == "pass" else ("!" if item["status"] == "warn" else "✗")
            print(f"{mark} {item['step']}: {item['detail']} ({item['duration_ms']} ms)")
            if item["status"] != "pass" and item["remediation"]:
                print(f"    Fix: {item['remediation']}")
        print(f"Summary: {summary['passed']} passed, {summary['warnings']} warnings, {summary['failed']} failed")
        if args.session_end_smoke:
            print("SessionEnd smoke writes one metadata-only Memory event.")
        elif args.prompt_context_smoke:
            print("Prompt-context smoke is read-only and invokes the hook directly; it does not prove Claude Code delivered this context to a model.")
        elif args.hook_smoke:
            print("Hook smoke is read-only; it does not invoke a model or submit a learning outcome.")
        else:
            print("This does not invoke a Claude model or create a production learning outcome.")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())