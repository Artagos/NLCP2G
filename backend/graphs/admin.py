"""The operator subagent as a graph.

    START -> agent -> (tools -> agent)* -> END

Structurally the same ReAct loop as the tutor, holding a very different set of
tools: this one can rewrite the rules every learner runs under, delete another
learner's note, force the auditor and erase a learner's memory.

The difference that matters is not in this file. `admin.is_admin(chat_id)` is
checked before `run()` is ever called, so the graph is only ever constructed for
someone already on the allow-list. There is no node here that decides who is an
administrator, and there is deliberately no tool that could grant it.

`force_tick` is not here either. It is async, and firing a real trigger at real
learners is something an operator should do explicitly — it stays the
deterministic `/tick` command rather than something a model may try mid-sentence.
"""
from __future__ import annotations

from langgraph.graph import END, START, StateGraph
from langgraph.prebuilt import ToolNode, tools_condition

from .. import llm
from ..prompts import ADMIN_SYSTEM
from ..tools.admin_tools import ADMIN_TOOLS
from .schema import AdminState

# Eleven tools, several of them irreversible. A model still asking for more
# after six calls is not converging on an answer.
MAX_TOOL_ROUNDS = 6


def _think(state: AdminState) -> dict:
    model = llm.chat_model().bind_tools(ADMIN_TOOLS)
    reply = model.invoke([{"role": "system", "content": ADMIN_SYSTEM},
                          *state["messages"]])
    return {"messages": [reply]}


def _build() -> StateGraph:
    graph = StateGraph(AdminState)
    graph.add_node("agent", _think)
    graph.add_node("tools", ToolNode(ADMIN_TOOLS))
    graph.add_edge(START, "agent")
    graph.add_conditional_edges("agent", tools_condition, {"tools": "tools", END: END})
    graph.add_edge("tools", "agent")
    return graph


BUILDER = _build()
GRAPH = BUILDER.compile()


def run(actor: str, message: str) -> AdminState:
    """One operator turn. Callers must have checked `admin.is_admin` first."""
    return GRAPH.invoke(
        {"actor": actor, "message": message,
         "messages": [{"role": "user", "content": message}]},
        {"recursion_limit": 2 * MAX_TOOL_ROUNDS + 2},
    )
