"""Translator (pure executor): NL approach -> C++ -> sandbox -> faithful verdict.

The codegen step is deliberately BLIND to the problem: it sees only the raw I/O
format and the learner's described algorithm (see prompts.translator_codegen_system).
It cannot infer the intended solution, so it can only implement what was said.

Steps:
  1. The LLM turns the described approach into C++ — or GATES (can_implement=false)
     with specific feedback if the description isn't implementable as stated.
  2. The fidelity critic (critic.py) checks the code against the learner's words
     and returns a Handoff. Approved code runs; a `revise` sends the Executor
     back once; an `escalate` stops and asks the learner.
  3. The sandbox compiles & runs it against the test suite.
  4. The LLM phrases the verdict facts conversationally, under strict no-hints rules.

Steps 1–2 and their cycle are a compiled graph — `graphs/solution.py` — and this
module is the seam around it: `build_program` runs the graph and hands back a
`BuildResult`, `finish_run` turns a completed sandbox run into a reply. The
split is what lets the two callers differ: the web UI runs the program inline,
while a chat channel hands it to a worker and reports back by webhook.

Step 4 only ever sees the deterministic verdict facts we hand it — never a
license to invent strategy advice.
"""
from __future__ import annotations

from pydantic import BaseModel

from . import memory, rules
from .llm import generate, generate_structured
from .problem import Problem
from .prompts import (executor_revision_note, translator_codegen_system,
                      with_pushed, VERDICT_EXPLAINER_SYSTEM)
from .sandbox import RunResult, run_cpp


class GeneratedProgram(BaseModel):
    # False when the description can't be faithfully turned into code — the
    # translator must gate rather than guess an algorithmic step.
    can_implement: bool
    # populated only when can_implement is True
    cpp_source: str = ""
    approach_summary: str = ""   # one-line restatement of what was implemented
    # populated only when can_implement is False — specifically what could not
    # be turned into code and what the learner must clarify
    blocking_issue: str = ""


def _generate_cpp(problem: Problem, described_approach: str,
                  revision: str = "") -> GeneratedProgram:
    """Build the program. On a rebuild, `revision` is the critic's fix list —
    the learner's original words are still the source of truth."""
    ask = (
        "Here is the approach I want you to implement, exactly as "
        f"described:\n\n{described_approach}"
    )
    if revision:
        ask = f"{revision}\n\n--- the learner's original description ---\n{described_approach}"
    return generate_structured(
        translator_codegen_system(problem),
        [{"role": "user", "content": ask}],
        GeneratedProgram,
    )


def _verdict_facts(approach_summary: str, run: RunResult) -> str:
    """Deterministic, factual summary of what happened — the ONLY thing the
    verdict-explainer LLM is allowed to work from."""
    lines = [f"Approach we implemented: {approach_summary}"]

    if not run.ok:
        lines.append("Outcome: COMPILE ERROR")
        lines.append(f"Compiler output:\n{run.compile_error}")
        return "\n".join(lines)

    if run.all_accepted:
        slowest = max(run.results, key=lambda r: r["time_ms"])
        n = len(run.results)
        lines.append(f"Outcome: ACCEPTED — passed all {n} test{'' if n == 1 else 's'}.")
        # How many tests there were is part of the verdict, not trivia. A scraped
        # Codeforces problem ships only its published samples — sometimes exactly
        # one — while a bank problem carries six or seven including generated
        # stress cases. Saying "passed every test" for both told the learner the
        # same thing about wildly different amounts of evidence, and "every test"
        # invites the strongest possible reading of the weakest possible check.
        if n < 3:
            lines.append(
                f"Only {n} test{' was' if n == 1 else 's were'} available for this "
                "problem, so passing is real but weak evidence: it does not show "
                "the approach is correct in general, and it cannot show whether it "
                "is fast enough on a large input."
            )
        lines.append(
            f"Slowest test '{slowest['name']}': {slowest['time_ms']} ms "
            f"(limit {slowest['time_limit_ms']} ms)."
        )
        return "\n".join(lines)

    fail = run.first_failure()
    lines.append(f"Outcome: {fail['verdict']} on test '{fail['name']}'.")
    if fail["verdict"] == "TLE":
        lines.append(
            f"It exceeded the time limit: ran for at least {fail['time_ms']} ms, "
            f"limit is {fail['time_limit_ms']} ms."
        )
    elif fail["verdict"] == "WA":
        lines.append(f"Input (preview): {fail.get('input_preview', '')}")
        lines.append(f"Expected output: {fail.get('expected', '')}")
        lines.append(f"Your program's output: {fail.get('got', '')}")
    elif fail["verdict"] == "RE":
        lines.append(
            f"Runtime error (exit code {fail.get('return_code')}). "
            f"stderr: {fail.get('stderr', '')}"
        )
    # how many passed before the failure, for encouragement
    passed = sum(1 for r in run.results if r["verdict"] == "AC")
    lines.append(f"({passed} of {len(run.results)} tests passed.)")
    return "\n".join(lines)


def _explain(facts: str) -> str:
    # the markdown operating rules are pushed here too — R6 (report results in
    # full) and R12 (don't quote the source) are enforced from the rules file
    return generate(with_pushed(VERDICT_EXPLAINER_SYSTEM, rules.block()),
                    [{"role": "user", "content": facts}])


class TranslationOutcome(BaseModel):
    reply: str                 # conversational message shown to the user
    cpp_source: str            # kept server-side; surfaced only if asked
    verdict: str               # AC | WA | TLE | RE | CE | UNCLEAR | ...
    approach_summary: str
    # what the critic did, for the trace and the monitor
    critic_status: str = ""
    critic_rounds: int = 0
    violations: list[str] = []


def _gate_reply(issue: str) -> str:
    return (
        "I couldn't turn that into a working program yet — I only build exactly "
        "what you describe, and something in the description wasn't complete "
        "enough to translate into code. Here's what I couldn't resolve:\n\n"
        f"{issue}\n\n"
        "Fill in those details and I'll build it and run it."
    )


def _infeasible_reply(issue: str) -> str:
    return (
        "Before building anything, I checked whether what you described can "
        "actually be carried out — and as written, it can't:\n\n"
        f"{issue}\n\n"
        "Describe a concrete step-by-step procedure over the given inputs and "
        "I'll build it and run it."
    )


def _escalation_reply(question: str) -> str:
    return (
        "I got as far as building your program, but a fidelity check stopped me "
        "before running it — I couldn't write it without deciding something you "
        "didn't specify, and guessing on your behalf would make it my solution "
        "rather than yours. Here's what I need from you:\n\n"
        f"{question}\n\n"
        "Tell me that and I'll build it exactly as you say."
    )


def _scratch(run_id: str | None, agent: str, round_: int, status: str, payload: dict) -> None:
    """Both agents write here. No-ops when there's no run id (unit tests)."""
    if run_id:
        memory.scratch(run_id, agent, round_, status, payload)


class BuildResult(BaseModel):
    """What came out of the blind pipeline, before anything was executed.

    Split out from `translate_and_run` so the sandbox step can happen somewhere
    else: over a chat channel the run is handed to an out-of-process worker and
    the verdict arrives later through the run-complete webhook. `ready` False
    means the pipeline already has its final answer (infeasible, unclear, or the
    critic escalated) and nothing should be executed at all.
    """

    ready: bool
    outcome: TranslationOutcome | None = None    # set when ready is False
    cpp_source: str = ""
    approach_summary: str = ""
    critic_status: str = ""
    critic_rounds: int = 0
    violations: list[str] = []


def finish_run(approach_summary: str, cpp_source: str, run: RunResult,
               critic_status: str = "", critic_rounds: int = 0,
               violations: list[str] | None = None,
               run_id: str | None = None) -> TranslationOutcome:
    """Turn a completed sandbox run into the reply the learner sees."""
    violations = violations or []

    if run.infra_error:
        return TranslationOutcome(
            reply=(
                "I built your program, but couldn't run it just now — the "
                "sandbox that executes code isn't available at the moment. "
                "This isn't a problem with your solution. Please try again in a bit."
            ),
            cpp_source=cpp_source, verdict="SANDBOX_UNAVAILABLE",
            approach_summary=approach_summary, critic_status=critic_status,
            critic_rounds=critic_rounds, violations=violations,
        )

    facts = _verdict_facts(approach_summary, run)
    reply = _explain(facts)

    if not run.ok:
        verdict = "CE"
    elif run.all_accepted:
        verdict = "AC"
    else:
        verdict = run.first_failure()["verdict"]

    _scratch(run_id, "sandbox", critic_rounds or 1, verdict,
             {"tests": len(run.results), "compile_ok": run.ok})

    return TranslationOutcome(
        reply=reply, cpp_source=cpp_source, verdict=verdict,
        approach_summary=approach_summary, critic_status=critic_status,
        critic_rounds=critic_rounds, violations=violations,
    )


def build_program(problem: Problem, described_approach: str,
                  run_id: str | None = None) -> BuildResult:
    """Run the blind pipeline and report what came out of it.

    The screener, the executor⇄critic cycle and the round budget are a compiled
    graph (`graphs/solution.py`). This function is its entry point: it exists so
    that everything upstream — the turn graph, the deferred chat path, the tests
    — still deals in a `BuildResult` and does not need to know there is a graph
    down there at all.
    """
    # imported here rather than at module scope: the graph's nodes call back
    # into this module (`translator._generate_cpp`, the reply builders), and
    # importing it eagerly would be a cycle
    from .graphs import solution as solution_graph

    final = solution_graph.run(problem, described_approach, run_id)
    violations = final.get("violations") or []
    rounds = final.get("rounds", 0)
    status = final.get("critic_status", "")

    if final.get("ready"):
        return BuildResult(
            ready=True,
            cpp_source=final.get("cpp_source", ""),
            approach_summary=final.get("approach_summary", ""),
            critic_status=status,
            critic_rounds=rounds,
            violations=violations,
        )

    # One of the three exits that never runs anything: infeasible, unclear, or
    # the critic asking the learner a question.
    return BuildResult(
        ready=False,
        critic_status=status,
        critic_rounds=rounds,
        violations=violations,
        outcome=TranslationOutcome(
            reply=final.get("reply", ""),
            cpp_source=final.get("cpp_source", ""),
            verdict=final.get("verdict", ""),
            approach_summary=final.get("approach_summary", ""),
            critic_status=status,
            critic_rounds=rounds,
            violations=violations,
        ),
    )


def translate_and_run(problem: Problem, described_approach: str,
                      run_id: str | None = None) -> TranslationOutcome:
    """The synchronous path (web UI): build, run, and report in one call."""
    built = build_program(problem, described_approach, run_id=run_id)
    if not built.ready:
        return built.outcome
    run = run_cpp(problem, built.cpp_source)
    return finish_run(built.approach_summary, built.cpp_source, run,
                      critic_status=built.critic_status,
                      critic_rounds=built.critic_rounds,
                      violations=built.violations, run_id=run_id)
