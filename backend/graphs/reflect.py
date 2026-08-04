"""Deciding what is worth remembering.

    START ─┬─ mechanical ─────────────────► END        (the model is never asked)
           └─ worth a look ─► assess ─┬─ save ─► persist ─► END
                                      └─ no ───────────► END

Small, and the shape carries the argument. The skip is an *edge out of START*,
not a guard clause inside the node: "give me another problem" and "summarize"
are mechanical commands from which nothing durable is ever learned, and they
should not reach a model at all. Writing that as an edge means the graph says so
rather than a comment saying so.

The other branch is the one that matters. Most turns end at `no`, and that is
the harder half of this feature: a system that saves everything drowns its own
retrieval, and one that saves nothing re-asks the same question every session.
"""
from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from .. import reflect
from .schema import ReflectState

# Nothing durable is ever learned from these — they're mechanical commands.
SKIP_INTENTS = {"new_problem", "summarize", "error"}


def _worth_asking(state: ReflectState) -> str:
    if state.get("intent") in SKIP_INTENTS:
        return END
    return "assess" if (state.get("user_message") or "").strip() else END


def _assess(state: ReflectState) -> dict:
    """Ask the model whether anything here is worth keeping. Best-effort: a
    reflection that fails must never break the turn it is reflecting on."""
    decision = reflect.decide(state["user_message"], state["assistant_reply"],
                              state["intent"])
    if decision is None:
        return {"save": False}
    return {"save": decision.save, "kind": decision.kind, "text": decision.text,
            "cue_keywords": decision.cue_keywords, "cue_note": decision.cue_note,
            "reason": decision.reason}


def _decided(state: ReflectState) -> str:
    return "persist" if state.get("save") and state.get("text", "").strip() else END


def _persist(state: ReflectState) -> dict:
    return {"saved": reflect.persist(
        user_id=state["user_id"], kind=state["kind"], text=state["text"],
        cue_keywords=state.get("cue_keywords") or [],
        cue_note=state.get("cue_note", ""), intent=state["intent"],
        problem_key=state.get("problem_key"))}


def _build() -> StateGraph:
    graph = StateGraph(ReflectState)
    graph.add_node("assess", _assess)
    graph.add_node("persist", _persist)

    graph.add_conditional_edges(START, _worth_asking, ["assess", END])
    graph.add_conditional_edges("assess", _decided, ["persist", END])
    graph.add_edge("persist", END)
    return graph


BUILDER = _build()
GRAPH = BUILDER.compile()


def run(user_id: str, user_message: str, assistant_reply: str, intent: str,
        problem_key: str | None = None) -> ReflectState:
    return GRAPH.invoke({
        "user_id": user_id, "user_message": user_message,
        "assistant_reply": assistant_reply, "intent": intent,
        "problem_key": problem_key, "save": False, "saved": None,
    })
