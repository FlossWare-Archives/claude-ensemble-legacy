"""Provider-neutral reviewer port and MCP reviewer adapter."""

from __future__ import annotations

import json
import math
import os
import urllib.request
from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(frozen=True)
class ReviewerResult:
    reviewer: str
    status: str
    verdict: str
    summary: str
    findings: list[dict[str, Any]]
    provider: str = ""
    model: str = ""
    error: str = ""


class Reviewer(Protocol):
    name: str

    def review(
        self,
        *,
        candidate_id: str,
        candidate: str,
        context: str,
        focus: str = "",
    ) -> ReviewerResult:
        """Review one candidate without modifying the workspace."""


class MCPReviewer:
    """Call the reviewer-mcp candidate-review tool over its HTTP transport."""

    def __init__(
        self,
        name: str,
        *,
        endpoint: str | None = None,
        auth_token: str | None = None,
        repository: str | None = None,
        branch: str | None = None,
        timeout: float | None = None,
    ) -> None:
        if name not in {"grok", "perplexity", "jules"}:
            raise ValueError("unsupported MCP reviewer: " + name)
        self.name = name
        self.endpoint = endpoint or os.environ.get(
            "REVIEWER_MCP_URL", "http://127.0.0.1:8790/mcp"
        )
        self.auth_token = (
            auth_token
            if auth_token is not None
            else os.environ.get("MCP_AUTH_TOKEN", "")
        )
        self.repository = repository or os.environ.get("REVIEWER_MCP_REPOSITORY", "")
        self.branch = branch or os.environ.get("REVIEWER_MCP_BRANCH", "main")
        try:
            jules_timeout = float(os.environ.get("JULES_TIMEOUT_SECONDS", "900"))
            if not math.isfinite(jules_timeout) or jules_timeout <= 0:
                raise ValueError("JULES_TIMEOUT_SECONDS must be finite and positive")
            default_timeout = max(960.0, jules_timeout + 60.0)
            if isinstance(timeout, bool):
                raise ValueError("reviewer MCP timeout must not be boolean")
            configured_timeout = float(timeout) if timeout is not None else float(
                os.environ.get("REVIEWER_MCP_TIMEOUT_SECONDS", str(default_timeout))
            )
        except (TypeError, ValueError) as exc:
            raise ValueError("reviewer MCP timeout must be a positive number") from exc
        if (
            not math.isfinite(configured_timeout)
            or configured_timeout <= 0
        ):
            raise ValueError("reviewer MCP timeout must be a finite positive number")
        if configured_timeout < jules_timeout + 30.0:
            raise ValueError(
                "REVIEWER_MCP_TIMEOUT_SECONDS must exceed JULES_TIMEOUT_SECONDS by at least 30 seconds"
            )
        self.timeout = float(configured_timeout)

    def review(
        self,
        *,
        candidate_id: str,
        candidate: str,
        context: str,
        focus: str = "",
    ) -> ReviewerResult:
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": "review_candidate",
                "arguments": {
                    "candidate_id": candidate_id,
                    "candidate": candidate,
                    "context": context,
                    "focus": focus,
                    "reviewer": self.name,
                    "repository": self.repository,
                    "head_ref": self.branch,
                },
            },
        }
        request = urllib.request.Request(
            self.endpoint,
            data=json.dumps(payload).encode(),
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        if self.auth_token:
            request.add_header("Authorization", "Bearer " + self.auth_token)
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            body = json.loads(response.read().decode())

        if "error" in body:
            raise RuntimeError(body["error"].get("message", "reviewer MCP error"))
        content = body.get("result", {}).get("content", [])
        text = next(
            item.get("text", "") for item in content if item.get("type") == "text"
        )
        result = json.loads(text)
        if isinstance(result, dict) and isinstance(result.get("reviews"), list):
            match = next(
                (
                    item
                    for item in result["reviews"]
                    if isinstance(item, dict) and item.get("reviewer") == self.name
                ),
                None,
            )
            if match is None:
                raise RuntimeError(
                    "reviewer MCP returned no result for " + self.name
                )
            result = match
        if not isinstance(result, dict):
            raise RuntimeError("reviewer MCP returned an invalid result")
        return ReviewerResult(
            reviewer=str(result.get("reviewer", self.name)),
            status=str(result.get("status", "failed")),
            verdict=str(result.get("verdict", "comment")),
            summary=str(result.get("summary", "")),
            findings=result.get("findings", [])
            if isinstance(result.get("findings", []), list)
            else [],
            provider=str(result.get("provider", "")),
            model=str(result.get("model", "")),
            error=str(result.get("error", "")),
        )
