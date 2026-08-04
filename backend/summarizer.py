"""Per-problem recap generator.

Summarizes a learner's activity on one problem — the approaches they tried and
the verdicts, plus any concept questions — into a short recap. Used both when
switching problems (auto-summary of the one being left) and on an explicit
"summarize" command.

Guardrail: the summary recaps what the learner *did*; it never gives hints, a
solution, or advice on what approach to use (see prompts.SUMMARIZER_SYSTEM).
"""
from __future__ import annotations

from . import rules
from .llm import generate
from .problem import Problem
from .prompts import SUMMARIZER_SYSTEM, with_pushed


def narrate(digest: str) -> str:
    """Phrase the digest conversationally. The digest is the only input.

    The operating rules are pushed here too — R1 (no hints) has to hold in a
    recap of an unsolved problem just as much as in a live answer.
    """
    return generate(with_pushed(SUMMARIZER_SYSTEM, rules.block()),
                    [{"role": "user", "content": digest}])


def summarize(problem: Problem, attempts: list[dict], questions: list[str]) -> str:
    """Recap one learner's activity on one problem.

    Assembling the facts and phrasing them are two nodes of a graph
    (`graphs/summarize.py`), so that the model only ever sees the digest.
    """
    # imported here rather than at module scope: the graph's narrate node calls
    # back into this module, so eager import is a cycle
    from .graphs import summarize as summary_graph

    return summary_graph.run(problem, attempts, questions)["recap"]
