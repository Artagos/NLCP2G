"""The second agent: a fidelity critic that checks the Executor's output.

The Executor (translator.py) turns a learner's words into C++. The promise this
product makes is that it builds *exactly* what they described — but "be helpful"
is the strongest instinct a code model has, and the failure mode is silent: it
sorts before scanning, guards a division, replaces the described double loop
with something smarter, and the learner is told their idea passed when what
passed was the model's idea.

So a second agent reviews the code against the description, on a written rubric
(C1–C4 in prompts.critic_system), with the same blindness the Executor has: raw
I/O format and the learner's words, nothing else. It cannot judge correctness,
because it does not know the problem — which is precisely why it can only judge
fidelity.

The two agents exchange a small structured object and branch on its fields, not
on each other's prose:

    Handoff(status, result, needs_approval)

      approved  -> run the program
      revise    -> Executor rebuilds with `result` as a removal/restore list
      escalate  -> stop and ask the learner (needs_approval=True)

Every exchange is appended to the shared scratchpad (memory.scratch) under the
run id, so the whole negotiation can be replayed afterwards.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from .llm import generate_structured
from .problem import Problem
from .prompts import critic_system

# How many times the Executor may be sent back. The brief states this too; it is
# enforced here so a chatty critic cannot spend the learner's time.
MAX_ROUNDS = 2


class Violation(BaseModel):
    rule: str = ""    # rubric id: C1 INVENTED_LOGIC ... C4 SILENT_REPAIR
    quote: str = ""   # the offending fragment of the generated code
    why: str = ""     # why it is not in the learner's words


class Handoff(BaseModel):
    """The structured object the agents pass between them."""

    status: Literal["approved", "revise", "escalate"]
    # empty on approve; a removal/restore list on revise; a question on escalate
    result: str = ""
    # true only on escalate: the loop stops and the learner is asked
    needs_approval: bool = False
    violations: list[Violation] = []
    confidence: float = 0.0


def review(problem: Problem, described_approach: str, cpp_source: str,
           approach_summary: str) -> Handoff:
    """Ask the critic whether the program matches the description."""
    handoff = generate_structured(
        critic_system(problem),
        [{
            "role": "user",
            "content": (
                "--- what the learner said they wanted ---\n"
                f"{described_approach}\n\n"
                "--- what the Executor says it implemented ---\n"
                f"{approach_summary}\n\n"
                "--- the program it produced ---\n"
                f"```cpp\n{cpp_source}\n```\n\n"
                "Does this program do what the learner's words say — no more, "
                "no less? Apply the rubric."
            ),
        }],
        Handoff,
    )
    # Trust the branch field over the flag: needs_approval is only ever true on
    # an escalation, whatever the model filled in.
    handoff.needs_approval = handoff.status == "escalate"
    if handoff.status == "approved":
        handoff.result = ""
        handoff.violations = []
    return handoff


def fix_list(handoff: Handoff) -> str:
    """Render the critic's findings as the imperative list the Executor gets."""
    if handoff.result.strip():
        return handoff.result.strip()
    return "\n".join(
        f"- [{v.rule}] {v.why}" + (f'  (offending code: "{v.quote}")' if v.quote else "")
        for v in handoff.violations
    ) or "- The program did not match the description; rebuild it literally."
