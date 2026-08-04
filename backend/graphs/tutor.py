"""The tutor as a graph: a model bound to three tools, cycling until it stops
asking for them.

    START -> tutor -> (tools -> tutor)* -> END

Two nodes and a conditional edge, which is the whole of a ReAct loop. It
replaces a hand-written function-calling loop that did the same thing in forty
lines; the win is not brevity, it is that `tools_condition` and `ToolNode` are
the framework's business now, and the tool results merge into state through
declared reducers instead of being appended to a local list.

What did NOT move into the framework, deliberately:

  * **the push block.** `rules.block()` and this learner's rule documents are
    composed into the system message on every invocation, before the model sees
    the question. Rules are always in force; waiting for the model to ask for
    them would be a bug, and no amount of tool-calling machinery changes that.

  * **the round cap.** `recursion_limit` bounds the cycle. A tutor that keeps
    calling tools is a tutor that never answers, and the learner is waiting.
"""
from __future__ import annotations

from langgraph.graph import END, START, StateGraph
from langgraph.prebuilt import ToolNode, tools_condition

from .. import docstore, llm, rules
from ..problem import Problem
from ..prompts import tutor_system
from ..tools.tutor_tools import TUTOR_TOOLS
from .schema import TutorState

# Tool calls per turn before we stop and answer with what we have. Three tools
# exist; a model that has called more than four times is looping, not working.
MAX_TOOL_ROUNDS = 4


def pushed_block(user_id: str) -> tuple[str, list[str]]:
    """The always-on context: markdown operating rules + this learner's rules.

    Returns (text, rule_ids) — the ids go to the run log so the monitor can
    check the agent against the rules that were actually in force at the time,
    rather than the ones in the file when it happens to be grading.
    """
    rule_docs = docstore.rules_for(user_id)
    blocks = [rules.block(), docstore.render_rules(rule_docs)]
    ids = rules.ids() + [d["id"] for d in rule_docs]
    return "\n\n".join(b for b in blocks if b), ids


def _think(state: TutorState) -> dict:
    """Answer, or ask for a tool. The model decides which."""
    model = llm.chat_model().bind_tools(TUTOR_TOOLS)
    reply = model.invoke([{"role": "system", "content": state["system"]},
                          *state["messages"]])
    return {"messages": [reply]}


def _build() -> StateGraph:
    graph = StateGraph(TutorState)
    graph.add_node("tutor", _think)
    # ToolNode reads the tool calls off the last AIMessage, runs them, and the
    # Commands they return merge facts_used / notes_seen / tools_called into
    # state on the way back.
    graph.add_node("tools", ToolNode(TUTOR_TOOLS))
    graph.add_edge(START, "tutor")
    graph.add_conditional_edges("tutor", tools_condition, {"tools": "tools", END: END})
    graph.add_edge("tools", "tutor")
    return graph


BUILDER = _build()
# No checkpointer: this graph holds a `Problem` and a pile of tool scaffolding,
# and it starts fresh every turn. The conversation that *is* checkpointed lives
# in the turn graph.
GRAPH = BUILDER.compile()


def run(problem: Problem, message: str, history: list[dict] | None = None,
        user_id: str = "guest", problem_key: str | None = None) -> TutorState:
    """One tutor turn. Returns the final state; `tutor.answer` shapes it."""
    system, rule_ids = pushed_block(user_id)
    messages = [{"role": m["role"], "content": m["content"]} for m in (history or [])]
    messages.append({"role": "user", "content": message})

    return GRAPH.invoke(
        {
            "problem": problem,
            "uid": user_id,
            "problem_key": problem_key or "",
            "message": message,
            "system": tutor_system(problem, system),
            "rules_applied": rule_ids,
            "messages": messages,
        },
        # each tool call costs two supersteps (tools, then tutor again), plus
        # the first hop in and the last hop out
        {"recursion_limit": 2 * MAX_TOOL_ROUNDS + 2},
    )
