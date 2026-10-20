from __future__ import annotations

from execution import ExecutionContext, ExecutionEngine, RecoveryCandidate, RecoveryPolicy
from execution.nodes import ModelExecution
from providers import ModelCapabilities
from providers.model_provider import ModelProvider, ModelRequest, ModelResponse


class SequenceProvider(ModelProvider):
    name = "sequence"

    def __init__(self, failures: int) -> None:
        self.failures = failures
        self.calls = 0

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
        self.calls += 1
        if self.calls <= self.failures:
            raise RuntimeError("temporary provider failure")
        return ModelResponse(provider=self.name, model=request.model or "default", text="success")


def context() -> ExecutionContext:
    return ExecutionContext(request_id="recovery", objective="test recovery")


def node(provider: ModelProvider, model: str = "haiku") -> ModelExecution:
    return ModelExecution(
        execution_id="model-1",
        stage="solve",
        worker_id=model,
        provider=provider,
        model=model,
        prompt="prompt",
    )


def test_engine_retries_failed_model_within_bound() -> None:
    provider = SequenceProvider(failures=1)
    result = ExecutionEngine(
        recovery_policy=RecoveryPolicy(max_retries=1),
    ).execute(node(provider), context())

    assert result.successful
    assert provider.calls == 2
    assert result.metadata["recovery"] == [
        {
            "action": "retry",
            "attempt": 1,
            "reason": "RuntimeError: temporary provider failure",
            "candidate": None,
        }
    ]


def test_engine_uses_explicit_fallback_factory() -> None:
    primary = SequenceProvider(failures=10)
    fallback = SequenceProvider(failures=0)
    replacement = node(fallback, "sonnet")

    def factory(_: ModelExecution, candidate: RecoveryCandidate) -> ModelExecution | None:
        assert candidate.model == "sonnet"
        return replacement

    result = ExecutionEngine(
        recovery_policy=RecoveryPolicy(),
        recovery_candidates=(RecoveryCandidate(model="sonnet"),),
        recovery_factory=factory,
    ).execute(node(primary), context())

    assert result.successful
    assert primary.calls == 1
    assert fallback.calls == 1
    assert result.model == "sonnet"
    assert result.metadata["recovery"][0]["action"] == "fallback"


def test_engine_abstains_after_failed_recovery() -> None:
    provider = SequenceProvider(failures=10)
    result = ExecutionEngine(
        recovery_policy=RecoveryPolicy(
            max_retries=1,
            allow_fallback=False,
            allow_escalation=False,
        ),
    ).execute(node(provider), context())

    assert result.failed
    assert provider.calls == 2
    assert result.metadata["recovery"][-1]["action"] == "abstain"


def test_engine_rejects_same_target_fallback() -> None:
    provider = SequenceProvider(failures=10)
    original = node(provider, "haiku")

    def factory(current: ModelExecution, candidate: RecoveryCandidate) -> ModelExecution | None:
        assert current is original
        assert candidate.model == "sonnet"
        return current

    result = ExecutionEngine(
        recovery_candidates=(RecoveryCandidate(model="sonnet"),),
        recovery_factory=factory,
    ).execute(original, context())

    assert result.failed
    assert provider.calls == 1
    assert result.metadata["recovery"][-1]["action"] == "abstain"
    assert "same execution target" in result.metadata["recovery"][-1]["reason"]
