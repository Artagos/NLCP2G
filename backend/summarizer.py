"""Per-problem recap generator.

Summarizes a learner's activity on one problem — the approaches they tried and
the verdicts, plus any concept questions — into a short recap. Used both when
switching problems (auto-summary of the one being left) and on an explicit
"summarize" command.

Guardrail: the summary recaps what the learner *did*; it never gives hints, a
solution, or advice on what approach to use (see prompts.SUMMARIZER_SYSTEM).
"""
from __future__ import annotations

from .llm import generate
from .problem import Problem
from .prompts import SUMMARIZER_SYSTEM


def summarize(problem: Problem, attempts: list[dict], questions: list[str]) -> str:
    solved = any(a["verdict"] == "AC" for a in attempts)
    lines = [
        f"Problem: {problem.name}" + (f" (rating {problem.rating})" if problem.rating else ""),
        f"Solved: {'yes' if solved else 'no'}",
    ]
    if attempts:
        lines.append("Approaches tried (in order):")
        for i, a in enumerate(attempts, 1):
            lines.append(f"  {i}. [{a['verdict']}] {a['approach']}")
    else:
        lines.append("Approaches tried: none")
    if questions:
        lines.append("Concept questions they asked:")
        for q in questions:
            lines.append(f"  - {q}")
    digest = "\n".join(lines)
    return generate(SUMMARIZER_SYSTEM, [{"role": "user", "content": digest}])
