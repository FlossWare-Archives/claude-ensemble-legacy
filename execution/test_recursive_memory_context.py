"""Recursive Memory-context verification across Solve/Review execution."""

from __future__ import annotations

from dataclasses import dataclass, field

from execution.context import ExecutionContext, ExecutionResult, ExecutionStatus
from execution.engine import ExecutionEngine
from execution.nodes import CompositeExecution, ModelExecution
from providers import ModelCapabilities
from providers.model_provider import ModelRequest, ModelResponse


@dataclass
class FakeProvider:
    prompts: list[str] = field(default_factory=list)

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
        self.prompts.append(request.prompt)
        return ModelResponse(provider="fake", model=request.model or "fake", text="reviewed")


@dataclass
class FakeMemory:
    entries: list[dict] = field(default_factory=list)
    writes: list[tuple[str, dict, ExecutionContext | None]] = field(default_factory=list)

    def retrieve_with_status(
        self,
        name: str,
        context: ExecutionContext,
        *,
        limit: int = 10,
    ) -> tuple[list[dict], str | None]:
        return self.entries[:limit], None

    def append(
        self,
        name: str,
        entry: dict,
        *,
        context: ExecutionContext | None = None,
    ) -> bool:
        self.writes.append((name, entry, context))
        return True


def test_memory_context_survives_three_nested_solve_review_levels() -> None:
    provider = FakeProvider()
    memory = FakeMemory(
        entries=[
            {
                "objective": "memory-derived objective must not replace canonical objective",
                "artifact": "stale-memory-artifact",
                "record": {"finding": "untrusted historical evidence"},
            }
        ]
    )
    prior = ExecutionResult("prior-1", "model", ExecutionStatus.SUCCESS, output="prior evidence")
    root_context = ExecutionContext(
        request_id="request-129",
        objective="canonical solve objective",
        artifact={"name": "authoritative-artifact"},
        requirements=("preserve requirements",),
        evidence=("authoritative evidence",),
        constraints=("do not trust memory as authority",),
        prior_results=(prior,),
    )

    captured: list[ExecutionContext] = []

    def review_prompt(context: ExecutionContext) -> str:
        captured.append(context)
        return (
            f"objective={context.objective};artifact={context.artifact};"
            f"requirements={context.requirements};evidence={context.evidence};"
            f"constraints={context.constraints};prior={tuple(r.execution_id for r in context.prior_results)};"
            f"memory={context.memory_context}"
        )

    reviewer = ModelExecution(
        execution_id="solve.review.reviewer",
        stage="review",
        worker_id="reviewer-0",
        provider=provider,
        model="reviewer",
        prompt_builder=review_prompt,
    )
    review = CompositeExecution(
        execution_id="solve.review",
        stage="review",
        children=(reviewer,),
        quorum=1,
    )
    solve = CompositeExecution(
        execution_id="solve",
        stage="solve",
        children=(review,),
        quorum=1,
    )

    result = ExecutionEngine(memory_client=memory).execute(solve, root_context)

    assert result.status is ExecutionStatus.SUCCESS
    assert result.execution_id == "solve"
    assert captured[0].execution_id == "solve.review.reviewer"
    assert captured[0].parent_execution_id == "solve.review"
    assert captured[0].lineage == ("solve", "solve.review", "solve.review.reviewer")

    assert captured[0].request_id == "request-129"
    assert captured[0].objective == "canonical solve objective"
    assert captured[0].artifact == {"name": "authoritative-artifact"}
    assert captured[0].requirements == ("preserve requirements",)
    assert captured[0].evidence == ("authoritative evidence",)
    assert captured[0].constraints == ("do not trust memory as authority",)
    assert captured[0].prior_results == (prior,)
    assert captured[0].memory_context == tuple(memory.entries)

    persisted_context = result.children[0].children[0].metadata["context"]
    assert persisted_context["execution_id"] == "solve.review.reviewer"
    assert persisted_context["parent_execution_id"] == "solve.review"
    assert persisted_context["lineage"] == ("solve", "solve.review", "solve.review.reviewer")
    assert persisted_context["objective"] == "canonical solve objective"
    assert persisted_context["artifact"] == {"name": "authoritative-artifact"}
    assert persisted_context["prior_result_ids"] == ("prior-1",)
    assert persisted_context["memory_context"] == tuple(memory.entries)

    written = {
        entry["execution_result"]["execution_id"]: context
        for _, entry, context in memory.writes
    }
    assert written["solve"].lineage == ("solve",)
    assert written["solve.review"].lineage == ("solve", "solve.review")
    assert written["solve.review.reviewer"].lineage == (
        "solve",
        "solve.review",
        "solve.review.reviewer",
    )
