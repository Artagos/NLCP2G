"""The state schemas every graph in this system runs on.

One `TypedDict` per graph, with reducers on the fields that accumulate. This is
the part of the refactor that pays for itself: the old orchestration passed a
`ChatResponse` plus a handful of locals down a chain of function calls, and what
a given step could see or change was whatever happened to be in scope. Here the
contract is written down — a node declares what it reads by name and returns a
partial update, and nothing else can be touched.

Two rules the schemas below follow, both learned from what this system does:

**Nothing large or live goes in checkpointed state.** `TurnState` carries a
`problem_key`, never a `Problem`. A statement is kilobytes of HTML and it would
be written into every checkpoint of every turn. The one exception is
`SolutionState`, which does hold the `Problem` — and that graph is compiled
*without* a checkpointer precisely so it can (see `graphs/solution.py`).

**Accumulators get reducers, not append-in-place.** `facts_used`, `notes_seen`
and `tools_called` are written by tool calls that may run in the same superstep,
so they merge rather than overwrite. They are deduplicated because they end up
in the run log the monitor grades, and a doubled id there reads as the agent
having pulled the same memory twice.
"""
from __future__ import annotations

import operator
from typing import Annotated, Any, Sequence, TypedDict

from langchain_core.messages import AnyMessage
from langgraph.graph.message import add_messages

from ..problem import Problem


def _merge_unique(left: Sequence[Any] | None, right: Sequence[Any] | None) -> list[Any]:
    """Concatenate, preserving order, dropping repeats."""
    out: list[Any] = []
    for item in list(left or []) + list(right or []):
        if item not in out:
            out.append(item)
    return out


# --------------------------------------------------------------- the chat turn

class TurnState(TypedDict, total=False):
    """One learner message, from routing to reply.

    This is the only state in the system that is checkpointed, and that shapes
    it. Every field here except `messages` is *per-turn* and is overwritten by
    the input on each invocation; `messages` is the one thing that accumulates,
    because it is the conversation and the conversation is what resumes.

    Accumulating fields deliberately do NOT live here. `facts_used`,
    `notes_seen`, `tools_called` and `violations` belong to the tutor and
    solution graphs, which are not checkpointed and start clean every turn. Put
    a reducer-backed list in a checkpointed state and it merges with last turn's
    values, so the run log would slowly claim the agent had pulled every memory
    it had ever pulled. They come back through `meta` instead.
    """

    # --- who and what -------------------------------------------------------
    sid: str                    # memory scope, "u:<uid>" — also the thread id
    uid: str                    # the learner
    run_id: str                 # ties this turn to the run log and scratchpad
    message: str                # what they said

    # --- routing ------------------------------------------------------------
    intent: str                 # concept | meta | strategy | solution | ...
    rating_delta: int           # new_problem only: how much harder/easier
    reason: str                 # the router's justification, for the log

    # --- the problem, by reference ------------------------------------------
    # Deliberately the key and not the object: see the module docstring.
    problem_key: str

    # --- the solution branch ------------------------------------------------
    # Set when the sandbox run must be handed to a worker rather than executed
    # inline: {"channel": ..., "chat_id": ...}. None on the web path.
    defer: dict | None
    # What the blind pipeline produced, carried from `build` to whichever node
    # runs it. Plain fields with no reducer, overwritten by the seed each turn:
    # a program is a kilobyte or so, which is a fine thing to checkpoint. A
    # problem *statement* is not, which is why only the key is up there.
    ready: bool
    cpp_source: str
    approach_summary: str
    critic_status: str
    critic_rounds: int
    violations: list[str]

    # --- the conversation, and the answer -----------------------------------
    messages: Annotated[list[AnyMessage], add_messages]
    reply: str
    verdict: str
    meta: dict


# -------------------------------------------------------------- the tutor loop

class TutorState(TypedDict, total=False):
    """The guardrailed tutor and its tools.

    Its own state rather than a slice of `TurnState`, for the same reason the
    solution pipeline has its own: it needs the `Problem` object (the tutor is
    the one agent that *does* see the statement, so it can recognise a
    problem-specific question and refuse it), and the tool-call scaffolding it
    generates is working memory, not conversation. What gets checkpointed as the
    conversation is the learner's message and the tutor's final answer, which
    the turn graph appends — not the six messages it took to get there.
    """

    problem: Problem
    uid: str
    problem_key: str
    message: str

    messages: Annotated[list[AnyMessage], add_messages]
    system: str                                     # the pushed block + prompt
    rules_applied: list[str]
    facts_used: Annotated[list[str], _merge_unique]
    notes_seen: Annotated[list[int], _merge_unique]
    tools_called: Annotated[list[str], _merge_unique]
    reply: str


# ------------------------------------------------- the blind solution pipeline

class SolutionState(TypedDict, total=False):
    """Screener -> executor <-> critic -> (run | stop).

    Holds the `Problem` itself rather than a key, because this graph is compiled
    without a checkpointer and never persisted: re-resolving the key per node
    would mean a Codeforces round trip in the middle of a turn.

    The blindness guarantee does not live here. It lives in `prompts.py`, which
    renders only the raw I/O format for these three agents and never the
    statement — so a node holding the whole `Problem` still cannot leak it.
    """

    problem: Problem
    described_approach: str     # the learner's words: the source of truth
    run_id: str | None

    # screener
    feasible: bool
    issue: str

    # executor
    can_implement: bool
    cpp_source: str
    approach_summary: str
    blocking_issue: str
    revision: str               # the critic's fix list, on a rebuild
    rounds: int

    # critic. The Handoff object itself is kept, not flattened: the escalation
    # message is rendered from it by critic.fix_list, and losing the structure
    # here would mean re-deriving it from strings.
    handoff: Any                # critic.Handoff — typed loosely to avoid a cycle
    critic_status: str          # approved | revise | escalate | not_reached
    needs_approval: bool
    violations: Annotated[list[str], operator.add]

    # what came out: "ready" means approved and not yet executed
    ready: bool
    verdict: str                # set only on a terminal non-ready outcome
    reply: str


# ------------------------------------------------------- the operator subagent

class AdminState(TypedDict, total=False):
    """The privileged path. `actor` is the chat id that already passed
    `admin.is_admin()` — the graph is never entered otherwise, so authorisation
    is a code gate rather than something a node is asked to decide."""

    actor: str
    message: str
    messages: Annotated[list[AnyMessage], add_messages]
    reply: str


# --------------------------------------------------------- out-of-band workers

class MonitorState(TypedDict, total=False):
    """The judge's pass over the run log. Grades one run at a time so a single
    model failure costs one verdict rather than the whole batch."""

    limit: int
    pending: list[dict]         # ungraded runs still to look at
    graded: int
    judged: list[dict]          # every judgment on file, for the analyst
    findings: str
    report_path: str | None


class NudgeState(TypedDict, total=False):
    """Whether to say something unprompted to one learner, and what happened.

    The graph runs once per linked learner. `decision` is `Fire` or
    `Silence(reason)` from triggers.py, and both are recorded — a silence with a
    reason is the interesting half of this system, not an absence of output.
    """

    link: dict
    channel: Any                # channels.Channel; never persisted
    now: float
    dry_run: bool
    learner: Any                # triggers.LearnerState
    decision: Any               # triggers.Decision


class ReflectState(TypedDict, total=False):
    """Should anything from this exchange be remembered? Usually not."""

    user_id: str
    user_message: str
    assistant_reply: str
    intent: str
    problem_key: str | None
    save: bool
    kind: str
    text: str
    cue_keywords: list[str]
    cue_note: str
    reason: str
    saved: dict | None


class SummaryState(TypedDict, total=False):
    """Recap of one learner's activity on one problem."""

    problem: Problem
    attempts: list[dict]
    questions: list[str]
    digest: str
    recap: str
