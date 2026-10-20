"""Execution-engine integration tests for canonical Memory retrieval."""

from __future__ import annotations

from dataclasses import dataclass, field
import json

import pytest

from execution.context import ExecutionContext, ExecutionStatus
from execution.engine import ExecutionEngine
from execution.nodes import CompositeExecution, ModelExecution, PipelineExecution
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
        return ModelResponse(
            provider="fake",
            model=request.model or "fake-model",
            text="model-result",
        )


@dataclass
class FakeMemory:
    entries: list[dict] = field(default_factory=list)
    error: str | None = None
    calls: list[tuple[str, ExecutionContext, int]] = field(default_factory=list)
    writes: list[tuple[str, dict, ExecutionContext | None]] = field(default_factory=list)
    write_error: Exception | None = None
    write_rejected: bool = False

    def append(
        self,
        name: str,
        entry: dict,
        *,
        context: ExecutionContext | None = None,
    ) -> bool:
        if self.write_error is not None:
            raise self.write_error
        if self.write_rejected:
            return False
        self.writes.append((name, entry, context))
        return True

    def retrieve_with_status(
        self,
        name: str,
        context: ExecutionContext,
        *,
        limit: int = 10,
    ) -> tuple[list[dict], str | None]:
        self.calls.append((name, context, limit))
        if self.error is not None:
            return [], self.error
        return self.entries, None


@pytest.mark.parametrize(
    "memory_name",
    ["", "   ", "session learnings", "session/learnings", ".", "..", None],
)
def test_memory_name_rejects_invalid_configuration(memory_name) -> None:
    with pytest.raises(ValueError, match="Invalid memory name"):
        ExecutionEngine(memory_name=memory_name)


def test_memory_name_accepts_names_allowed_by_memory_service() -> None:
    engine = ExecutionEngine(memory_name="session_learnings.v2-prod")

    assert engine.memory_name == "session_learnings.v2-prod"


@pytest.mark.parametrize("memory_max_bytes", [0, -1, 1.5, "1024", True])
def test_memory_max_bytes_rejects_invalid_configuration(memory_max_bytes) -> None:
    with pytest.raises(ValueError, match="memory_max_bytes must be a positive integer"):
        ExecutionEngine(memory_max_bytes=memory_max_bytes)


def test_memory_retrieval_is_bounded_by_serialized_byte_budget() -> None:
    provider = FakeProvider()
    first = {"record": {"lesson": "first"}}
    second = {"record": {"lesson": "second"}}

    def serialize(entry: dict) -> bytes:
        return json.dumps(
            entry, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")

    first_size = len(serialize(first))
    second_size = len(serialize(second))
    memory = FakeMemory(entries=[first, second])
    engine = ExecutionEngine(
        memory_client=memory,
        memory_name="session_learnings",
        memory_limit=10,
        memory_max_bytes=first_size,
    )

    model = ModelExecution(
        execution_id="model-1",
        stage="model",
        provider=provider,
        prompt_builder=lambda context: repr(context.memory_context),
    )

    result = engine.execute(
        model,
        ExecutionContext(request_id="request-1", objective="test memory byte budget"),
    )

    assert result.status is ExecutionStatus.SUCCESS
    assert provider.prompts == [repr((first,))]
    retrieval = result.metadata["context"]["metadata"]["memory_retrieval"]
    assert retrieval == {
        "status": "success",
        "count": 1,
        "original_count": 2,
        "retained_count": 1,
        "original_bytes": first_size + second_size,
        "retained_bytes": first_size,
        "truncated": True,
    }


def test_memory_retrieval_stops_at_first_record_that_exceeds_byte_budget() -> None:
    provider = FakeProvider()
    first = {"record": {"lesson": "a"}}
    oversized = {"record": {"lesson": "this record does not fit"}}
    later = {"record": {"lesson": "b"}}

    def serialize(entry: dict) -> bytes:
        return json.dumps(
            entry, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")

    first_size = len(serialize(first))
    oversized_size = len(serialize(oversized))
    later_size = len(serialize(later))
    memory = FakeMemory(entries=[first, oversized, later])
    engine = ExecutionEngine(
        memory_client=memory,
        memory_name="session_learnings",
        memory_limit=10,
        memory_max_bytes=first_size + later_size,
    )

    model = ModelExecution(
        execution_id="model-1",
        stage="model",
        provider=provider,
        prompt_builder=lambda context: repr(context.memory_context),
    )

    result = engine.execute(
        model,
        ExecutionContext(request_id="request-1", objective="test retrieval order"),
    )

    assert result.status is ExecutionStatus.SUCCESS
    assert provider.prompts == [repr((first,))]
    retrieval = result.metadata["context"]["metadata"]["memory_retrieval"]
    assert retrieval == {
        "status": "success",
        "count": 1,
        "original_count": 3,
        "retained_count": 1,
        "original_bytes": first_size + oversized_size + later_size,
        "retained_bytes": first_size,
        "truncated": True,
    }


def test_memory_retrieval_budget_failure_is_reported_for_non_serializable_record() -> None:
    provider = FakeProvider()
    memory = FakeMemory(entries=[{"record": object()}])
    engine = ExecutionEngine(memory_client=memory)

    model = ModelExecution(
        execution_id="model-1",
        stage="model",
        provider=provider,
        prompt="continue without malformed memory",
    )

    result = engine.execute(
        model,
        ExecutionContext(request_id="request-1", objective="test malformed memory"),
    )

    assert result.status is ExecutionStatus.SUCCESS
    assert provider.prompts == ["continue without malformed memory"]
    assert result.metadata["context"]["memory_context"] == ()
    retrieval = result.metadata["context"]["metadata"]["memory_retrieval"]
    assert retrieval["status"] == "failure"
    assert retrieval["count"] == 0
    assert retrieval["error"].startswith("TypeError: ")
    

def test_memory_context_flows_through_nested_composite_and_pipeline() -> None:
    provider = FakeProvider()
    memory = FakeMemory(entries=[{"record": {"lesson": "use the canonical context"}}])
    engine = ExecutionEngine(memory_client=memory, memory_name="session_learnings", memory_limit=4)

    model = ModelExecution(
        execution_id="model-1",
        stage="model",
        provider=provider,
        prompt_builder=lambda context: (
            f"memory={context.memory_context[0]['record']['lesson']};"
            f"lineage={','.join(context.lineage)}"
        ),
    )
    pipeline = PipelineExecution(
        execution_id="pipeline-1",
        stage="pipeline",
        children=(model,),
    )
    root = CompositeExecution(
        execution_id="composite-1",
        stage="composite",
        children=(pipeline,),
        quorum=1,
    )

    result = engine.execute(
        root,
        ExecutionContext(request_id="request-1", objective="test memory flow"),
    )

    assert result.status is ExecutionStatus.SUCCESS
    assert provider.prompts == [
        "memory=use the canonical context;lineage=composite-1,pipeline-1,model-1"
    ]
    assert len(memory.calls) == 1
    name, retrieved_context, limit = memory.calls[0]
    assert name == "session_learnings"
    assert limit == 4
    assert retrieved_context.lineage == ("composite-1", "pipeline-1", "model-1")
    assert retrieved_context.memory_context == ()

    model_result = result.children[0].children[0]
    persisted_context = model_result.metadata["context"]
    assert persisted_context["memory_context"] == tuple(memory.entries)
    serialized = json.dumps(
        memory.entries[0], ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    assert persisted_context["metadata"]["memory_retrieval"] == {
        "status": "success",
        "count": 1,
        "original_count": 1,
        "retained_count": 1,
        "original_bytes": len(serialized),
        "retained_bytes": len(serialized),
        "truncated": False,
    }


def test_memory_failure_is_distinguishable_from_successful_model_execution() -> None:
    provider = FakeProvider()
    memory = FakeMemory(error="memory service unavailable")
    engine = ExecutionEngine(memory_client=memory)

    model = ModelExecution(
        execution_id="model-1",
        stage="model",
        provider=provider,
        prompt="continue without memory",
    )

    result = engine.execute(
        model,
        ExecutionContext(request_id="request-1", objective="test memory failure"),
    )

    assert result.status is ExecutionStatus.SUCCESS
    assert result.output == "model-result"
    assert result.metadata["context"]["memory_context"] == ()
    assert result.metadata["context"]["metadata"]["memory_retrieval"] == {
        "status": "failure",
        "error": "memory service unavailable",
        "count": 0,
    }


@dataclass
class FailingProvider:
    @property
    def capabilities(self) -> ModelCapabilities:
        return ModelCapabilities(
            provider="test-failing",
            supports_system_prompt=True,
            supports_messages=True,
            supports_temperature_override=True,
            supports_max_tokens_override=True,
            supports_timeout_override=True,
        )

    def generate(self, request: ModelRequest) -> ModelResponse:
        raise RuntimeError("provider failed")


def test_execution_results_are_persisted_with_canonical_identity_and_lineage() -> None:
    provider = FakeProvider()
    memory = FakeMemory()
    engine = ExecutionEngine(memory_client=memory, memory_name="session_learnings")

    model = ModelExecution(
        execution_id="model-1",
        stage="model",
        provider=provider,
        prompt="persist this result",
    )
    pipeline = PipelineExecution(
        execution_id="pipeline-1",
        stage="pipeline",
        children=(model,),
    )
    root = CompositeExecution(
        execution_id="composite-1",
        stage="composite",
        children=(pipeline,),
        quorum=1,
    )

    result = engine.execute(
        root,
        ExecutionContext(request_id="request-1", objective="test writeback"),
    )

    assert result.status is ExecutionStatus.SUCCESS
    assert [entry["execution_result"]["execution_id"] for _, entry, _ in memory.writes] == [
        "model-1",
        "pipeline-1",
        "composite-1",
    ]

    by_id = {entry["execution_result"]["execution_id"]: (entry, context) for _, entry, context in memory.writes}
    model_entry, model_context = by_id["model-1"]
    assert model_entry["execution_result"]["status"] == "success"
    assert model_entry["execution_result"]["output"] == "model-result"
    assert model_context is not None
    assert model_context.lineage == ("composite-1", "pipeline-1", "model-1")

    pipeline_entry, pipeline_context = by_id["pipeline-1"]
    assert pipeline_entry["execution_result"]["status"] == "success"
    assert pipeline_context is not None
    assert pipeline_context.lineage == ("composite-1", "pipeline-1")

    composite_entry, composite_context = by_id["composite-1"]
    assert composite_entry["execution_result"]["status"] == "success"
    assert composite_context is not None
    assert composite_context.lineage == ("composite-1",)


def test_failed_and_partial_results_keep_their_actual_status_in_memory() -> None:
    success_provider = FakeProvider()
    failure_provider = FailingProvider()
    memory = FakeMemory()
    engine = ExecutionEngine(memory_client=memory)

    success = ModelExecution(
        execution_id="success-model",
        stage="worker",
        provider=success_provider,
        prompt="succeed",
    )
    failure = ModelExecution(
        execution_id="failure-model",
        stage="worker",
        provider=failure_provider,
        prompt="fail",
    )
    root = CompositeExecution(
        execution_id="composite-1",
        stage="composite",
        children=(success, failure),
        quorum=1,
    )

    result = engine.execute(
        root,
        ExecutionContext(request_id="request-1", objective="test status writeback"),
    )

    assert result.status is ExecutionStatus.PARTIAL
    persisted = {
        entry["execution_result"]["execution_id"]: entry["execution_result"]
        for _, entry, _ in memory.writes
    }
    assert persisted["success-model"]["status"] == "success"
    assert persisted["success-model"]["output"] == "model-result"
    assert persisted["failure-model"]["status"] == "failure"
    assert persisted["failure-model"]["error"] == "RuntimeError: provider failed"
    assert persisted["failure-model"]["output"] is None
    assert persisted["composite-1"]["status"] == "partial"


def test_memory_writeback_failure_does_not_change_execution_result() -> None:
    provider = FakeProvider()
    memory = FakeMemory(write_error=RuntimeError("memory service unavailable"))
    engine = ExecutionEngine(memory_client=memory)

    model = ModelExecution(
        execution_id="model-1",
        stage="model",
        provider=provider,
        prompt="continue without persistence",
    )

    result = engine.execute(
        model,
        ExecutionContext(request_id="request-1", objective="test writeback failure"),
    )

    assert result.status is ExecutionStatus.SUCCESS
    assert result.output == "model-result"
    assert memory.writes == []


def test_memory_writeback_rejection_does_not_change_execution_result(caplog) -> None:
    provider = FakeProvider()
    memory = FakeMemory(write_rejected=True)
    engine = ExecutionEngine(memory_client=memory)

    model = ModelExecution(
        execution_id="model-1",
        stage="model",
        provider=provider,
        prompt="continue after persistence rejection",
    )

    with caplog.at_level("WARNING", logger="execution.engine"):
        result = engine.execute(
            model,
            ExecutionContext(request_id="request-1", objective="test writeback rejection"),
        )

    assert result.status is ExecutionStatus.SUCCESS
    assert result.output == "model-result"
    assert memory.writes == []
    assert "Memory write-back was rejected for execution model-1" in caplog.text
