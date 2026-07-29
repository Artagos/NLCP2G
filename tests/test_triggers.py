"""The background trigger, and the silence branch.

This is the file the homework's "how do you know the silence branch works?"
question is answered by. Three things make it possible to test a trigger nobody
fires by hand:

  * `decide()` never reads the clock — `now` is a parameter, so 03:00 is just an
    argument;
  * it returns a value rather than performing an action, so a test asserts on the
    decision instead of watching for an absence;
  * the sweep records every decision, so "stayed silent on purpose" and "was
    broken" are distinguishable — which is exactly what `test_..._records_...`
    below checks.
"""
from __future__ import annotations

import asyncio

import pytest

from backend import memory, scheduler, triggers
from backend.channels import FakeChannel
from backend.triggers import Fire, LearnerState, Silence

# A fixed reference time: 2026-07-29 14:00 UTC, comfortably inside waking hours.
NOON = 1785074400.0
HOUR = 3600.0
DAY = 24 * HOUR


def state(**kw) -> LearnerState:
    """A learner who SHOULD be nudged, so each test changes exactly one thing."""
    base = dict(
        user_id="alice", chat_id="1001",
        problem_key="bank:count-pairs", problem_name="Count Pairs With Sum K",
        problem_rating=1000, solved=False, attempts=["WA"],
        last_activity_at=NOON - 3 * DAY, last_nudge_at=None,
        nudges_on_problem=0, tz_offset=0, muted=False, run_in_flight=False,
    )
    base.update(kw)
    return LearnerState(**base)


def test_the_baseline_learner_is_nudged():
    d = triggers.decide(state(), NOON)
    assert isinstance(d, Fire)
    assert d.kind == "stale_unsolved"
    assert "Count Pairs With Sum K" in d.text


def test_a_learner_who_never_tried_gets_a_different_nudge():
    d = triggers.decide(state(attempts=[]), NOON)
    assert isinstance(d, Fire) and d.kind == "unstarted"


# ----------------------------------------------------------- silence branches

@pytest.mark.parametrize("reason,changes", [
    ("nudges_muted",           {"muted": True}),
    ("no_channel",             {"chat_id": ""}),
    ("run_in_flight",          {"run_in_flight": True}),
    ("nothing_pending",        {"problem_key": None}),
    ("nothing_pending",        {"solved": True}),
    ("awaiting_learner",       {"attempts": ["WA", "UNCLEAR"]}),
    ("awaiting_learner",       {"attempts": ["NEEDS_CLARIFICATION"]}),
    ("awaiting_learner",       {"attempts": ["INFEASIBLE"]}),
    ("would_hint",             {"attempts": ["WA", "TLE", "TLE"]}),
    ("recently_active",        {"last_activity_at": NOON - 300}),
    ("cooldown",               {"last_nudge_at": NOON - 2 * HOUR}),
    ("already_nudged_problem", {"nudges_on_problem": 2}),
])
def test_each_silence_condition_stays_silent_with_its_reason(reason, changes):
    d = triggers.decide(state(**changes), NOON)
    assert isinstance(d, Silence), f"expected silence for {changes}, got {d}"
    assert d.reason == reason
    assert d.detail, "a silence with no detail is not a written record"


def test_quiet_hours_use_the_learners_own_offset():
    # 14:00 UTC is fine in UTC, but it is 02:00 for someone at +12
    assert isinstance(triggers.decide(state(tz_offset=0), NOON), Fire)
    d = triggers.decide(state(tz_offset=12), NOON)
    assert isinstance(d, Silence) and d.reason == "quiet_hours"


def test_three_am_is_silent_and_that_is_all_it_takes_to_test_it():
    three_am = NOON - 11 * HOUR      # 03:00 UTC
    d = triggers.decide(state(), three_am)
    assert isinstance(d, Silence) and d.reason == "quiet_hours"


def test_a_single_timeout_is_not_yet_a_reason_to_go_quiet():
    """One TLE is ordinary. It takes a streak before a nudge reads as a hint."""
    assert isinstance(triggers.decide(state(attempts=["TLE"]), NOON), Fire)
    d = triggers.decide(state(attempts=["TLE", "TLE"]), NOON)
    assert isinstance(d, Silence) and d.reason == "would_hint"


def test_a_timeout_streak_is_broken_by_a_later_verdict():
    assert isinstance(triggers.decide(state(attempts=["TLE", "TLE", "WA"]), NOON), Fire)


def test_the_nudge_never_mentions_a_verdict():
    """The nudge is unsolicited, so it is the one message that must not hint.
    It may name the problem and count attempts; verdicts are off limits."""
    for attempts in (["WA"], ["WA", "WA"], ["CE"], ["RE", "WA"]):
        d = triggers.decide(state(attempts=attempts), NOON)
        assert isinstance(d, Fire)
        for verdict in ("WA", "TLE", "CE", "RE", "wrong answer", "too slow", "timed out"):
            assert verdict not in d.text


def test_cooldown_counts_from_fired_nudges_only():
    """Being silent 10 times must not start a cooldown; only a sent one does."""
    assert triggers.COOLDOWN_S > 0
    d = triggers.decide(state(last_nudge_at=NOON - triggers.COOLDOWN_S - 1), NOON)
    assert isinstance(d, Fire)


# ------------------------------------------------------- the recorded decision

def _link(user_id="alice", chat_id="1001", tz=0):
    memory.link_chat("telegram", chat_id, user_id, tz_offset=tz)


def test_a_sweep_that_stays_silent_writes_down_why(monkeypatch):
    """The point of the whole design: silence is auditable.

    Without the record, a bot that decided not to speak and a bot that fell over
    are the same observation.
    """
    _link()
    monkeypatch.setattr(scheduler, "gather", lambda link: state(
        user_id=link["user_id"], chat_id=link["chat_id"], attempts=["TLE", "TLE"]))
    channel = FakeChannel(name="telegram")

    outcomes = asyncio.run(scheduler.sweep(channel, now=NOON))

    assert channel.said_nothing                     # nothing was sent ...
    rows = memory.recent_nudges()
    assert len(rows) == 1                           # ... but it is on the record
    assert rows[0]["decision"] == "silent"
    assert rows[0]["reason"] == "would_hint"
    assert "timeouts in a row" in rows[0]["detail"]
    assert isinstance(outcomes[0][1], Silence)


def test_a_sweep_that_fires_sends_once_and_records_it(monkeypatch):
    _link()
    monkeypatch.setattr(scheduler, "gather",
                        lambda link: state(user_id=link["user_id"],
                                           chat_id=link["chat_id"]))
    channel = FakeChannel(name="telegram")

    asyncio.run(scheduler.sweep(channel, now=NOON))

    assert channel.texts_to("1001"), "expected a nudge"
    row = memory.recent_nudges()[0]
    assert row["decision"] == "fired" and row["kind"] == "stale_unsolved"


def test_dry_run_decides_and_records_but_sends_nothing(monkeypatch):
    _link()
    monkeypatch.setattr(scheduler, "gather",
                        lambda link: state(user_id=link["user_id"],
                                           chat_id=link["chat_id"]))
    channel = FakeChannel(name="telegram")

    asyncio.run(scheduler.sweep(channel, now=NOON, dry_run=True))

    assert channel.said_nothing
    assert memory.recent_nudges()[0]["decision"] == "dry-run"


def test_a_delivery_failure_is_not_recorded_as_a_decision_to_be_silent(monkeypatch):
    """Otherwise an outage would look like good judgement in the audit trail."""
    _link()
    monkeypatch.setattr(scheduler, "gather",
                        lambda link: state(user_id=link["user_id"],
                                           chat_id=link["chat_id"]))
    channel = FakeChannel(name="telegram", fail_on={"1001"})

    asyncio.run(scheduler.sweep(channel, now=NOON))

    row = memory.recent_nudges()[0]
    assert row["decision"] == "failed" and row["reason"] == "delivery_error"


def test_a_sweep_with_no_linked_learners_does_nothing_quietly():
    channel = FakeChannel(name="telegram")
    assert asyncio.run(scheduler.sweep(channel, now=NOON)) == []
    assert channel.said_nothing


def test_gather_never_hands_out_a_problem():
    """A scheduler pass must not have side effects on the learner's state.
    `state.current()` would ASSIGN a problem to someone who has none."""
    _link(user_id="bob", chat_id="2002")
    link = memory.get_link("telegram", "2002")

    s = scheduler.gather(link)

    assert s.problem_key is None                     # nothing was assigned
    assert memory.get_current_ref("u:bob") is None


def test_parse_now_accepts_iso_and_epoch():
    assert scheduler.parse_now(None) is None
    assert scheduler.parse_now("1785074400") == 1785074400.0
    assert scheduler.parse_now("2026-07-29T03:00:00") > 0
