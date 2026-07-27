"""Deciding what is worth remembering.

The agent writes its own memory, so something has to decide *when*. That
decision is the interesting part: a system that saves everything drowns its own
retrieval, and one that saves nothing re-asks the learner the same question
every session. So this runs after a turn, looks at what was actually said, and
usually saves nothing.

It produces two kinds of document (both land in docstore.py):

  fact — durable, saved WITH A CUE, retrieved later when the cue matches. When
         it surfaces, the model decides what to do with it.
  rule — a standing change in behaviour, attached on every run for its owner.
         The model doesn't decide what to do; the rule says. It only decides
         whether the rule applies here.
"""
from __future__ import annotations

import logging
from typing import Literal

from pydantic import BaseModel

from . import docstore
from .llm import generate_structured
from .prompts import REFLECT_SYSTEM

log = logging.getLogger("cp_tutor.reflect")

# Nothing durable is ever learned from these — they're mechanical commands.
_SKIP_INTENTS = {"new_problem", "summarize", "error"}


class MemoryDecision(BaseModel):
    save: bool
    kind: Literal["fact", "rule"] = "fact"
    # the memory, in the agent's own words, third person
    text: str = ""
    # facts only: the words a FUTURE request would contain
    cue_keywords: list[str] = []
    cue_note: str = ""
    # why this was or wasn't worth saving — kept for the monitor
    reason: str = ""


def consider(user_id: str, user_message: str, assistant_reply: str,
             intent: str, problem_key: str | None = None) -> dict | None:
    """Look at one exchange; save a document if it earned it. Returns it, or None."""
    if intent in _SKIP_INTENTS or not user_message.strip():
        return None

    try:
        decision = generate_structured(
            REFLECT_SYSTEM,
            [{
                "role": "user",
                "content": (
                    f"Intent of this turn: {intent}\n\n"
                    f"--- learner said ---\n{user_message}\n\n"
                    f"--- agent replied ---\n{assistant_reply[:1500]}\n\n"
                    "Is there anything here worth remembering long-term? Most of "
                    "the time the answer is no."
                ),
            }],
            MemoryDecision,
        )
    except Exception as exc:            # memory is best-effort; never break a turn
        log.warning("reflection failed: %s", exc)
        return None

    if not decision.save or not decision.text.strip():
        return None

    doc = docstore.save(
        doc_type=decision.kind,
        text=decision.text,
        user_id=user_id,
        scope="private",                # everything the agent infers is private
        cue_keywords=decision.cue_keywords if decision.kind == "fact" else [],
        cue_note=decision.cue_note if decision.kind == "fact" else "",
        source=f"chat:{intent}",
        problem_key=problem_key,
    )
    log.info("saved %s for %s: %s", decision.kind, user_id, decision.text)
    return doc
