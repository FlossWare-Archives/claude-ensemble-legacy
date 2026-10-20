"""Fail-closed behavior for review worker failures."""

import json

import pytest

from providers import ModelCapabilities, ModelProvider, ModelRequest, ModelResponse
from arbitration.orchestrator import ArbitrationOrchestrator, TaskType


class FailedWorkers(ModelProvider):
    def __init__(self):
        self.models = []

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
        self.models.append(request.model)
        if request.model in {"sonnet", "haiku"}:
            raise RuntimeError("provider unavailable")
        return ModelResponse(
            provider="test",
            model=request.model or "unknown",
            text=json.dumps({"adjudicated_result": "should not be reached"}),
        )


def test_review_stops_when_no_worker_succeeds():
    provider = FailedWorkers()
    orchestrator = ArbitrationOrchestrator(
        TaskType.CODE_REVIEW,
        "Review this change.",
        providers={"sonnet": provider, "haiku": provider, "opus": provider},
    )
    orchestrator.add_phase(["sonnet", "haiku"], "opus", "Review.")

    with pytest.raises(RuntimeError, match="no successful workers"):
        orchestrator.run()

    assert provider.models == ["sonnet", "haiku"]
