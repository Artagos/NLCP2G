"""The blind pipeline, as a graph with a real cycle.

                     ┌──────────────── revise, budget left ──────────┐
                     ▼                                               │
    START ─► screen ─┬─ feasible ─► execute ─┬─ built ─► critique ───┤
                     │                       └─ gated ─► stop_gated  ├─ approved ─► ready ─► END
                     └─ infeasible ─► stop_infeasible ─► END         └─ escalate ─► ask ─► END
                                                                     └─ budget out ─► exhausted ─► END

Four ways out, and only one of them runs the learner's code. That asymmetry is
the product: the system promises to build *exactly* what someone described, and
three of these exits exist because it could not.

Why this graph is compiled without a checkpointer, and holds a whole `Problem`
in its state: it starts fresh on every attempt and nothing about a half-built
program is worth resuming. The conversation is what resumes, and that lives in
`turn.py`.

Blindness is not enforced here. `prompts.py` renders only the raw I/O format for
the screener, the executor and the critic — never the statement — so a node
holding the `Problem` object still cannot leak it. Keeping that guarantee in the
prompt layer is what let the orchestration be rewritten underneath it without
re-auditing what each agent can see.

Every node writes to the shared scratchpad (`memory.scratch`) under the run id,
so the whole negotiation replays afterwards for the monitor. The order of those
writes is asserted in `tests/test_critic_loop.py` and is part of the contract.
"""
from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from .. import critic, memory, screener, translator
from .schema import SolutionState


def _scratch(state: SolutionState, agent: str, round_: int, status: str,
             payload: dict) -> None:
    """Both agents write here. No-ops when there's no run id (unit tests)."""
    run_id = state.get("run_id")
    if run_id:
        memory.scratch(run_id, agent, round_, status, payload)


# ------------------------------------------------------------------ the nodes
# Each calls its agent through the *module*, never through a name imported at
# the top of this file. `screener.screen(...)`, not `from ..screener import
# screen`. The suite stubs these agents by setting module attributes, and a
# direct import would bind the real function at import time and silently
# detach every one of those stubs.

def _screen(state: SolutionState) -> dict:
    """Is what they described even possible to carry out? Runs before any code
    is written, and must not reject a solution merely for being slow — a naive
    brute force is feasible, and that is the teaching point."""
    verdict = screener.screen(state["problem"], state["described_approach"])
    _scratch(state, "screener", 0,
             "feasible" if verdict.feasible else "infeasible", {"issue": verdict.issue})
    return {"feasible": verdict.feasible, "issue": verdict.issue}


def _execute(state: SolutionState) -> dict:
    """Turn the description into C++ — or gate, if it cannot be done as stated.

    On a rebuild the critic's fix list arrives as `revision`, but the learner's
    original words remain the source of truth.
    """
    rounds = state.get("rounds", 0) + 1
    program = translator._generate_cpp(
        state["problem"], state["described_approach"], state.get("revision", ""))
    _scratch(state, "executor", rounds,
             "generated" if program.can_implement else "gated",
             {"approach_summary": program.approach_summary,
              "blocking_issue": program.blocking_issue,
              "source_len": len(program.cpp_source)})
    return {
        "rounds": rounds,
        "can_implement": program.can_implement,
        "cpp_source": program.cpp_source,
        "approach_summary": program.approach_summary,
        "blocking_issue": program.blocking_issue,
    }


def _critique(state: SolutionState) -> dict:
    """Does the program do what the learner's words say — no more, no less?

    The critic cannot judge correctness, because it does not know the problem.
    That is exactly why it can judge fidelity.
    """
    handoff = critic.review(state["problem"], state["described_approach"],
                            state["cpp_source"], state["approach_summary"])
    _scratch(state, "critic", state["rounds"], handoff.status,
             {"result": handoff.result, "confidence": handoff.confidence,
              "violations": [v.model_dump() for v in handoff.violations]})
    return {
        "handoff": handoff,
        "critic_status": handoff.status,
        "needs_approval": handoff.needs_approval,
        # every violation raised across all rounds, including ones later fixed:
        # a caught-and-corrected C1 is exactly what the monitor needs to see
        "violations": [f"{v.rule}: {v.why}".strip(": ") for v in handoff.violations],
    }


# ------------------------------------------------------------- the four exits

def _stop_infeasible(state: SolutionState) -> dict:
    return {
        "ready": False, "verdict": "INFEASIBLE", "cpp_source": "",
        "approach_summary": "",
        "reply": translator._infeasible_reply(
            state.get("issue")
            or "The described solution can't be carried out as stated."),
    }


def _stop_gated(state: SolutionState) -> dict:
    return {
        "ready": False, "verdict": "UNCLEAR", "critic_status": "not_reached",
        "cpp_source": "", "approach_summary": "",
        "reply": translator._gate_reply(
            state.get("blocking_issue")
            or "The described steps were too ambiguous to implement."),
    }


def _exhausted(state: SolutionState) -> dict:
    """Budget spent and still not faithful: ask, rather than loop or ship it.

    Shipping would hand the learner a program containing steps they never
    described and call it theirs; looping would spend their afternoon. Asking
    is the only honest third option.
    """
    handoff = state["handoff"]
    asked = critic.Handoff(
        status="escalate", needs_approval=True, confidence=handoff.confidence,
        result=("After two attempts I still couldn't produce a program that "
                "matches your description without adding steps of my own. The "
                "part I keep having to invent is:\n\n"
                + critic.fix_list(handoff)
                + "\n\nCould you spell that part out?"))
    _scratch(state, "critic", state["rounds"], "escalate",
             {"reason": "round budget exhausted"})
    return {"handoff": asked, "critic_status": "escalate", "needs_approval": True}


def _ask(state: SolutionState) -> dict:
    """Escalation: stop before the sandbox and put the question to the learner."""
    handoff = state["handoff"]
    return {
        "ready": False, "verdict": "NEEDS_CLARIFICATION", "critic_status": "escalate",
        "reply": translator._escalation_reply(
            handoff.result or "Part of the approach wasn't specified."),
    }


def _ready(state: SolutionState) -> dict:
    """Approved and faithful. Nothing has been executed yet — the caller decides
    whether to run it here and now, or hand it to a worker."""
    return {"ready": True, "critic_status": state["critic_status"]}


# ----------------------------------------------------------------- the wiring

def _after_screen(state: SolutionState) -> str:
    return "execute" if state.get("feasible") else "stop_infeasible"


def _after_execute(state: SolutionState) -> str:
    return "critique" if state.get("can_implement") else "stop_gated"


def _after_critique(state: SolutionState) -> str:
    """The branch reads the handoff's *fields*, never its prose.

    `needs_approval` is derived from `status` inside `critic.review`, so a model
    that sets the flag inconsistently cannot stall the loop or skip it.
    """
    if state.get("critic_status") == "approved":
        return "ready"
    if state.get("needs_approval"):
        return "ask"
    if state.get("rounds", 0) >= critic.MAX_ROUNDS:
        return "exhausted"
    return "execute"


def _revision_note(state: SolutionState) -> dict:
    """Hand the critic's findings back to the executor as an imperative list."""
    return {"revision": translator.executor_revision_note(
        critic.fix_list(state["handoff"]), state["rounds"] + 1)}


def _build() -> StateGraph:
    graph = StateGraph(SolutionState)
    graph.add_node("screen", _screen)
    graph.add_node("execute", _execute)
    graph.add_node("critique", _critique)
    graph.add_node("revise", _revision_note)
    graph.add_node("ready", _ready)
    graph.add_node("ask", _ask)
    graph.add_node("exhausted", _exhausted)
    graph.add_node("stop_infeasible", _stop_infeasible)
    graph.add_node("stop_gated", _stop_gated)

    graph.add_edge(START, "screen")
    graph.add_conditional_edges("screen", _after_screen,
                                ["execute", "stop_infeasible"])
    graph.add_conditional_edges("execute", _after_execute,
                                ["critique", "stop_gated"])
    graph.add_conditional_edges(
        "critique",
        # the one cycle in this system: revise routes back through `revise`,
        # which is what carries the critic's fix list into the next build
        lambda s: "revise" if _after_critique(s) == "execute" else _after_critique(s),
        ["revise", "ready", "ask", "exhausted"])
    graph.add_edge("revise", "execute")
    graph.add_edge("exhausted", "ask")

    for terminal in ("ready", "ask", "stop_infeasible", "stop_gated"):
        graph.add_edge(terminal, END)
    return graph


BUILDER = _build()
GRAPH = BUILDER.compile()

# MAX_ROUNDS trips through screen -> (execute -> critique -> revise) * N -> exit.
# The limit is a backstop against a mis-wired edge, not the round budget itself;
# that is enforced by `_after_critique`.
RECURSION_LIMIT = 4 * critic.MAX_ROUNDS + 6


def run(problem, described_approach: str, run_id: str | None = None) -> SolutionState:
    return GRAPH.invoke(
        {"problem": problem, "described_approach": described_approach,
         "run_id": run_id, "rounds": 0, "revision": ""},
        {"recursion_limit": RECURSION_LIMIT},
    )
