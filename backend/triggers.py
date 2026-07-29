"""The background trigger's decision: nudge, or deliberately stay quiet.

Two design choices here are the whole point of this module.

**The decision is a pure function of state and a passed-in `now`.** Nothing in
`decide()` reads the clock, the database, or the network. That is what makes a
trigger you do not control testable: a test can ask "what would you do at 03:00,
for a learner who was active four minutes ago, who has already been nudged
today?" without waiting for 03:00 to come round.

**Silence is an outcome, not an absence.** Every evaluation returns either
`Fire` or `Silence(reason)`, and the caller records both. A bot that says nothing
because it decided to and a bot that says nothing because it crashed look
identical from the outside; the recorded reason is the only thing that
distinguishes them, which is why the reason is a required field rather than a log
line.

Two of the silence reasons come from the product's own promise rather than from
generic politeness, and they are the interesting ones:

  awaiting_learner — the agent has already asked the learner a question (their
      description was too vague to build, or the critic escalated). Nudging would
      mean re-asking, and any elaboration means supplying the algorithmic step
      they didn't give. So: nothing.
  would_hint — the learner's recent attempts have all timed out. A nudge at that
      moment can only be read two ways: "try again" (useless) or "your approach
      is too slow" (a hint, which rule R1 forbids). Neither is worth sending.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Literal

# Verdicts that mean the ball is already in the learner's court.
_AWAITING = {"UNCLEAR", "INFEASIBLE", "NEEDS_CLARIFICATION"}

# Tunables. Env-overridable so the admin agent can change pacing without a deploy.
ACTIVE_WINDOW_S = int(os.environ.get("CP_TUTOR_ACTIVE_WINDOW_S", 90 * 60))
COOLDOWN_S = int(os.environ.get("CP_TUTOR_NUDGE_COOLDOWN_S", 20 * 60 * 60))
QUIET_FROM_HOUR = int(os.environ.get("CP_TUTOR_QUIET_FROM", 22))    # local
QUIET_UNTIL_HOUR = int(os.environ.get("CP_TUTOR_QUIET_UNTIL", 8))
MAX_NUDGES_PER_PROBLEM = int(os.environ.get("CP_TUTOR_MAX_NUDGES_PER_PROBLEM", 2))
# how many consecutive timeouts before a nudge would amount to a hint
TLE_STREAK_FOR_HINT = int(os.environ.get("CP_TUTOR_TLE_STREAK", 2))


@dataclass
class LearnerState:
    """Everything the decision is allowed to look at."""

    user_id: str
    chat_id: str
    problem_key: str | None = None
    problem_name: str | None = None
    problem_rating: int | None = None
    solved: bool = False
    # verdicts on the current problem, oldest first
    attempts: list[str] = field(default_factory=list)
    last_activity_at: float | None = None
    last_nudge_at: float | None = None
    nudges_on_problem: int = 0
    tz_offset: int = 0              # hours from UTC, for quiet hours
    muted: bool = False
    run_in_flight: bool = False


@dataclass
class Fire:
    kind: Literal["unstarted", "stale_unsolved"]
    text: str
    decision: str = "fired"
    reason: str = ""


@dataclass
class Silence:
    reason: str
    detail: str = ""
    decision: str = "silent"


Decision = Fire | Silence


def _local_hour(now: float, tz_offset: int) -> int:
    return int((now // 3600 + tz_offset) % 24)


def _tle_streak(attempts: list[str]) -> int:
    streak = 0
    for verdict in reversed(attempts):
        if verdict == "TLE":
            streak += 1
        else:
            break
    return streak


# Nudge text is templated, never model-generated. A nudge is unsolicited, so it
# is the one message the learner did not opt into — the safest way to guarantee
# it carries no hint is for no model to write it. It may name the problem and
# count the attempts; it must never mention a verdict.
def _text_unstarted(s: LearnerState) -> str:
    rating = f" (rating {s.problem_rating})" if s.problem_rating else ""
    return (
        f"You've got {s.problem_name}{rating} open and haven't tried anything on "
        "it yet. Want to have a go? Describe how you'd solve it in your own words "
        "and I'll build it and run it. Or say \"another problem\" to switch."
    )


def _text_stale(s: LearnerState) -> str:
    n = len(s.attempts)
    tries = "1 attempt" if n == 1 else f"{n} attempts"
    return (
        f"You left {s.problem_name} unfinished after {tries}. Still fancy it? "
        "Describe your next idea and I'll run it — or say \"another problem\" for "
        "something different, or \"summarize\" for a recap of what you tried."
    )


def decide(s: LearnerState, now: float) -> Decision:
    """Should the background trigger say anything to this learner right now?"""

    # ---- preconditions: can we speak at all? ----
    if s.muted:
        return Silence("nudges_muted", "an operator has muted background nudges")
    if not s.chat_id:
        return Silence("no_channel", "learner has no linked chat to write to")
    if s.run_in_flight:
        return Silence("run_in_flight",
                       "a sandbox run is still going; the agent already owes them a message")

    # ---- phase 1: is there anything worth saying? ----
    if not s.problem_key:
        return Silence("nothing_pending", "no problem is open for this learner")
    if s.solved:
        return Silence("nothing_pending", f"{s.problem_name} is already solved")

    last = s.attempts[-1] if s.attempts else None
    if last in _AWAITING:
        return Silence(
            "awaiting_learner",
            f"the agent already asked a question after a {last} verdict; nudging "
            "would either repeat it or supply the missing step",
        )

    streak = _tle_streak(s.attempts)
    if streak >= TLE_STREAK_FOR_HINT:
        return Silence(
            "would_hint",
            f"{streak} timeouts in a row — an unprompted message here reads as "
            "'your approach is too slow', which R1 forbids",
        )

    # ---- phase 2: is now a reasonable moment? ----
    if s.last_activity_at is not None and now - s.last_activity_at < ACTIVE_WINDOW_S:
        mins = int((now - s.last_activity_at) // 60)
        return Silence("recently_active", f"active {mins} min ago; they don't need chasing")
    if s.last_nudge_at is not None and now - s.last_nudge_at < COOLDOWN_S:
        hours = round((now - s.last_nudge_at) / 3600, 1)
        return Silence("cooldown", f"last nudged {hours}h ago")
    if s.nudges_on_problem >= MAX_NUDGES_PER_PROBLEM:
        return Silence("already_nudged_problem",
                       f"already nudged {s.nudges_on_problem}x about {s.problem_name}")

    hour = _local_hour(now, s.tz_offset)
    if hour >= QUIET_FROM_HOUR or hour < QUIET_UNTIL_HOUR:
        return Silence("quiet_hours", f"local time is {hour:02d}:xx")

    # ---- fire ----
    if not s.attempts:
        return Fire(kind="unstarted", text=_text_unstarted(s))
    return Fire(kind="stale_unsolved", text=_text_stale(s))
