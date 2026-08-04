"""Deciding whether to speak when nobody asked.

    START ─► gather ─► decide ─┬─ fire ────► END
                               └─ silent ──► END

One learner per invocation; `scheduler.sweep` runs it across every linked
learner on each heartbeat.

The branch is the point of this graph. Most passes end at `silent`, and that is
the design working: `triggers.decide` returns `Silence(reason)` for eight
distinct reasons, two of which come from the product's promise rather than from
politeness — `awaiting_learner` (the agent already asked a question, so a nudge
either repeats it or supplies the missing step) and `would_hint` (after two
timeouts, an unprompted message can only read as *your approach is too slow*,
which rule R1 forbids).

`decide` is a pure function with an injected clock, and it stays one. Nothing
here reads the time; `scheduler --now 2026-07-29T03:00` is enough to put the
whole system at 3am and watch the quiet-hours branch fire. Making that decision
a node would have meant a graph that cannot be tested without mocking a clock.

The nudge text is templated, never model-written. This graph never calls a model
at all — it is the one agent-shaped thing in the system whose output no LLM
touches, because it is the one message nobody asked for.
"""
from __future__ import annotations

import logging

from langgraph.graph import END, START, StateGraph

from .. import memory, scheduler, triggers
from ..triggers import Fire
from .schema import NudgeState

log = logging.getLogger("cp_tutor.graphs.sweep")


def _gather(state: NudgeState) -> dict:
    """Build this learner's state from the stores. No writes, no problem loading."""
    return {"learner": scheduler.gather(state["link"])}


def _decide(state: NudgeState) -> dict:
    return {"decision": triggers.decide(state["learner"], state["now"])}


def _route(state: NudgeState) -> str:
    return "fire" if isinstance(state["decision"], Fire) else "silent"


async def _fire(state: NudgeState) -> dict:
    """Send it — unless this is a dry run, which decides and records but stays
    quiet. A delivery failure is recorded as a failure, not as a silence: those
    are different things and conflating them would hide an outage behind a
    plausible-looking decision."""
    learner, decision = state["learner"], state["decision"]
    if not state.get("dry_run"):
        try:
            await state["channel"].send(learner.chat_id, decision.text)
        except Exception as exc:
            log.warning("nudge delivery to %s failed: %s", learner.chat_id, exc)
            memory.record_nudge(learner.user_id, learner.chat_id, "failed",
                                "delivery_error", decision.kind,
                                learner.problem_key, str(exc)[:300])
            return {}
    memory.record_nudge(learner.user_id, learner.chat_id,
                        "dry-run" if state.get("dry_run") else "fired", "",
                        decision.kind, learner.problem_key, decision.text[:500])
    return {}


def _silent(state: NudgeState) -> dict:
    """Record the silence and why. An unexplained non-event is not evidence of
    anything; this is what makes the quiet passes auditable."""
    learner, decision = state["learner"], state["decision"]
    memory.record_nudge(learner.user_id, learner.chat_id, "silent",
                        decision.reason, "", learner.problem_key, decision.detail)
    return {}


def _build() -> StateGraph:
    graph = StateGraph(NudgeState)
    graph.add_node("gather", _gather)
    graph.add_node("decide", _decide)
    graph.add_node("fire", _fire)
    graph.add_node("silent", _silent)

    graph.add_edge(START, "gather")
    graph.add_edge("gather", "decide")
    graph.add_conditional_edges("decide", _route, ["fire", "silent"])
    graph.add_edge("fire", END)
    graph.add_edge("silent", END)
    return graph


BUILDER = _build()
GRAPH = BUILDER.compile()


async def run(link: dict, channel, now: float, dry_run: bool) -> NudgeState:
    return await GRAPH.ainvoke({"link": link, "channel": channel, "now": now,
                                "dry_run": dry_run})
