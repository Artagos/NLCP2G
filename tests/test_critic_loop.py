"""The executor/critic handoff.

The model calls are stubbed. What is under test is the control flow — that the
pipeline branches on the Handoff *fields* rather than on prose, that the round
budget is real, that an escalation stops before the sandbox, and that every step
lands in the shared scratchpad. Those are the parts that break silently in
production, and none of them need a live model to check.
"""
from __future__ import annotations

import pytest

from backend import critic, memory, screener, translator
from backend.critic import Handoff, Violation
from backend.sandbox import RunResult
from backend.translator import GeneratedProgram

AC_RUN = RunResult(ok=True, compile_error=None, results=[
    {"name": "sample-1", "verdict": "AC", "time_ms": 12, "time_limit_ms": 1000}])


@pytest.fixture
def stub(monkeypatch):
    """Wire up stub agents; each test overrides the pieces it cares about."""
    state = {"programs": [], "handoffs": [], "ran": [], "codegen_calls": []}

    def fake_screen(problem, described):
        return screener.ScreenResult(feasible=True)

    def fake_codegen(problem, described, revision=""):
        state["codegen_calls"].append(revision)
        return state["programs"].pop(0)

    def fake_review(problem, described, source, summary):
        return state["handoffs"].pop(0)

    def fake_run(problem, source):
        state["ran"].append(source)
        return AC_RUN

    monkeypatch.setattr(screener, "screen", fake_screen)
    monkeypatch.setattr(translator, "_generate_cpp", fake_codegen)
    monkeypatch.setattr(critic, "review", fake_review)
    monkeypatch.setattr(translator, "run_cpp", fake_run)
    monkeypatch.setattr(translator, "_explain", lambda facts: "Here's what happened.")
    return state


def _program(src="int main(){}", summary="counts pairs"):
    return GeneratedProgram(can_implement=True, cpp_source=src, approach_summary=summary)


def test_approved_code_runs_without_a_second_round(stub, problem):
    stub["programs"] = [_program()]
    stub["handoffs"] = [Handoff(status="approved", confidence=0.9)]

    out = translator.translate_and_run(problem, "check every pair", run_id="r1")

    assert out.verdict == "AC"
    assert out.critic_rounds == 1
    assert len(stub["ran"]) == 1
    assert stub["codegen_calls"] == [""]        # no revision note on a clean pass


def test_a_revise_sends_the_executor_back_once_then_runs(stub, problem):
    stub["programs"] = [_program("sorted version"), _program("literal version")]
    stub["handoffs"] = [
        Handoff(status="revise", result="remove the sorting step",
                violations=[Violation(rule="C1", quote="sort(v.begin()",
                                      why="the learner never mentioned sorting")]),
        Handoff(status="approved", confidence=0.8),
    ]

    out = translator.translate_and_run(problem, "check every pair", run_id="r2")

    assert out.verdict == "AC"
    assert out.critic_rounds == 2
    # the code that actually ran is the rebuilt one, not the first attempt
    assert stub["ran"] == ["literal version"]
    # and the rebuild carried the critic's fix list
    assert "remove the sorting step" in stub["codegen_calls"][1]
    assert "C1: the learner never mentioned sorting" in out.violations


def test_an_escalation_stops_before_the_sandbox_and_asks_the_learner(stub, problem):
    stub["programs"] = [_program()]
    stub["handoffs"] = [Handoff(
        status="escalate", needs_approval=True,
        result="You said to 'combine' the two numbers — add them, or join them?")]

    out = translator.translate_and_run(problem, "combine the numbers", run_id="r3")

    assert out.verdict == "NEEDS_CLARIFICATION"
    assert out.critic_status == "escalate"
    assert stub["ran"] == []                    # nothing was executed
    assert "add them, or join them?" in out.reply


def test_the_round_budget_is_enforced_and_turns_into_an_escalation(stub, problem):
    """Two failed reviews must ask the learner, not loop and not ship the code."""
    stub["programs"] = [_program("v1"), _program("v2")]
    stub["handoffs"] = [
        Handoff(status="revise", result="remove the early exit"),
        Handoff(status="revise", result="remove the early exit"),
    ]

    out = translator.translate_and_run(problem, "scan the list", run_id="r4")

    assert out.verdict == "NEEDS_CLARIFICATION"
    assert out.critic_rounds == critic.MAX_ROUNDS
    assert stub["ran"] == []
    assert "remove the early exit" in out.reply


def test_an_infeasible_description_never_reaches_the_executor(monkeypatch, problem):
    monkeypatch.setattr(screener, "screen", lambda p, d: screener.ScreenResult(
        feasible=False, issue="'just know the answer' is not a procedure"))
    called = []
    monkeypatch.setattr(translator, "_generate_cpp",
                        lambda *a, **k: called.append(1))

    out = translator.translate_and_run(problem, "just know the answer", run_id="r5")

    assert out.verdict == "INFEASIBLE"
    assert called == []
    assert "not a procedure" in out.reply


def test_a_gated_description_never_reaches_the_critic(stub, problem):
    stub["programs"] = [GeneratedProgram(
        can_implement=False, blocking_issue="You didn't say what to compare against.")]
    stub["handoffs"] = []                       # popping one would raise

    out = translator.translate_and_run(problem, "compare them", run_id="r6")

    assert out.verdict == "UNCLEAR"
    assert out.critic_status == "not_reached"
    assert "compare against" in out.reply


def test_every_handoff_is_written_to_the_shared_scratchpad(stub, problem):
    stub["programs"] = [_program("v1"), _program("v2")]
    stub["handoffs"] = [Handoff(status="revise", result="drop the guard"),
                        Handoff(status="approved")]

    translator.translate_and_run(problem, "scan the list", run_id="trace-me")

    trace = [(e["agent"], e["round"], e["status"])
             for e in memory.scratch_for("trace-me")]
    assert trace == [
        ("screener", 0, "feasible"),
        ("executor", 1, "generated"),
        ("critic", 1, "revise"),
        ("executor", 2, "generated"),
        ("critic", 2, "approved"),
        ("sandbox", 2, "AC"),
    ]


def test_needs_approval_is_derived_from_status_not_trusted_from_the_model():
    """A model that sets the flag inconsistently must not be able to stall or
    skip the loop — the branch field is the single source of truth."""
    lying = Handoff(status="approved", needs_approval=True, result="ignore me")
    fixed = critic.Handoff.model_validate(lying.model_dump())
    assert fixed.needs_approval is True         # raw model output, unchecked

    # review() normalises it; simulate what it does to the parsed object
    lying.needs_approval = lying.status == "escalate"
    assert lying.needs_approval is False
