"""Standard-library HTTP helper with a cancellable overall deadline.

The network operation runs in a short-lived child process. If the total
request deadline expires, the parent terminates and reaps that process, closing
its sockets instead of leaving a background thread doing network I/O.
"""
from __future__ import annotations

import json
import math
import subprocess
import sys
import time
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


class ProviderHTTPError(RuntimeError):
    """Raised when an external provider rejects or cannot receive a request."""

    def __init__(self, provider: str, status: int | None, detail: str):
        self.provider = provider
        self.status = status
        self.detail = detail
        super().__init__(
            f"{provider} API request failed"
            + (f" with HTTP {status}" if status else "")
            + f": {detail}"
        )


def _post_json_once(
    *, provider: str, url: str, payload: dict[str, Any],
    headers: dict[str, str], timeout: float,
) -> tuple[dict[str, Any], dict[str, str]]:
    """Perform the blocking request; the parent process enforces the deadline."""
    request = Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={**headers, "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8")
            return json.loads(body), dict(response.headers.items())
    except HTTPError as exc:
        try:
            body = exc.read().decode("utf-8")
        except Exception:
            body = str(exc)
        raise ProviderHTTPError(provider, exc.code, body[:2000]) from exc
    except (URLError, TimeoutError, OSError) as exc:
        raise ProviderHTTPError(provider, None, str(exc)) from exc
    except json.JSONDecodeError as exc:
        raise ProviderHTTPError(provider, None, "provider returned invalid JSON") from exc


_WORKER_SCRIPT = r"""
import json
import sys
from providers.http_client import ProviderHTTPError, _post_json_once

request = json.load(sys.stdin)
try:
    body, headers = _post_json_once(**request)
except ProviderHTTPError as exc:
    print(json.dumps({"ok": False, "status": exc.status, "detail": exc.detail}))
except Exception as exc:
    print(json.dumps({
        "ok": False, "status": None,
        "detail": f"provider worker failed: {type(exc).__name__}: {str(exc)[:1000]}"
    }))
else:
    print(json.dumps({"ok": True, "body": body, "headers": headers}))
"""


def post_json(
    *, provider: str, url: str, payload: dict[str, Any],
    headers: dict[str, str], timeout: float,
) -> tuple[dict[str, Any], dict[str, str]]:
    """POST JSON with a hard overall deadline and no orphaned network worker."""
    if (
        isinstance(timeout, bool)
        or not isinstance(timeout, (int, float))
        or not math.isfinite(timeout)
        or timeout <= 0
    ):
        raise ValueError("timeout must be a finite number greater than zero")

    deadline = time.monotonic() + float(timeout)
    request = {
        "provider": provider,
        "url": url,
        "payload": payload,
        "headers": headers,
        "timeout": float(timeout),
    }
    # Serialize before starting the worker. If the payload is not JSON-safe,
    # no child exists that could be left blocked on stdin.
    try:
        request_json = json.dumps(request)
    except (TypeError, ValueError) as exc:
        raise ProviderHTTPError(provider, None, f"request payload is not JSON serializable: {exc}") from exc

    try:
        process = subprocess.Popen(
            [sys.executable, "-c", _WORKER_SCRIPT],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            cwd=Path(__file__).resolve().parent.parent,
        )
    except OSError as exc:
        raise ProviderHTTPError(provider, None, f"could not start provider transport: {exc}") from exc

    remaining = deadline - time.monotonic()
    if remaining <= 0:
        process.kill()
        process.communicate()
        raise ProviderHTTPError(provider, None, "overall request deadline exceeded")
    try:
        stdout, stderr = process.communicate(input=request_json, timeout=remaining)
    except subprocess.TimeoutExpired as exc:
        process.kill()
        process.communicate()
        raise ProviderHTTPError(provider, None, "overall request deadline exceeded") from exc
    except BaseException:
        # Any exceptional exit after launch must close the child's stdin and
        # reap it, not just the normal timeout path.
        if process.poll() is None:
            process.kill()
        process.communicate()
        raise

    if time.monotonic() > deadline:
        raise ProviderHTTPError(provider, None, "overall request deadline exceeded")
    if process.returncode != 0:
        detail = stderr.strip()[:2000] or f"transport worker exited {process.returncode}"
        raise ProviderHTTPError(provider, None, detail)
    try:
        result = json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise ProviderHTTPError(provider, None, "provider transport returned invalid worker output") from exc
    if not isinstance(result, dict) or not isinstance(result.get("ok"), bool):
        raise ProviderHTTPError(provider, None, "provider transport returned malformed worker output")
    if not result["ok"]:
        raise ProviderHTTPError(provider, result.get("status"), str(result.get("detail", "request failed")))
    body = result.get("body")
    response_headers = result.get("headers")
    if not isinstance(body, dict) or not isinstance(response_headers, dict):
        raise ProviderHTTPError(provider, None, "provider returned an unexpected response shape")
    return body, response_headers
