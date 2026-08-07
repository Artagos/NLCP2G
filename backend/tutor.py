"""Guardrailed tutor — answers general CS questions in the abstract.

The agent itself is a graph (`graphs/tutor.py`); this module is its entry point
and the shape of its answer. Keeping the two apart is what lets everything
upstream — the turn graph, the API, the run log — carry on talking to
`tutor.answer(...) -> Answer` while the orchestration underneath changed
completely.

Its context contains the current problem's statement and tags (see
`prompts.tutor_system`) so it can recognise a problem-specific question and
refuse, but never the intended solution — so there is nothing to leak. Note the
inversion against the rest of the system: the tutor sees the statement precisely
*because* it has to refuse; the blind agents downstream never do.

Context gets filled both ways, and the split is enforced by where each half
lives:

  push — `graphs.tutor.pushed_block`, composed into the system message on every
         run before the model sees the question: the markdown operating rules
         and this learner's rule documents.

  pull — `tools/tutor_tools.py`, fetched mid-run only when the request calls for
         it: `retrieve_memory` (cue-matched facts), `list_known_facts` (all of
         them, for "what do you know about me?"), `read_problem_notes` (what
         other learners wrote, arriving fenced as untrusted) and `search_corpus`
         (the shared concept notes, hybrid-retrieved and reranked).

Everything the run touched — rule ids, fact ids, note ids, corpus chunk ids,
tool names — comes back in the Answer so it can be written to the run log for
the monitor.
"""
from __future__ import annotations

from pydantic import BaseModel

from .graphs import tutor as tutor_graph
from .llm import text_of
from .problem import Problem


class Answer(BaseModel):
    reply: str
    rules_applied: list[str] = []   # rule ids pushed into this run
    facts_used: list[str] = []      # doc ids the agent pulled
    notes_seen: list[int] = []      # note ids the agent pulled
    chunks_used: list[str] = []     # corpus chunk ids the agent retrieved
    tools_called: list[str] = []


def answer(problem: Problem, message: str, history: list[dict] | None = None,
           user_id: str = "guest", problem_key: str | None = None) -> Answer:
    final = tutor_graph.run(problem, message, history, user_id, problem_key)

    # the reply is the last message the model produced without asking for a tool
    messages = final.get("messages") or []
    reply = text_of(messages[-1]) if messages else ""

    return Answer(
        reply=reply.strip(),
        rules_applied=final.get("rules_applied") or [],
        facts_used=final.get("facts_used") or [],
        notes_seen=final.get("notes_seen") or [],
        chunks_used=final.get("chunks_used") or [],
        tools_called=final.get("tools_called") or [],
    )
