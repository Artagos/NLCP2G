"""Per-session 'current problem', with adaptive selection and persistence.

Each session (browser) has its own current problem. New problems are chosen
*adaptively* from the learner's history (memory): repeats are excluded and the
difficulty band tracks the ratings they've already solved. The current problem
key is persisted so a refresh (or a server restart) restores the same problem.
"""
from __future__ import annotations

import logging

from . import bank, codeforces, memory
from .problem import Problem, fallback_problem

log = logging.getLogger("cp_tutor.state")

_MIN_RATING = 800
_MAX_RATING = 3500

# In-process cache of the full Problem (with tests) per session.
_cache: dict[str, Problem] = {}


def _key(p: Problem) -> str:
    return p.url or "fallback"


def _band(sid: str) -> tuple[int, int]:
    """Default difficulty band for the next problem, from what they've solved."""
    solved = memory.solved_ratings(sid)
    if not solved:
        return (_MIN_RATING, 1000)
    top = min(max(solved), 1500)  # progress upward, but don't jump to the deep end
    return (top, top + 200)


def _target_band(current_rating: int | None, rating_delta: int) -> tuple[int, int]:
    """Band centered on (current rating + delta), clamped to the rated range.
    A tight ±50 window targets that exact CF rating level."""
    base = current_rating or 1000
    target = max(_MIN_RATING, min(_MAX_RATING, base + rating_delta))
    return (max(_MIN_RATING, target - 50), min(_MAX_RATING, target + 50))


def _persist(sid: str, p: Problem) -> None:
    cid, idx = codeforces.parse_ref(p.url)
    memory.set_current(sid, _key(p), p.name, p.rating, cid, idx)


def _fetch(sid: str, lo: int, hi: int) -> Problem:
    """A problem in the [lo, hi] band that this learner hasn't seen.

    Codeforces first; the offline bank when it can't be reached. The bank keeps
    the difficulty band, repeat-avoidance and easier/harder deltas meaningful
    while the scrape is down — with a single hardcoded fallback they were all
    no-ops.
    """
    exclude = memory.seen_keys(sid)
    try:
        return codeforces.random_problem(exclude_keys=exclude, min_rating=lo, max_rating=hi)
    except Exception as exc:
        log.warning("Codeforces fetch failed (%s); using the offline bank", exc)
    try:
        return bank.random_problem(exclude_keys=exclude, min_rating=lo, max_rating=hi)
    except Exception as exc:            # pragma: no cover — the bank is static
        log.error("offline bank failed too (%s); using the single fallback", exc)
        return fallback_problem()


def current(sid: str) -> Problem:
    if sid in _cache:
        return _cache[sid]
    ref = memory.get_current_ref(sid)
    if ref:
        key, cid, idx = ref
        try:
            bank_id = bank.parse_key(key)
            if bank_id:
                p = bank.get(bank_id)
                if p is None:
                    raise LookupError(f"unknown bank problem {bank_id!r}")
            elif key == "fallback" or cid is None:
                p = fallback_problem()
            else:
                p = codeforces.fetch_problem(cid, idx)
            _cache[sid] = p
            return p
        except Exception as exc:
            log.warning("could not restore problem %s (%s); loading new", ref, exc)
    return load_new(sid)


def reset(sid: str) -> None:
    """Drop the cached problem for a session (call alongside memory.reset)."""
    _cache.pop(sid, None)


def load_new(sid: str, rating_delta: int = 0) -> Problem:
    """Load a new problem. A non-zero rating_delta targets (current rating +
    delta); otherwise the adaptive default band (from solved ratings) is used."""
    if rating_delta:
        lo, hi = _target_band(current(sid).rating, rating_delta)
    else:
        lo, hi = _band(sid)
    p = _fetch(sid, lo, hi)
    _cache[sid] = p
    _persist(sid, p)
    return p
