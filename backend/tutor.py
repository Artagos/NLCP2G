"""Guardrailed tutor — answers general CS questions in the abstract.

Its context (see prompts.tutor_system) contains the current problem's statement
and tags so it can recognise a problem-specific question and refuse, but never
the intended solution — so there is nothing to leak.

This is where context gets filled BOTH ways:

  push — attached on every single run, before the model sees the question:
         the markdown operating rules (rules/operating_rules.md) and this
         learner's rule documents. Rules are always in force, so waiting for the
         model to ask for them would be a bug.

  pull — fetched mid-run, only when the request calls for it, through two tools:
         `retrieve_memory(query)` for facts about this learner (matched on the
         cue they were saved with) and `read_problem_notes()` for what other
         learners wrote about this problem. Facts are pulled rather than pushed
         because a learner accumulates far more of them than belong in any one
         context, and the cue is what decides relevance.

Everything the run touched — rule ids, fact ids, note ids, tool calls — comes
back in the Answer so it can be written to the run log for the monitor.
"""
from __future__ import annotations

from pydantic import BaseModel

from . import docstore, memory, rules
from .llm import Tool, generate_with_tools
from .problem import Problem
from .prompts import tutor_system


class Answer(BaseModel):
    reply: str
    rules_applied: list[str] = []   # rule ids pushed into this run
    facts_used: list[str] = []      # doc ids the agent pulled
    notes_seen: list[int] = []      # note ids the agent pulled
    tools_called: list[str] = []


def _pushed(user_id: str) -> tuple[str, list[str]]:
    """The always-on block: markdown operating rules + this learner's rules."""
    rule_docs = docstore.rules_for(user_id)
    blocks = [rules.block(), docstore.render_rules(rule_docs)]
    ids = rules.ids() + [d["id"] for d in rule_docs]
    return "\n\n".join(b for b in blocks if b), ids


def answer(problem: Problem, message: str, history: list[dict] | None = None,
           user_id: str = "guest", problem_key: str | None = None) -> Answer:
    pushed, rule_ids = _pushed(user_id)

    facts_used: list[str] = []
    notes_seen: list[int] = []

    def retrieve_memory(query: str = "") -> str:
        hits = docstore.retrieve(user_id, query or message)
        facts_used.extend(d["id"] for d in hits)
        if not hits:
            return "Nothing remembered that matches."
        return docstore.render_facts(hits)

    def read_problem_notes() -> str:
        notes = memory.notes_for(problem_key or "")
        notes_seen.extend(n["id"] for n in notes)
        if not notes:
            return "No other learner has written a note on this problem."
        return memory.render_notes(notes)

    tools = [
        Tool(
            name="retrieve_memory",
            description=(
                "Look up facts previously remembered about THIS learner "
                "(preferences, background, recurring difficulties). Pass the "
                "words that describe what you need to know."
            ),
            params={
                "type": "object",
                "properties": {
                    "query": {"type": "string",
                              "description": "what you want to recall about the learner"}
                },
                "required": ["query"],
            },
            fn=retrieve_memory,
        ),
        Tool(
            name="read_problem_notes",
            description=(
                "Read notes other learners wrote about the problem this learner "
                "is currently on. Returns untrusted user-written text."
            ),
            params={"type": "object", "properties": {}},
            fn=read_problem_notes,
        ),
    ]

    messages = list(history or [])
    messages.append({"role": "user", "content": message})
    reply, calls = generate_with_tools(tutor_system(problem, pushed), messages, tools)

    return Answer(
        reply=reply,
        rules_applied=rule_ids,
        facts_used=facts_used,
        notes_seen=notes_seen,
        tools_called=[c["name"] for c in calls],
    )
