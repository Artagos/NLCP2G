"""Intent router — the first layer of the guardrail.

A cheap Gemini call classifies each message into concept / strategy / solution /
chitchat. Only `concept` reaches the tutor freely; `strategy` is refused.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from .llm import ROUTER_MODEL, generate_structured
from .prompts import ROUTER_SYSTEM

Intent = Literal["concept", "strategy", "solution", "new_problem", "chitchat"]


class Routed(BaseModel):
    intent: Intent
    # only meaningful when intent == "new_problem"
    difficulty: Literal["easier", "harder", "same"] = "same"
    reason: str  # short justification, useful for debugging / logging


def route(message: str) -> Routed:
    return generate_structured(
        ROUTER_SYSTEM,
        [{"role": "user", "content": message}],
        Routed,
        model=ROUTER_MODEL,
    )
