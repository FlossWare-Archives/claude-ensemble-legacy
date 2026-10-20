"""Security and policy tests for the REST collaboration boundary."""

from __future__ import annotations

import json
import subprocess
import threading
import urllib.error
import urllib.request

from server.ensemble_server import create_server
from providers import ClaudeCodeProvider


def request(server, payload, token=None):
    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        f"http://127.0.0.1:{server.server_port}/api/v1/collaboration/run",
        data=data,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    if token is not None:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=3) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read())


def run_request(monkeypatch, tmp_path, payload, token=None):
    monkeypatch.setenv("ENSEMBLE_METRICS_FILE", str(tmp_path / "metrics.jsonl"))
    monkeypatch.setenv("ENSEMBLE_EXPERIMENT_FILE", str(tmp_path / "experiments.jsonl"))
    server = create_server("127.0.0.1", 0, graph_url="", memory_url="")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        return request(server, payload, token)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(2)


def test_collaboration_is_disabled_without_server_token(monkeypatch, tmp_path):
    monkeypatch.delenv("ENSEMBLE_COLLABORATION_AUTH_TOKEN", raising=False)
    status, body = run_request(monkeypatch, tmp_path, {"task": "test task"})
    assert status == 503
    assert "disabled" in body["error"]


def test_collaboration_rejects_missing_bearer_token(monkeypatch, tmp_path):
    monkeypatch.setenv("ENSEMBLE_COLLABORATION_AUTH_TOKEN", "test-secret")
    status, body = run_request(monkeypatch, tmp_path, {"task": "test task"})
    assert status == 401
    assert body["error_code"] == "unauthorized"


def test_collaboration_blocks_external_reviewers_without_opt_in(monkeypatch, tmp_path):
    monkeypatch.setenv("ENSEMBLE_COLLABORATION_AUTH_TOKEN", "test-secret")
    monkeypatch.setenv("ENSEMBLE_COLLABORATION_REVIEWERS", "grok")
    monkeypatch.delenv("ENSEMBLE_COLLABORATION_ALLOW_EXTERNAL_DATA", raising=False)
    status, body = run_request(monkeypatch, tmp_path, {"task": "test task"}, token="test-secret")
    assert status == 403
    assert body["error_code"] == "forbidden"
    assert "external reviewers are disabled" in body["error"]


def test_collaboration_rejects_empty_task_before_provider_dispatch(monkeypatch, tmp_path):
    monkeypatch.setenv("ENSEMBLE_COLLABORATION_AUTH_TOKEN", "test-secret")
    monkeypatch.setenv("ENSEMBLE_COLLABORATION_ALLOW_EXTERNAL_DATA", "true")
    status, body = run_request(
        monkeypatch,
        tmp_path,
        {"task": " ", "solvers": ["claude-sonnet"], "reviewers": []},
        token="test-secret",
    )
    assert status == 400
    assert body["error_code"] == "invalid_request"
    assert body["error"] == "task is required"



def test_collaboration_rest_route_uses_strict_claude_code_provider(
    monkeypatch, tmp_path
):
    """Exercise the real REST route and orchestrator; mock only CLI I/O."""
    import providers.claude_code as claude_code_module

    monkeypatch.setenv("ENSEMBLE_COLLABORATION_AUTH_TOKEN", "test-secret")
    monkeypatch.setenv("ENSEMBLE_COLLABORATION_SOLVERS", "sonnet")
    monkeypatch.setenv("ENSEMBLE_COLLABORATION_ARBITER", "opus")
    monkeypatch.setenv("ENSEMBLE_COLLABORATION_REVIEWERS", "")
    monkeypatch.delenv("ENSEMBLE_COLLABORATION_ALLOW_EXTERNAL_DATA", raising=False)
    monkeypatch.setenv("ENSEMBLE_METRICS_FILE", str(tmp_path / "metrics.jsonl"))
    monkeypatch.setenv("ENSEMBLE_EXPERIMENT_FILE", str(tmp_path / "experiments.jsonl"))
    monkeypatch.setenv("ENSEMBLE_DECISION_PROVENANCE_FILE", str(tmp_path / "decisions.jsonl"))

    adjudication = json.dumps({
        "selected_candidate": "r1-sonnet",
        "decision": "use candidate",
        "rationale": "supported by the candidate evidence",
        "supporting_evidence": ["candidate returned a non-empty proposal"],
        "rejected_alternatives": [],
        "blocking_concerns": [],
        "follow_ups": [],
        "complete": True,
        "human_decision_required": False,
    })
    cli_results = iter(["REST route solution", adjudication])
    commands = []

    class FakeProcess:
        returncode = 0

        def __init__(self, result):
            self.result = result

        def communicate(self, input=None, timeout=None):
            assert input
            assert timeout == 300.0
            return (
                json.dumps({
                    "type": "result",
                    "subtype": "success",
                    "result": self.result,
                    "model": "claude-sonnet-4-5",
                    "modelUsage": {"claude-sonnet-4-5": {"provider": "vertex"}},
                    "apiProvider": "vertex",
                }),
                "",
            )

    provider = ClaudeCodeProvider(expected_provider="vertex")
    monkeypatch.setattr(claude_code_module.shutil, "which", lambda _: "/mock/claude")
    monkeypatch.setattr(
        claude_code_module.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args=args[0],
            returncode=0,
            stdout=json.dumps({"apiProvider": "vertex"}),
            stderr="",
        ),
    )

    def fake_start_process(command):
        commands.append(command)
        return FakeProcess(next(cli_results))

    monkeypatch.setattr(provider, "_start_process", fake_start_process)

    server = create_server("127.0.0.1", 0, graph_url="", memory_url="")
    # Keep the real registry and route, replacing only model resolution so both
    # solver and arbiter use the real strict provider with mocked CLI I/O.
    registry = server.application.models.registry
    monkeypatch.setattr(registry, "resolve", lambda _name: provider)
    monkeypatch.setattr(registry, "resolve_model", lambda name: ("claude-code", name))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        status, body = request(
            server,
            {"task": "Design a safe feature.", "solvers": ["sonnet"], "arbiter": "opus", "reviewers": []},
            token="test-secret",
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(2)

    assert status == 200
    assert body["ok"] is True
    assert body["status"] == "accepted"
    assert body["selected_candidate"]["proposal"] == "REST route solution"
    assert len(commands) == 2
    assert commands[0][commands[0].index("--model") + 1] == "sonnet"
    assert commands[1][commands[1].index("--model") + 1] == "opus"
