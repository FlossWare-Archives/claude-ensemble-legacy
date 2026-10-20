"""Mocked end-to-end regression tests for Jira-backed CE reviews.

These tests exercise the real CLI context-loading and worker/arbiter orchestration
without invoking Claude Code, network services, Jira, or any external provider.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from arbitration.orchestrator import ArbitrationOrchestrator, TaskType
from providers import ModelCapabilities
from providers.model_provider import ModelRequest, ModelResponse
import tools.arbitrate as arbitrate


class MockReviewProvider:
    """Deterministic stand-in for Claude Code, recording every model request."""

    def __init__(self) -> None:
        self.requests: list[ModelRequest] = []

    @property
    def capabilities(self) -> ModelCapabilities:
        return ModelCapabilities(
            provider="test",
            supports_system_prompt=True,
            supports_messages=True,
            supports_temperature_override=True,
            supports_max_tokens_override=True,
            supports_timeout_override=True,
        )

    def generate(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
        if request.model == "opus":
            text = json.dumps(
                {
                    "adjudicated_result": (
                        "Review completed. Verify acceptance criteria against source."
                    ),
                    "selected_worker": "sonnet",
                    "rationale": "The worker reports are grounded in supplied evidence.",
                    "supporting_evidence": ["Jira requirement reached the arbiter"],
                    "rejected_alternatives": ["Unsupported findings rejected"],
                    "next_phase_questions": [],
                }
            )
        else:
            text = (
                f"Mock findings from {request.model}. "
                "JIRA_CONTEXT_MARKER and SOURCE_CONTEXT_MARKER were supplied."
            )
        return ModelResponse(
            provider="mock",
            model=request.model or "unknown",
            text=text,
        )


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def _make_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "target-repo"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.name", "CE Test")
    _git(repo, "config", "user.email", "ce-test@example.invalid")
    source = repo / "service.py"
    source.write_text("def calculate(value):\n    return value\n", encoding="utf-8")
    _git(repo, "add", "service.py")
    _git(repo, "commit", "-m", "baseline")
    source.write_text(
        "def calculate(value):\n"
        "    # SOURCE_CONTEXT_MARKER\n"
        "    if value is None:\n"
        "        return 0\n"
        "    return value\n",
        encoding="utf-8",
    )
    return repo


def _mock_orchestrator(monkeypatch: pytest.MonkeyPatch, provider: MockReviewProvider):
    instances: list[ArbitrationOrchestrator] = []

    def factory(task_type, task_description):
        instance = ArbitrationOrchestrator(
            task_type,
            task_description,
            default_provider=provider,
        )
        instances.append(instance)
        return instance

    monkeypatch.setattr(arbitrate, "ArbitrationOrchestrator", factory)
    return instances


def test_jira_context_flows_through_real_review_cli_to_workers_and_arbiter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    repo = _make_repo(tmp_path)
    jira_context = tmp_path / "jira-context"
    jira_context.mkdir(mode=0o700)
    (jira_context / "jira-requirements.md").write_text(
        "# PROJ-482: Handle missing values\n\n"
        "Acceptance criteria: missing input must return zero. "
        "JIRA_CONTEXT_MARKER\n",
        encoding="utf-8",
    )

    provider = MockReviewProvider()
    instances = _mock_orchestrator(monkeypatch, provider)
    args = SimpleNamespace(
        repo_path=str(repo),
        target_branch="main",
        context_dir=str(jira_context),
        phases=1,
        tiers=1,
    )

    arbitrate.cmd_code_review(args)

    output = capsys.readouterr().out
    assert "Review completed" in output
    assert len(instances) == 1
    assert len(provider.requests) == 3  # two workers and one arbiter

    worker_requests = [r for r in provider.requests if r.model != "opus"]
    arbiter_request = next(r for r in provider.requests if r.model == "opus")
    assert len(worker_requests) == 2
    for request in worker_requests:
        assert "PROJ-482: Handle missing values" in request.prompt
        assert "Acceptance criteria: missing input must return zero" in request.prompt
        assert "SOURCE_CONTEXT_MARKER" in request.prompt
    assert "Worker sonnet" in arbiter_request.prompt
    assert "Worker haiku" in arbiter_request.prompt
    assert "JIRA_CONTEXT_MARKER" in arbiter_request.prompt
    assert "Mock findings from sonnet" in arbiter_request.prompt
    assert "Mock findings from haiku" in arbiter_request.prompt


def test_review_with_no_diff_fails_closed_before_any_model_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    repo = _make_repo(tmp_path)
    _git(repo, "checkout", "--", "service.py")
    provider = MockReviewProvider()
    _mock_orchestrator(monkeypatch, provider)
    args = SimpleNamespace(
        repo_path=str(repo),
        target_branch="main",
        context_dir=None,
        phases=1,
        tiers=1,
    )

    with pytest.raises(ValueError, match="No changes found"):
        arbitrate.cmd_code_review(args)

    assert provider.requests == []
