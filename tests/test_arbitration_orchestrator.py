import json
from arbitration.orchestrator import ArbiterResult, ArbitrationOrchestrator, ContextManager, TaskType
from providers import ModelCapabilities
from providers.model_provider import ModelResponse


class FakeProvider:
    def __init__(self):
        self.requests = []

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

    def generate(self, request):
        self.requests.append(request)
        if request.model == "opus":
            return ModelResponse(
                provider="fake",
                model=request.model,
                text=json.dumps(
                    {
                        "adjudicated_result": f"stage result for {request.model}",
                        "selected_worker": "sonnet",
                        "rationale": "selected from the supplied evidence",
                        "supporting_evidence": ["worker evidence"],
                        "rejected_alternatives": ["other worker result"],
                        "next_phase_questions": ["recheck the changed behavior"],
                    }
                ),
            )
        return ModelResponse(
            provider="fake",
            model=request.model,
            text=f"worker analysis from {request.model}",
        )


def test_every_stage_runs_multiple_workers_and_an_arbiter():
    provider = FakeProvider()
    orchestrator = ArbitrationOrchestrator(
        TaskType.CODE_REVIEW,
        "Review the change.",
        default_provider=provider,
    )
    orchestrator.auto_phases(num_phases=3)

    result = orchestrator.run()

    assert result == "stage result for opus"
    assert len(orchestrator.results) == 3
    assert all(len(workers) == 2 for workers, _ in orchestrator.results)
    assert all(arbiter.adjudicated_result for _, arbiter in orchestrator.results)
    assert len(provider.requests) == 9


def test_stage_handoff_preserves_worker_results_and_adjudication():
    provider = FakeProvider()
    orchestrator = ArbitrationOrchestrator(
        TaskType.CODE_REVIEW,
        "Review the change.",
        default_provider=provider,
    )
    orchestrator.auto_phases(num_phases=2)

    orchestrator.run()

    worker_prompts = [
        request.prompt
        for request in provider.requests
        if request.model != "opus"
    ]
    stage_one_prompts = worker_prompts[:2]
    stage_two_prompts = worker_prompts[2:]

    assert all("worker analysis from sonnet" not in prompt for prompt in stage_one_prompts)
    assert all("Prior Arbitration Stage 1" in prompt for prompt in stage_two_prompts)
    assert all("worker analysis from sonnet" in prompt for prompt in stage_two_prompts)
    assert all("stage result for opus" in prompt for prompt in stage_two_prompts)
    assert all("Rejected alternatives" in prompt for prompt in stage_two_prompts)


def test_stage_handoff_preserves_long_source_files():
    context = ContextManager(TaskType.CODE_REVIEW)
    source = "x" * 25_000
    context.files["/repo/large.py"] = source

    rendered = context.get_stage_context()

    assert source in rendered
    assert "truncated" not in rendered


def test_arbiter_receives_all_worker_results():
    provider = FakeProvider()
    orchestrator = ArbitrationOrchestrator(
        TaskType.CODE_REVIEW,
        "Review the change.",
        default_provider=provider,
    )
    orchestrator.auto_phases(num_phases=1)

    orchestrator.run()

    arbiter_prompt = next(
        request.prompt for request in provider.requests if request.model == "opus"
    )
    assert "### Worker sonnet" in arbiter_prompt
    assert "### Worker haiku" in arbiter_prompt


def test_configured_stage_count_controls_termination():
    provider = FakeProvider()
    orchestrator = ArbitrationOrchestrator(
        TaskType.BUG_ANALYSIS,
        "Analyze the bug.",
        default_provider=provider,
    )
    orchestrator.auto_phases(num_phases=4)

    orchestrator.run()

    assert len(orchestrator.results) == 4
    assert len(provider.requests) == 12


def test_arbiter_result_preserves_constructor_compatibility():
    legacy = ArbiterResult(
        model="opus",
        phase=1,
        synthesis="legacy result",
        selected_best="sonnet",
    )
    current = ArbiterResult(
        model="opus",
        phase=1,
        adjudicated_result="current result",
        selected_worker="haiku",
    )

    assert legacy.adjudicated_result == "legacy result"
    assert legacy.synthesis == "legacy result"
    assert legacy.selected_worker == "sonnet"
    assert legacy.selected_best == "sonnet"
    assert current.adjudicated_result == "current result"
    assert current.synthesis == "current result"
    assert current.selected_worker == "haiku"
    assert current.selected_best == "haiku"


def test_arbiter_result_rejects_conflicting_aliases():
    try:
        ArbiterResult(
            model="opus",
            phase=1,
            synthesis="legacy",
            adjudicated_result="current",
        )
    except ValueError as exc:
        assert "must match" in str(exc)
    else:
        raise AssertionError("conflicting constructor aliases must be rejected")


def test_arbiter_rejects_malformed_json():
    class MalformedProvider(FakeProvider):
        def generate(self, request):
            self.requests.append(request)
            if request.model == "opus":
                return ModelResponse(
                    provider="fake",
                    model=request.model,
                    text="not json",
                )
            return ModelResponse(
                provider="fake",
                model=request.model,
                text="worker result",
            )

    provider = MalformedProvider()
    orchestrator = ArbitrationOrchestrator(
        TaskType.BUG_ANALYSIS,
        "Analyze the bug.",
        default_provider=provider,
    )
    orchestrator.auto_phases(num_phases=1)

    try:
        orchestrator.run()
    except RuntimeError as exc:
        assert "invalid structured output" in str(exc)
    else:
        raise AssertionError("malformed arbiter output must fail the stage")


def test_arbiter_rejects_incomplete_json():
    class IncompleteProvider(FakeProvider):
        def generate(self, request):
            self.requests.append(request)
            if request.model == "opus":
                return ModelResponse(
                    provider="fake",
                    model=request.model,
                    text=json.dumps({"rationale": "missing adjudicated_result"}),
                )
            return ModelResponse(
                provider="fake",
                model=request.model,
                text="worker result",
            )

    provider = IncompleteProvider()
    orchestrator = ArbitrationOrchestrator(
        TaskType.BUG_ANALYSIS,
        "Analyze the bug.",
        default_provider=provider,
    )
    orchestrator.auto_phases(num_phases=1)

    try:
        orchestrator.run()
    except RuntimeError as exc:
        assert "invalid structured output" in str(exc)
    else:
        raise AssertionError("incomplete arbiter output must fail the stage")


def test_multistage_handoff_preserves_real_loaded_source_artifact():
    from pathlib import Path

    repo_root = Path(__file__).resolve().parents[1]
    source_path = repo_root / "arbitration" / "orchestrator.py"
    source = source_path.read_text(encoding="utf-8")

    provider = FakeProvider()
    orchestrator = ArbitrationOrchestrator(
        TaskType.CODE_REVIEW,
        "Review the loaded source artifact across stages.",
        default_provider=provider,
    )
    orchestrator.context_manager.load_files([source_path])
    orchestrator.auto_phases(num_phases=2)

    orchestrator.run()

    worker_prompts = [
        request.prompt
        for request in provider.requests
        if request.model != "opus"
    ]
    assert len(worker_prompts) == 4
    stage_one_prompts = worker_prompts[:2]
    stage_two_prompts = worker_prompts[2:]

    assert all(str(source_path) in prompt for prompt in stage_one_prompts)
    assert all(source in prompt for prompt in stage_one_prompts)
    assert all(str(source_path) in prompt for prompt in stage_two_prompts)
    assert all(source in prompt for prompt in stage_two_prompts)

    assert all("worker analysis from sonnet" in prompt for prompt in stage_two_prompts)
    assert all("worker analysis from haiku" in prompt for prompt in stage_two_prompts)
    assert all("stage result for opus" in prompt for prompt in stage_two_prompts)
    assert all("Selected worker: sonnet" in prompt for prompt in stage_two_prompts)
    assert all("Evidence: worker evidence" in prompt for prompt in stage_two_prompts)
    assert all("Rejected alternatives: other worker result" in prompt for prompt in stage_two_prompts)
