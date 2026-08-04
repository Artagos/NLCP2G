"""The per-problem recap.

    START ─► digest ─► narrate ─► END

Two nodes, and the split between them is the guardrail. `digest` assembles the
facts deterministically — which approaches were tried, in what order, what
verdict each got, what concept questions were asked. `narrate` is the only step
that calls a model, and the only thing it is given is that digest.

So the recap can restate what the learner did and cannot invent what they should
have done. Rule R1 (no hints) has to hold in a recap of an *unsolved* problem
just as much as in a live answer, and the moment where that could go wrong is a
model with the problem in front of it and a sympathetic instinct.
"""
from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from .. import summarizer
from .schema import SummaryState


def _digest(state: SummaryState) -> dict:
    """The factual half: no model, no judgement, just what happened."""
    problem = state["problem"]
    attempts = state.get("attempts") or []
    questions = state.get("questions") or []

    solved = any(a["verdict"] == "AC" for a in attempts)
    lines = [
        f"Problem: {problem.name}"
        + (f" (rating {problem.rating})" if problem.rating else ""),
        f"Solved: {'yes' if solved else 'no'}",
    ]
    if attempts:
        lines.append("Approaches tried (in order):")
        for i, a in enumerate(attempts, 1):
            lines.append(f"  {i}. [{a['verdict']}] {a['approach']}")
    else:
        lines.append("Approaches tried: none")
    if questions:
        lines.append("Concept questions they asked:")
        lines.extend(f"  - {q}" for q in questions)
    return {"digest": "\n".join(lines)}


def _narrate(state: SummaryState) -> dict:
    return {"recap": summarizer.narrate(state["digest"])}


def _build() -> StateGraph:
    graph = StateGraph(SummaryState)
    graph.add_node("digest", _digest)
    graph.add_node("narrate", _narrate)
    graph.add_edge(START, "digest")
    graph.add_edge("digest", "narrate")
    graph.add_edge("narrate", END)
    return graph


BUILDER = _build()
GRAPH = BUILDER.compile()


def run(problem, attempts: list[dict], questions: list[str]) -> SummaryState:
    return GRAPH.invoke({"problem": problem, "attempts": attempts,
                         "questions": questions})
