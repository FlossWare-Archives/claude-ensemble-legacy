from __future__ import annotations

import json

import pytest

from collaboration.orchestrator import CollaborationOrchestrator
from collaboration.reviewer import ReviewerResult
from providers import ClaudeCodeProvider, ModelCapabilities, ModelProvider, ModelRequest, ModelResponse


class FakeProvider(ModelProvider):
    def __init__(self, responses: dict[str, list[str] | str]):
        self.responses = responses
        self.requests: list[ModelRequest] = []

    @property
    def capabilities(self) -> ModelCapabilities:
        # Test doubles declare permissive behavior intentionally; strict
        # composition is separately tested against ClaudeCodeProvider.
        return ModelCapabilities(
            provider="fake",
            supports_system_prompt=True,
            supports_messages=True,
            supports_temperature_override=True,
            supports_max_tokens_override=True,
            supports_timeout_override=True,
        )

    def generate(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
        if request.model is None and "" not in self.responses and len(self.responses) == 1:
            value = next(iter(self.responses.values()))
        else:
            value = self.responses.get(request.model or "", "")
        if isinstance(value, list):
            text = value.pop(0)
        else:
            text = value
        return ModelResponse(
            provider="fake",
            model=request.model or "unknown",
            text=text,
        )


def test_fake_provider_infers_sole_response_for_omitted_model():
    provider = FakeProvider({"arbiter": "arbiter result"})
    response = provider.generate(ModelRequest(prompt="prompt"))
    assert response.text == "arbiter result"


def test_fake_provider_explicit_empty_model_key_takes_precedence():
    provider = FakeProvider({"": "explicit", "arbiter": "inferred"})
    response = provider.generate(ModelRequest(prompt="prompt"))
    assert response.text == "explicit"


def test_fake_provider_does_not_guess_with_multiple_response_keys():
    provider = FakeProvider({"arbiter": "one", "other": "two"})
    response = provider.generate(ModelRequest(prompt="prompt"))
    assert response.text == ""


class FakeReviewer:
    def __init__(self, name: str, verdict: str = "comment"):
        self.name = name
        self.verdict = verdict
        self.calls: list[str] = []

    def review(self, *, candidate_id, candidate, context, focus=""):
        self.calls.append(focus)
        return ReviewerResult(
            reviewer=self.name,
            status="complete",
            verdict=self.verdict,
            summary=f"{self.name} reviewed {candidate_id}",
            findings=[],
            provider="fake",
            model=self.name,
        )


def adjudication(**overrides):
    value = {
        "selected_candidate": "r1-sonnet",
        "decision": "use candidate",
        "rationale": "best supported",
        "supporting_evidence": ["review evidence"],
        "rejected_alternatives": ["r1-haiku"],
        "blocking_concerns": [],
        "follow_ups": [],
        "complete": True,
        "human_decision_required": False,
    }
    value.update(overrides)
    return json.dumps(value)


def test_collaboration_generates_independent_candidates_and_reviews():
    solvers = {
        "sonnet": FakeProvider({"sonnet": "solution A"}),
        "haiku": FakeProvider({"haiku": "solution B"}),
    }
    arbiter = FakeProvider({"arbiter": adjudication()})
    grok = FakeReviewer("grok", "comment")
    perplexity = FakeReviewer("perplexity", "request_changes")

    loop = CollaborationOrchestrator(
        "Design the feature.",
        solvers=solvers,
        arbiter=arbiter,
        reviewers={"grok": grok, "perplexity": perplexity},
    )

    result = loop.run(context="existing architecture")

    assert result.status == "accepted"
    assert result.selected_candidate is not None
    assert result.selected_candidate.proposal == "solution A"
    assert len(loop.state.candidates) == 2
    assert len(loop.state.reviews) == 4
    assert loop.state.audit[0]["event"] == "adjudication"
    assert "existing architecture" in arbiter.requests[0].prompt
    assert "solution A" in arbiter.requests[0].prompt
    assert "solution B" in arbiter.requests[0].prompt


def test_collaboration_does_not_use_majority_vote_and_can_request_targeted_review():
    solvers = {
        "sonnet": FakeProvider({"sonnet": "solution A"}),
        "haiku": FakeProvider({"haiku": "solution B"}),
    }
    arbiter = FakeProvider(
        {
            "arbiter": [
                adjudication(
                    complete=False,
                    blocking_concerns=["security assumption"],
                    follow_ups=[
                        {
                            "reviewer": "grok",
                            "candidate_id": "r1-sonnet",
                            "question": "challenge the security assumption",
                        }
                    ],
                ),
                adjudication(complete=True, rationale="targeted evidence resolved it"),
            ]
        }
    )
    grok = FakeReviewer("grok")
    perplexity = FakeReviewer("perplexity")

    loop = CollaborationOrchestrator(
        "Design the feature.",
        solvers=solvers,
        arbiter=arbiter,
        reviewers={"grok": grok, "perplexity": perplexity},
    )

    result = loop.run()

    assert result.status == "accepted"
    assert grok.calls == ["", "", "challenge the security assumption"]
    assert perplexity.calls == ["", ""]
    assert len(loop.state.adjudications) == 2


def test_collaboration_preserves_unresolved_state_after_max_rounds():
    solvers = {"sonnet": FakeProvider({"sonnet": "solution"})}
    arbiter = FakeProvider(
        {
            "arbiter": [
                adjudication(selected_candidate=None, complete=False, blocking_concerns=["still open"]),
                adjudication(selected_candidate=None, complete=False, blocking_concerns=["still open"]),
            ]
        }
    )

    loop = CollaborationOrchestrator(
        "Solve it.",
        solvers=solvers,
        arbiter=arbiter,
        reviewers={},
        max_rounds=2,
    )

    result = loop.run()

    assert result.status == "unresolved"
    assert result.adjudication["complete"] is False
    assert len(loop.state.candidates) == 2
    assert len(loop.state.adjudications) == 2


def test_collaboration_escalates_only_when_arbiter_requires_human():
    solvers = {"sonnet": FakeProvider({"sonnet": "solution"})}
    arbiter = FakeProvider(
        {
            "arbiter": adjudication(
                complete=False,
                human_decision_required=True,
                decision="choose between incompatible requirements",
            )
        }
    )

    loop = CollaborationOrchestrator(
        "Resolve requirements.",
        solvers=solvers,
        arbiter=arbiter,
        reviewers={},
    )

    result = loop.run()

    assert result.status == "needs_human"
    assert result.adjudication["human_decision_required"] is True


def test_collaboration_preserves_solver_failure_without_fabricating_solution():
    class FailingProvider(FakeProvider):
        def generate(self, request):
            raise RuntimeError("solver unavailable")

    solvers = {"sonnet": FailingProvider({"sonnet": ""})}
    arbiter = FakeProvider(
        {"arbiter": adjudication(selected_candidate=None, complete=False)}
    )

    loop = CollaborationOrchestrator(
        "Solve it.",
        solvers=solvers,
        arbiter=arbiter,
        reviewers={},
    )

    result = loop.run()

    assert result.status == "unresolved"
    assert loop.state.candidates[0].status == "failed"
    assert loop.state.candidates[0].proposal == ""
    assert loop.state.candidates[0].error == "solver unavailable"
    assert loop.state.audit[0]["event"] == "solver_failure"


def test_snapshot_includes_audit():
    loop = CollaborationOrchestrator(
        "Solve it.", solvers={"sonnet": FakeProvider({"sonnet": "solution"})},
        arbiter=FakeProvider({"arbiter": adjudication()}), reviewers={},
    )
    loop.run()
    assert loop.state.snapshot()["audit"]

def test_arbiter_accepts_json_wrapped_in_prose():
    wrapped = "Here is the result:\\n```json\\n" + adjudication() + "\\n```"
    loop = CollaborationOrchestrator(
        "Solve it.", solvers={"sonnet": FakeProvider({"sonnet": "solution"})},
        arbiter=FakeProvider({"arbiter": wrapped}), reviewers={},
    )
    assert loop.run().status == "accepted"

def test_arbiter_rejects_unknown_selected_candidate():
    loop = CollaborationOrchestrator(
        "Solve it.", solvers={"sonnet": FakeProvider({"sonnet": "solution"})},
        arbiter=FakeProvider({"arbiter": adjudication(selected_candidate="r9-nope")}), reviewers={},
    )
    try:
        loop.run()
    except ValueError as exc:
        assert "unknown candidate" in str(exc)
    else:
        raise AssertionError("unknown candidate must fail closed")

@pytest.mark.parametrize("response", ["", "not valid JSON"])
def test_empty_or_malformed_arbiter_output_fails_closed(response):
    loop = CollaborationOrchestrator(
        "Solve it.",
        solvers={"sonnet": FakeProvider({"sonnet": "solution"})},
        arbiter=FakeProvider({"arbiter": response}),
        reviewers={},
    )
    try:
        loop.run()
    except ValueError as exc:
        assert "arbiter response must contain a JSON object" in str(exc)
    else:
        raise AssertionError("empty or malformed arbiter output must fail closed")


def test_complete_adjudication_with_null_selection_cannot_be_accepted():
    loop = CollaborationOrchestrator(
        "Solve it.",
        solvers={"sonnet": FakeProvider({"sonnet": "valid solution"})},
        arbiter=FakeProvider({"arbiter": adjudication(selected_candidate=None)}),
        reviewers={},
    )
    result = loop.run()
    assert result.status == "needs_human"
    assert result.selected_candidate is None
    assert result.adjudication["complete"] is False
    assert any("without selecting" in item for item in result.adjudication["blocking_concerns"])

def test_failed_solver_candidate_cannot_be_accepted():
    class FailingProvider(FakeProvider):
        def generate(self, request):
            raise RuntimeError("solver unavailable")

    loop = CollaborationOrchestrator(
        "Solve it.",
        solvers={"sonnet": FailingProvider({"sonnet": ""})},
        arbiter=FakeProvider({"arbiter": adjudication()}),
        reviewers={},
    )
    result = loop.run()
    assert result.status == "needs_human"
    assert result.selected_candidate is None
    assert loop.state.candidates[0].status == "failed"
    assert any("failed candidate" in item for item in result.adjudication["blocking_concerns"])

def test_empty_solver_output_is_failed_and_cannot_be_accepted():
    loop = CollaborationOrchestrator(
        "Solve it.",
        solvers={"sonnet": FakeProvider({"sonnet": "   "})},
        arbiter=FakeProvider({"arbiter": adjudication()}),
        reviewers={},
    )
    result = loop.run()
    assert result.status == "needs_human"
    assert result.selected_candidate is None
    assert loop.state.candidates[0].status == "failed"
    assert loop.state.candidates[0].error == "solver returned an empty proposal"

def test_historical_candidate_cannot_be_selected_in_a_later_round():
    loop = CollaborationOrchestrator(
        "Solve it.",
        solvers={"sonnet": FakeProvider({"sonnet": ["solution one", "solution two"]})},
        arbiter=FakeProvider({"arbiter": [
            adjudication(complete=False, selected_candidate=None),
            adjudication(selected_candidate="r1-sonnet"),
        ]}),
        reviewers={},
        max_rounds=2,
    )
    result = loop.run()
    assert result.status == "needs_human"
    assert result.selected_candidate is None
    assert result.adjudication["complete"] is False
    assert any("outside the current round" in item for item in result.adjudication["blocking_concerns"])

def test_reviewer_failure_isolated_and_recorded():
    class FailingReviewer(FakeReviewer):
        def review(self, **kwargs):
            raise RuntimeError("reviewer unavailable")
    loop = CollaborationOrchestrator(
        "Solve it.", solvers={"sonnet": FakeProvider({"sonnet": "solution"})},
        arbiter=FakeProvider({"arbiter": adjudication()}), reviewers={"grok": FailingReviewer("grok")},
    )
    result = loop.run()
    assert result.status == "accepted"
    assert loop.state.reviews[0].status == "failed"
    assert "reviewer unavailable" in loop.state.reviews[0].error

def test_solver_audit_is_recorded_after_worker_completion():
    class FailingProvider(FakeProvider):
        def generate(self, request):
            raise RuntimeError("solver unavailable")
    loop = CollaborationOrchestrator(
        "Solve it.", solvers={"sonnet": FailingProvider({"sonnet": ""})},
        arbiter=FakeProvider({"arbiter": adjudication(selected_candidate=None, complete=False)}), reviewers={},
    )
    loop.run()
    assert [item["event"] for item in loop.state.audit] == ["solver_failure", "adjudication", "solver_failure", "adjudication", "solver_failure", "adjudication"]

def test_call_budgets_bound_solver_and_review_calls():
    solvers = {"sonnet": FakeProvider({"sonnet": "solution A"}), "haiku": FakeProvider({"haiku": "solution B"})}
    reviewers = {"grok": FakeReviewer("grok"), "perplexity": FakeReviewer("perplexity")}
    loop = CollaborationOrchestrator(
        "Solve it.", solvers=solvers, arbiter=FakeProvider({"arbiter": adjudication()}), reviewers=reviewers,
        max_solver_calls=1, max_review_calls=1,
    )
    result = loop.run()
    assert result.status == "accepted"
    assert len(loop.state.candidates) == 1
    assert len(loop.state.reviews) == 1


def test_call_budgets_bound_arbiter_calls_and_escalate():
    solvers = {"sonnet": FakeProvider({"sonnet": "solution"})}
    arbiter = FakeProvider({
        "arbiter": adjudication(
            complete=False,
            follow_ups=[{
                "reviewer": "grok",
                "candidate_id": "r1-sonnet",
                "question": "check the remaining concern",
            }],
        )
    })
    loop = CollaborationOrchestrator(
        "Solve it.",
        solvers=solvers,
        arbiter=arbiter,
        reviewers={"grok": FakeReviewer("grok")},
        max_rounds=2,
        max_arbiter_calls=1,
    )
    result = loop.run()
    assert result.status == "needs_human"
    assert result.adjudication["human_decision_required"] is True
    assert any(item["event"] == "arbiter_budget_exhausted" for item in loop.state.audit)
    assert len(arbiter.requests) == 1

def test_collaboration_uses_canonical_model_ids_for_solver_and_arbiter():
    solver = FakeProvider({"claude-sonnet-5": "solution A"})
    arbiter = FakeProvider({"claude-opus-5": adjudication()})
    loop = CollaborationOrchestrator(
        "Design the feature.",
        solvers={"sonnet": solver},
        arbiter=arbiter,
        reviewers={},
        solver_model_ids={"sonnet": "claude-sonnet-5"},
        arbiter_model_id="claude-opus-5",
        max_rounds=1,
    )

    result = loop.run()

    assert result.status == "accepted"
    assert result.selected_candidate is not None
    assert result.selected_candidate.model == "sonnet"
    assert solver.requests[0].model == "claude-sonnet-5"
    assert arbiter.requests[0].model == "claude-opus-5"



def test_configured_model_ids_are_preserved_across_follow_up_adjudications():
    solver = FakeProvider({
        "claude-sonnet-5": ["solution round one", "solution round two"],
    })
    arbiter = FakeProvider({
        "claude-opus-5": [
            adjudication(
                complete=False,
                blocking_concerns=["security assumption"],
                follow_ups=[{
                    "reviewer": "grok",
                    "candidate_id": "r1-sonnet",
                    "question": "challenge the security assumption",
                }],
            ),
            adjudication(complete=True, rationale="follow-up evidence resolved the concern"),
        ],
    })
    loop = CollaborationOrchestrator(
        "Design the feature.",
        solvers={"sonnet": solver},
        arbiter=arbiter,
        reviewers={"grok": FakeReviewer("grok")},
        solver_model_ids={"sonnet": "claude-sonnet-5"},
        arbiter_model_id="claude-opus-5",
        max_rounds=2,
    )

    result = loop.run()

    assert result.status == "accepted"
    # The follow-up re-adjudicates the existing candidate; it does not rerun solvers.
    assert [request.model for request in solver.requests] == ["claude-sonnet-5"]
    assert [request.model for request in arbiter.requests] == [
        "claude-opus-5", "claude-opus-5",
    ]



def test_follow_up_without_configured_reviewer_escalates_without_second_arbiter_call():
    arbiter = FakeProvider({"arbiter": adjudication(
        complete=False,
        follow_ups=[{"reviewer": "grok", "candidate_id": "r1-sonnet", "question": "verify"}],
    )})
    loop = CollaborationOrchestrator(
        "Review it.", solvers={"sonnet": FakeProvider({"sonnet": "solution"})},
        arbiter=arbiter, reviewers={}, max_rounds=1, max_arbiter_calls=3,
    )
    result = loop.run()
    assert result.status == "needs_human"
    assert len(arbiter.requests) == 1
    assert result.adjudication["human_decision_required"] is True
    assert any(item["event"] == "targeted_follow_ups_unavailable" for item in loop.state.audit)

def test_failed_follow_up_reviewer_escalates_without_second_arbiter_call():
    class FailingReviewer(FakeReviewer):
        def review(self, **kwargs):
            raise RuntimeError("reviewer unavailable")

    arbiter = FakeProvider(
        {
            "arbiter": [
                adjudication(
                    complete=False,
                    blocking_concerns=["security assumption"],
                    follow_ups=[
                        {
                            "reviewer": "grok",
                            "candidate_id": "r1-sonnet",
                            "question": "verify the security assumption",
                        }
                    ],
                ),
                adjudication(complete=True, rationale="should not be reached"),
            ]
        }
    )
    loop = CollaborationOrchestrator(
        "Review it.",
        solvers={"sonnet": FakeProvider({"sonnet": "solution"})},
        arbiter=arbiter,
        reviewers={"grok": FailingReviewer("grok")},
        max_rounds=1,
        max_arbiter_calls=3,
    )

    result = loop.run()

    assert result.status == "needs_human"
    assert len(arbiter.requests) == 1
    assert result.adjudication["human_decision_required"] is True
    assert any(
        "no usable review evidence" in item
        for item in result.adjudication["blocking_concerns"]
    )
    assert loop.state.reviews[-1].status == "failed"
    assert any(
        item["event"] == "targeted_follow_ups_unavailable"
        for item in loop.state.audit
    )




def test_real_claude_code_provider_composes_with_candidate_generation_and_arbiter(
    monkeypatch,
):
    """Exercise strict provider validation while mocking only Claude CLI I/O."""
    import subprocess

    import providers.claude_code as claude_code_module

    arbiter_result = adjudication()
    cli_results = iter(["solution from Claude Code", arbiter_result])
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

    provider = ClaudeCodeProvider(
        executable="claude",
        expected_provider="vertex",
    )

    monkeypatch.setattr(claude_code_module.shutil, "which", lambda _: "/mock/claude")

    def fake_preflight(*args, **kwargs):
        return subprocess.CompletedProcess(
            args=args[0], returncode=0, stdout=json.dumps({"apiProvider": "vertex"}), stderr=""
        )

    monkeypatch.setattr(claude_code_module.subprocess, "run", fake_preflight)

    def fake_start_process(command):
        commands.append(command)
        return FakeProcess(next(cli_results))

    monkeypatch.setattr(provider, "_start_process", fake_start_process)

    loop = CollaborationOrchestrator(
        "Design a safe feature.",
        solvers={"sonnet": provider},
        arbiter=provider,
        reviewers={},
        solver_model_ids={"sonnet": "claude-sonnet-4-5"},
        arbiter_model_id="claude-opus-4-1",
        max_rounds=1,
    )

    result = loop.run()

    assert result.status == "accepted"
    assert result.selected_candidate is not None
    assert result.selected_candidate.proposal == "solution from Claude Code"
    assert len(commands) == 2
    assert commands[0][0] == "/mock/claude"
    assert commands[1][0] == "/mock/claude"
    assert "--model" in commands[0]
    assert "claude-sonnet-4-5" in commands[0]
    assert "--model" in commands[1]
    assert "claude-opus-4-1" in commands[1]
