"""The judge's pass over the run log.

    START ─► load ─┬─ backlog ─► grade ─┐
                   │             ▲      │  one run at a time
                   │             └──────┘
                   └─ nothing ───────────► aggregate ─► analyse ─► report ─► END

Two passes, and the loop between them is why this is a graph rather than a list
comprehension: each run is graded on its own, so a model failure on run seven
costs one verdict instead of the batch. `monitor.grade` already returns None on
failure and leaves the run in the backlog; the cycle just makes that per-item
boundary explicit.

Nothing here runs while a learner is waiting. That separation is the whole
design — a check inside the request loop is a check the loop can be tuned to
satisfy, and it costs the user latency. This one sees the reply as shipped, can
look across many runs at once, and can be as slow and as suspicious as it likes.
"""
from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from .. import memory, monitor
from .schema import MonitorState


def _load(state: MonitorState) -> dict:
    pending = memory.ungraded_runs(state.get("limit", 25))
    return {"pending": pending, "graded": 0}


def _has_backlog(state: MonitorState) -> str:
    return "grade" if state.get("pending") else "aggregate"


def _grade(state: MonitorState) -> dict:
    """One run. `monitor.grade` writes the verdict and returns None if the judge
    itself failed — in which case the run stays ungraded and comes back next
    pass, which is the right answer for a transient model error."""
    pending = list(state["pending"])
    run = pending.pop(0)
    ok = monitor.grade(run) is not None
    return {"pending": pending, "graded": state.get("graded", 0) + (1 if ok else 0)}


def _aggregate(state: MonitorState) -> dict:
    return {"judged": memory.judgments(limit=200)}


def _analyse(state: MonitorState) -> dict:
    """The second pass: look across the batch for problems no single run reveals
    — rules that contradict each other, a rule the agent keeps ignoring, a
    planted note that nearly steered a run."""
    return {"findings": monitor.analyse(state.get("judged") or [])}


def _report(state: MonitorState) -> dict:
    judged = state.get("judged") or []
    if not judged:
        return {"report_path": None}
    return {"report_path": monitor.write_report(
        judged, state.get("findings", ""), state.get("graded", 0))}


def _build() -> StateGraph:
    graph = StateGraph(MonitorState)
    graph.add_node("load", _load)
    graph.add_node("grade", _grade)
    graph.add_node("aggregate", _aggregate)
    graph.add_node("analyse", _analyse)
    graph.add_node("report", _report)

    graph.add_edge(START, "load")
    graph.add_conditional_edges("load", _has_backlog, ["grade", "aggregate"])
    graph.add_conditional_edges("grade", _has_backlog, ["grade", "aggregate"])
    graph.add_edge("aggregate", "analyse")
    graph.add_edge("analyse", "report")
    graph.add_edge("report", END)
    return graph


BUILDER = _build()
GRAPH = BUILDER.compile()


def run(limit: int = 25) -> MonitorState:
    # one superstep per run graded, plus the four fixed stages and headroom;
    # the default recursion limit of 25 would stop a full backlog mid-pass
    return GRAPH.invoke({"limit": limit}, {"recursion_limit": limit + 10})
