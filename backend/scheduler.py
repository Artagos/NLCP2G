"""The background trigger: a heartbeat that runs whether or not anyone is watching.

This is the second of the two non-user triggers (the first is the sandbox
run-complete webhook). It wakes on a fixed interval, builds each linked learner's
state, asks `triggers.decide()` what to do, records the answer either way, and
sends only when the answer is `Fire`.

Deliberately separate from the bot process. The bot is a request loop; this is a
clock. Keeping them apart means the trigger cannot be accidentally satisfied by a
user message arriving, and it can be run on demand:

    python -m backend.scheduler --once
    python -m backend.scheduler --once --now 2026-07-29T03:00      # test quiet hours
    python -m backend.scheduler --once --dry-run                   # decide, send nothing
    python -m backend.scheduler --interval 900                     # the real heartbeat

`--now` is how the trigger is made to fire on purpose. Nothing in the decision
reads the clock itself, so passing a timestamp is enough to put the whole system
at 3am, or a minute after someone's last message.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import time
from datetime import datetime

from . import memory, triggers
from .channels import Channel, FakeChannel, TelegramChannel
from .triggers import Decision, Fire, LearnerState, Silence

log = logging.getLogger("cp_tutor.scheduler")

MUTE_KEY = "nudges_muted"


def sid_for(user_id: str) -> str:
    return f"u:{user_id}"


def gather(link: dict) -> LearnerState:
    """Build one learner's state from the stores. No writes, no problem loading."""
    user_id = link["user_id"]
    sid = sid_for(user_id)

    ref = memory.get_current_ref(sid)
    key = ref[0] if ref else None
    row = memory.seen_row(sid, key) if key else None
    attempts = [a["verdict"] for a in memory.attempts_for(sid, key)] if key else []

    return LearnerState(
        user_id=user_id,
        chat_id=str(link["chat_id"]),
        problem_key=key,
        problem_name=(row or {}).get("name"),
        problem_rating=(row or {}).get("rating"),
        solved=bool((row or {}).get("solved")),
        attempts=attempts,
        last_activity_at=memory.last_activity_at(sid),
        last_nudge_at=memory.last_nudge_at(user_id),
        nudges_on_problem=memory.nudges_for_problem(user_id, key) if key else 0,
        tz_offset=int(link.get("tz_offset") or 0),
        muted=memory.get_setting(MUTE_KEY, "0") == "1",
        run_in_flight=bool(memory.pending_jobs(user_id)),
    )


async def sweep(channel: Channel, now: float | None = None,
                dry_run: bool = False) -> list[tuple[str, Decision]]:
    """One pass over every linked learner. Returns the decisions, for the caller
    to report; every decision is also persisted."""
    now = time.time() if now is None else now
    outcomes: list[tuple[str, Decision]] = []

    for link in memory.all_links(channel.name):
        state = gather(link)
        decision = triggers.decide(state, now)
        outcomes.append((state.user_id, decision))

        if isinstance(decision, Fire):
            if not dry_run:
                try:
                    await channel.send(state.chat_id, decision.text)
                except Exception as exc:
                    # a send failure is not a decision to stay silent; record it
                    # as such rather than letting it masquerade as one
                    log.warning("nudge delivery to %s failed: %s", state.chat_id, exc)
                    memory.record_nudge(state.user_id, state.chat_id, "failed",
                                        "delivery_error", decision.kind,
                                        state.problem_key, str(exc)[:300])
                    continue
            memory.record_nudge(state.user_id, state.chat_id,
                                "dry-run" if dry_run else "fired", "", decision.kind,
                                state.problem_key, decision.text[:500])
        else:
            memory.record_nudge(state.user_id, state.chat_id, "silent",
                                decision.reason, "", state.problem_key, decision.detail)

    return outcomes


def format_outcomes(outcomes: list[tuple[str, Decision]]) -> str:
    if not outcomes:
        return "no linked learners — nothing to decide"
    lines = []
    for user_id, d in outcomes:
        if isinstance(d, Fire):
            lines.append(f"  FIRED   {user_id}: {d.kind}")
        else:
            lines.append(f"  silent  {user_id}: {d.reason} — {d.detail}")
    fired = sum(1 for _, d in outcomes if isinstance(d, Fire))
    return (f"{len(outcomes)} learner(s): {fired} nudged, {len(outcomes) - fired} silent\n"
            + "\n".join(lines))


async def run(channel: Channel, interval: int | None, once: bool,
              now: float | None, dry_run: bool) -> None:
    while True:
        outcomes = await sweep(channel, now=now, dry_run=dry_run)
        print(format_outcomes(outcomes))
        if once or not interval:
            return
        await asyncio.sleep(interval)


def parse_now(value: str | None) -> float | None:
    """Accept an ISO timestamp or a unix epoch; None means 'actually now'."""
    if not value:
        return None
    try:
        return float(value)
    except ValueError:
        return datetime.fromisoformat(value).timestamp()


def main() -> None:
    parser = argparse.ArgumentParser(description="NLCP2G background nudge trigger.")
    parser.add_argument("--once", action="store_true", help="one pass, then exit")
    parser.add_argument("--interval", type=int, default=900,
                        help="seconds between passes (default 900)")
    parser.add_argument("--now", type=str,
                        help="pretend it is this time (ISO 8601 or epoch) — how you "
                             "make the trigger fire on purpose")
    parser.add_argument("--dry-run", action="store_true",
                        help="decide and record, but send nothing")
    parser.add_argument("--fake-channel", action="store_true",
                        help="decide against a fake channel (no token needed)")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    memory.init()

    channel: Channel = FakeChannel(name="telegram") if args.fake_channel else TelegramChannel()
    try:
        asyncio.run(run(channel, args.interval, args.once, parse_now(args.now), args.dry_run))
    finally:
        asyncio.run(channel.close())


if __name__ == "__main__":
    main()
