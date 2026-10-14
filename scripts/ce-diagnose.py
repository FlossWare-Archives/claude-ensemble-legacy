#!/usr/bin/env python3
"""Read-only, correlated diagnostics for the running Claude Ensemble stack.

This never writes production Memory/Graph data, sends alerts/messages, or invokes a
model. Deep mode can run a separate Learning-to-Thompson test that writes only to
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

DEFAULT_GATEWAY = "http://127.0.0.1:8080"
DEFAULT_LEARNING_SOCKET = "/tmp/claude-learning.sock"
DEFAULT_THOMPSON_SOCKET = "/tmp/claude-thompson.sock"
DEFAULT_ALERT_SOCKET = "/tmp/claude-alert.sock"
DEFAULT_MESSENGER_SOCKET = f"/run/user/{os.getuid()}/claude-messenger/claude-messenger.sock"


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
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            status_code = response.status
            raw = response.read(128 * 1024)
    except urllib.error.HTTPError as exc:
        status_code = exc.code
        try:
            raw = exc.read(128 * 1024)
        except OSError:
            raw = b""
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        elapsed = round((time.monotonic() - start) * 1000)
        reason = getattr(exc, "reason", exc)
        return "fail", f"request failed ({type(reason).__name__})", None, elapsed

    elapsed = round((time.monotonic() - start) * 1000)
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return "fail", f"HTTP {status_code}; response was not JSON", None, elapsed
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


def messenger_round_trip(path: str, correlation_id: str, timeout: float = 2.0) -> tuple[str, str, int]:
    """Exercise transient pub/sub with a unique topic so no unrelated subscriber matches."""
    start = time.monotonic()
    topic = f"ce-diagnostic.{correlation_id}"
    event_type = f"ce.diagnostic.{correlation_id}"
    subscription_id = f"diag-{correlation_id}"
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as subscriber:
            subscriber.settimeout(timeout)
            subscriber.connect(path)
            subscriber.sendall((json.dumps({
                "op": "subscribe",
                "subscription_id": subscription_id,
                "filter": {"topics": [topic], "event_types": [event_type]},
            }) + "\n").encode("utf-8"))
            subscriber_reader = subscriber.makefile("r", encoding="utf-8")
            ack = json.loads(subscriber_reader.readline())
            if not isinstance(ack, dict) or ack.get("ok") is not True:
                return "fail", "messenger rejected diagnostic subscription", round((time.monotonic() - start) * 1000)

            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as publisher:
                publisher.settimeout(timeout)
                publisher.connect(path)
                publisher.sendall((json.dumps({
                    "op": "publish", "topic": topic, "event_type": event_type,
                    "data": {"diagnostic": True, "correlation_id": correlation_id},
                }) + "\n").encode("utf-8"))
                publisher_reader = publisher.makefile("r", encoding="utf-8")
                publish_ack = json.loads(publisher_reader.readline())
                if not isinstance(publish_ack, dict) or publish_ack.get("ok") is not True or publish_ack.get("delivered") != 1:
                    return "fail", "messenger did not acknowledge delivery to the isolated diagnostic subscriber", round((time.monotonic() - start) * 1000)

            message = json.loads(subscriber_reader.readline())
            elapsed = round((time.monotonic() - start) * 1000)
            valid = (
                isinstance(message, dict)
                and message.get("topic") == topic
                and message.get("event_type") == event_type
                and isinstance(message.get("data"), dict)
                and message["data"].get("correlation_id") == correlation_id
            )
            if not valid:
                return "fail", "messenger delivered an invalid or mismatched diagnostic event", elapsed
            return "pass", "isolated publish/subscribe round trip acknowledged; event was transient", elapsed
    except (OSError, TimeoutError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        return "fail", f"messenger round trip failed ({type(exc).__name__})", round((time.monotonic() - start) * 1000)


def shutil_which_systemctl() -> bool:
    return shutil.which("systemctl") is not None


def unit_status(unit: str, timeout: float = 2.0) -> tuple[str, str, int]:
    start = time.monotonic()
    try:
        result = subprocess.run(
            ["systemctl", "--user", "is-active", unit],
            capture_output=True, text=True, timeout=timeout, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return "warn", f"unit state unavailable ({type(exc).__name__})", round((time.monotonic() - start) * 1000)
    elapsed = round((time.monotonic() - start) * 1000)
    state = result.stdout.strip() or result.stderr.strip() or "unknown"
    return ("pass", state, elapsed) if result.returncode == 0 else ("fail", state, elapsed)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--deep", action="store_true",
                        help="exercise read-only Memory/Graph/Decision operations and the isolated Learning-to-Thompson pipeline")
    parser.add_argument("--pipeline-test", action="store_true",
                        help="run the isolated Learning-to-Thompson integration test using temporary state")
    parser.add_argument("--service", choices=("gateway", "memory", "graph", "decision", "learning", "thompson", "messenger", "alert"),
                        help="run only the selected service/integration checks")
    parser.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    parser.add_argument("--timeout", type=float, default=2.0,
                        help="per-request timeout in seconds (default: 2; maximum: 10)")
    args = parser.parse_args()
    if not 0.1 <= args.timeout <= 10:
        parser.error("--timeout must be between 0.1 and 10 seconds")

    gateway = os.environ.get("ENSEMBLE_GATEWAY_URL", DEFAULT_GATEWAY).rstrip("/")
    learning_socket = os.environ.get("ENSEMBLE_LEARNING_SOCKET", DEFAULT_LEARNING_SOCKET)
    thompson_socket = os.environ.get("ENSEMBLE_THOMPSON_SOCKET", DEFAULT_THOMPSON_SOCKET)
    alert_socket = os.environ.get("ENSEMBLE_ALERT_SOCKET", DEFAULT_ALERT_SOCKET)
    messenger_socket = os.environ.get("CLAUDE_MESSENGER_SOCKET", os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}") + "/claude-messenger/claude-messenger.sock")
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
        status, detail, elapsed = socket_ping(learning_socket, "learning", correlation_id, args.timeout)
        record(results, "learning.ping", status, detail, elapsed, f"Unix socket {learning_socket}",
               "Check claude-learning.service and ENSEMBLE_LEARNING_SOCKET.")

    if selected("thompson"):
        status, detail, elapsed = socket_ping(thompson_socket, "thompson", correlation_id, args.timeout)
        record(results, "thompson.ping", status, detail, elapsed, f"Unix socket {thompson_socket}",
               "Check claude-thompson.service and ENSEMBLE_THOMPSON_SOCKET.")

    if selected("messenger"):
        status, detail, elapsed = messenger_round_trip(messenger_socket, correlation_id, args.timeout)
        record(results, "messenger.round_trip", status, detail, elapsed, f"Unix socket {messenger_socket}",
               "Check claude-messenger.service and CLAUDE_MESSENGER_SOCKET.")

    if selected("alert"):
        status, detail, elapsed = socket_ping(alert_socket, "alert", correlation_id, args.timeout)
        record(results, "alert.ping", status, detail, elapsed, f"Unix socket {alert_socket}",
               "Check claude-alert.service and ENSEMBLE_ALERT_SOCKET. The diagnostic does not trigger alerts.")
        if not shutil_which_systemctl():
            record(results, "alert.unit", "skip", "systemctl unavailable; alert service state was not checked",
                   0, "systemd user unit claude-alert.service")
        else:
            status, detail, elapsed = unit_status("claude-alert.service", args.timeout)
            record(results, "alert.unit", status, detail, elapsed, "systemd user unit claude-alert.service",
                   "Check claude-alert.service logs. Alert delivery is intentionally not triggered by diagnostics.")

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

    failures = [item for item in results if item["status"] == "fail"]
    summary = {
        "ok": not failures,
        "correlation_id": correlation_id,
        "checks": len(results),
        "passed": sum(item["status"] == "pass" for item in results),
        "failed": len(failures),
        "skipped": 0,
        "mode": "deep" if args.deep else ("pipeline-test" if args.pipeline_test else "standard"),
        "side_effects": ("isolated pipeline test writes only temporary Learning/Thompson state; no production writes, model calls, or notifications" if args.pipeline_test or (args.deep and args.service is None) else "read-only service probes; no production writes, model calls, or notifications"),
        "results": results,
    }
    if args.json:
        print(json.dumps(summary, indent=2, sort_keys=True))
    else:
        print(f"CE diagnostic correlation_id={correlation_id} mode={summary['mode']}")
        for item in results:
            mark = "✓" if item["status"] == "pass" else "✗"
            print(f"{mark} {item['step']}: {item['detail']} ({item['duration_ms']} ms)")
            if item["status"] != "pass" and item["remediation"]:
                print(f"    Fix: {item['remediation']}")
        print(f"Summary: {summary['passed']} passed, {summary['failed']} failed")
        print("Mode is read-only; this does not simulate Learning-to-Thompson outcome processing.")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
