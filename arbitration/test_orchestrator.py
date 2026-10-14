from __future__ import annotations

import json

import pytest

from providers import ModelProvider, ModelRequest, ModelResponse

from arbitration.orchestrator import (
    ArbiterResult,
    ArbitrationOrchestrator,
    ModelPool,
    TaskType,
    TeachingSignal,
)


ARBITER_RESPONSE = json.dumps(
    {
        "adjudicated_result": "arbiter synthesis",
        "selected_worker": "sonnet",
        "rationale": "selected",
        "supporting_evidence": ["worker analysis"],
        "rejected_alternatives": [],
        "next_phase_questions": ["verify the boundary"],
    }
)


class FakeProvider(ModelProvider):
    def __init__(self, responses: dict[str, str], failures: set[str] | None = None):
        self.responses = responses
        self.failures = failures or set()
        self.requests: list[ModelRequest] = []

    def generate(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
        if request.model in self.failures:
            raise RuntimeError(f"provider failure for {request.model}")
        return ModelResponse(
            provider="fake",
            model=request.model or "unknown",
            text=self.responses.get(request.model or "", f"response:{request.model}"),
        )


def test_arbitration_uses_real_provider_results() -> None:
    provider = FakeProvider(
        {
            "sonnet": "worker analysis",
            "haiku": "second worker analysis",
            "opus": ARBITER_RESPONSE,
        }
    )
    orchestrator = ArbitrationOrchestrator(
        TaskType.CODE_REVIEW,
        "Review the supplied change.",
        providers={"sonnet": provider, "haiku": provider, "opus": provider},
    )
    orchestrator.add_phase(["sonnet", "haiku"], "opus", "Analyze the change.")

    result = orchestrator.run()

    assert result == "arbiter synthesis"
    assert len(provider.requests) == 3
    assert [request.model for request in provider.requests] == ["sonnet", "haiku", "opus"]
    assert "worker analysis" in provider.requests[2].prompt
    assert "second worker analysis" in provider.requests[2].prompt
    assert "Review the supplied change." in provider.requests[2].prompt


def test_worker_failure_is_reported_not_fabricated() -> None:
    provider = FakeProvider(
        {
            "haiku": "second worker analysis",
            "opus": ARBITER_RESPONSE,
        },
        failures={"sonnet"},
    )
    orchestrator = ArbitrationOrchestrator(
        TaskType.CODE_REVIEW,
        "Review the supplied change.",
        providers={"sonnet": provider, "haiku": provider, "opus": provider},
    )
    orchestrator.add_phase(["sonnet", "haiku"], "opus", "Analyze the change.")

    assert orchestrator.run() == "arbiter synthesis"
    worker_results = orchestrator.results[0][0]
    assert worker_results[0].succeeded is False
    assert worker_results[0].analysis == ""
    assert worker_results[0].error == "provider failure for sonnet"
    assert "FAILED" in provider.requests[2].prompt
    assert "second worker analysis" in provider.requests[2].prompt


def test_unknown_model_fails_explicitly() -> None:
    orchestrator = ArbitrationOrchestrator(
        TaskType.CODE_REVIEW,
        "Review the supplied change.",
    )
    orchestrator.add_phase(["gemini"], "opus", "Analyze the change.")

    result = orchestrator._run_workers(orchestrator.phases[0], "evidence")

    assert result[0].succeeded is False
    assert "No ModelProvider configured" in (result[0].error or "")


def test_auto_phase_uses_provider_models() -> None:
    provider = FakeProvider(
        {
            "sonnet": "worker",
            "haiku": "worker",
            "opus": ARBITER_RESPONSE,
        }
    )
    orchestrator = ArbitrationOrchestrator(
        TaskType.CODE_REVIEW,
        "Review the supplied change.",
        providers={"sonnet": provider, "haiku": provider, "opus": provider},
    )

    orchestrator.auto_phases()
    assert orchestrator.phases[0].workers == ["sonnet", "haiku"]
    assert orchestrator.phases[0].arbiter == "opus"
    assert orchestrator.run() == "arbiter synthesis"


def test_auto_phases_support_multiple_phases_with_explicit_reuse() -> None:
    provider = FakeProvider(
        {
            "sonnet": "worker",
            "haiku": "worker",
            "opus": ARBITER_RESPONSE,
        }
    )
    orchestrator = ArbitrationOrchestrator(
        TaskType.CODE_REVIEW,
        "Review the supplied change.",
        providers={"sonnet": provider, "haiku": provider, "opus": provider},
    )

    orchestrator.auto_phases(num_phases=2)

    assert len(orchestrator.phases) == 2
    assert orchestrator.phases[0].workers == ["sonnet", "haiku"]
    assert orchestrator.phases[0].arbiter == "opus"
    assert orchestrator.phases[1].workers == ["sonnet", "haiku"]
    assert orchestrator.phases[1].arbiter == "opus"

    assert orchestrator.run() == "arbiter synthesis"
    assert len(provider.requests) == 6
    # The second-stage workers receive the arbiter explanation as an explicit
    # teaching signal rather than relying on incidental prose placement.
    second_stage_worker_prompt = provider.requests[3].prompt
    assert "Arbiter Teaching Signal" in second_stage_worker_prompt
    assert "Rationale: selected" in second_stage_worker_prompt
    assert "Evidence: worker analysis" in second_stage_worker_prompt
    assert "Questions for the next stage: verify the boundary" in second_stage_worker_prompt


def test_legacy_stage_history_retains_arbiter_teaching_fields() -> None:
    orchestrator = ArbitrationOrchestrator(
        TaskType.CODE_REVIEW,
        "Review the supplied change.",
    )
    orchestrator.context_manager.stage_history.append(
        {
            "phase": 1,
            "workers": [],
            "arbiter": {
                "model": "opus",
                "adjudicated_result": "arbiter synthesis",
                "selected_worker": "sonnet",
                "rationale": "legacy rationale",
                "supporting_evidence": ["legacy evidence"],
                "rejected_alternatives": ["legacy alternative"],
                "next_phase_questions": [],
            },
        }
    )

    context = orchestrator.context_manager.get_stage_context()

    assert "### Arbiter Adjudication" in context
    assert "arbiter synthesis" in context
    assert "### Arbiter Teaching Signal" in context
    assert "Rationale: legacy rationale" in context
    assert "Evidence: legacy evidence" in context
    assert "Rejected alternatives: legacy alternative" in context


def test_teaching_signal_is_structured_and_serializable() -> None:
    provider = FakeProvider({"opus": ARBITER_RESPONSE})
    orchestrator = ArbitrationOrchestrator(
        TaskType.CODE_REVIEW,
        "Review the supplied change.",
        providers={"opus": provider},
    )
    orchestrator.add_phase([], "opus", "Analyze the change.")
    # Construct the signal from the same arbiter contract used by the pipeline.
    result = orchestrator._parse_arbiter_response(ARBITER_RESPONSE)
    arbiter = ArbiterResult(
        model="opus",
        phase=1,
        adjudicated_result=result["adjudicated_result"],
        selected_worker=result["selected_worker"],
        rationale=result["rationale"],
        supporting_evidence=result["supporting_evidence"],
        rejected_alternatives=result["rejected_alternatives"],
        next_phase_questions=result["next_phase_questions"],
    )
    signal = TeachingSignal.from_arbiter(arbiter)
    assert signal.to_dict() == {
        "phase": 1,
        "rationale": "selected",
        "supporting_evidence": ["worker analysis"],
        "rejected_alternatives": [],
        "next_phase_questions": ["verify the boundary"],
    }


def test_model_pool_rejects_arbiter_when_current_phase_has_no_free_model() -> None:
    pool = ModelPool()

    assert pool.get_workers(3) == ["sonnet", "haiku", "opus"]

    with pytest.raises(ValueError, match="current phase"):
        pool.get_arbiter()


def test_model_pool_releases_role_reservations_between_phases() -> None:
    pool = ModelPool()

    assert pool.get_workers(1) == ["sonnet"]
    assert pool.get_arbiter() == "opus"

    with pytest.raises(ValueError, match="worker model"):
        pool.get_workers(2)

    pool.reset_phase()

    assert pool.get_workers(1) == ["sonnet"]
    assert pool.get_arbiter() == "opus"


def test_builtin_claude_models_use_the_explicit_default_provider() -> None:
    orchestrator = ArbitrationOrchestrator(
        TaskType.CODE_REVIEW,
        "Review the supplied change.",
    )

    assert type(orchestrator._provider_for("sonnet")).__name__ == "ClaudeCodeProvider"

def test_worker_prompt_includes_original_task_and_current_phase_evidence() -> None:
    orchestrator = ArbitrationOrchestrator(
        TaskType.DESIGN_VALIDATION,
        "Execution output: worker-a verified objective.",
        providers={"worker-a": FakeProvider({"worker-a": "worker evidence"})},
    )
    orchestrator.add_phase(["worker-a"], "arbiter", "Evaluate the result.")
    request = orchestrator._worker_request(
        orchestrator.phases[0], "worker-a", "Current context: prior-stage state."
    )
    assert "Execution output: worker-a verified objective." in request.prompt
    assert "Current context: prior-stage state." in request.prompt
