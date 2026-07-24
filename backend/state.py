"""Per-session 'current problem', with adaptive selection and persistence.

Each session (browser) has its own current problem. New problems are chosen
*adaptively* from the learner's history (memory): repeats are excluded and the
difficulty band tracks the ratings they've already solved. The current problem
key is persisted so a refresh (or a server restart) restores the same problem.
"""
from __future__ import annotations

import logging

from . import codeforces, memory
from .problem import Problem, fallback_problem

log = logging.getLogger("cp_tutor.state")

_MIN_RATING = 800
_MAX_RATING = 3500
_STEP = 200  # how much "easier"/"harder" shifts the target rating

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


def _directional_band(current_rating: int | None, direction: str) -> tuple[int, int]:
    """Band shifted one step easier/harder relative to the current problem."""
    base = current_rating or 1000
    if direction == "easier":
        target = max(_MIN_RATING, base - _STEP)
    else:  # harder
        target = min(_MAX_RATING, base + _STEP)
    return (max(_MIN_RATING, target - 100), min(_MAX_RATING, target + 100))


def _persist(sid: str, p: Problem) -> None:
    cid, idx = codeforces.parse_ref(p.url)
    memory.set_current(sid, _key(p), p.name, p.rating, cid, idx)


def _fetch(sid: str, lo: int, hi: int) -> Problem:
    exclude = memory.seen_keys(sid)
    try:
        return codeforces.random_problem(exclude_keys=exclude, min_rating=lo, max_rating=hi)
    except Exception as exc:
        log.warning("Codeforces fetch failed (%s); using offline fallback", exc)
        return fallback_problem()


def current(sid: str) -> Problem:
    if sid in _cache:
        return _cache[sid]
    ref = memory.get_current_ref(sid)
    if ref:
        key, cid, idx = ref
        try:
            p = fallback_problem() if (key == "fallback" or cid is None) \
                else codeforces.fetch_problem(cid, idx)
            _cache[sid] = p
            return p
        except Exception as exc:
            log.warning("could not restore problem %s (%s); loading new", ref, exc)
    return load_new(sid)


def load_new(sid: str, direction: str | None = None) -> Problem:
    """Load a new problem. direction 'easier'/'harder' shifts the rating band
    relative to the current problem; otherwise the adaptive default band is used."""
    if direction in ("easier", "harder"):
        lo, hi = _directional_band(current(sid).rating, direction)
    else:
        lo, hi = _band(sid)
    p = _fetch(sid, lo, hi)
    _cache[sid] = p
    _persist(sid, p)
    return p
