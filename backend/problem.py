"""The Problem model, plus an offline fallback.

Problems now come from Codeforces (see codeforces.py); this module defines the
shape every agent consumes and a single hardcoded fallback used only when the
network / Codeforces is unreachable.

What each agent is allowed to see:
  * tutor + UI  -> `statement` (full text) and `tags`
  * translator + screener -> `io_format` ONLY (the raw I/O contract, no task
    narrative, no hints, no intended solution)
  * sandbox -> `tests`, `time_limit_ms`, `memory_limit_mb`
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field


@dataclass
class Test:
    name: str
    stdin: str
    expected_stdout: str


@dataclass
class Problem:
    name: str                       # e.g. "4A - Watermelon"
    statement: str                  # plain text — tutor's LLM context
    io_format: str                  # translator + screener see ONLY this
    tags: list[str]
    tests: list[Test]
    time_limit_ms: int = 2000
    memory_limit_mb: int = 256
    url: str | None = None
    rating: int | None = None
    source: str = "codeforces"
    statement_html: str | None = None  # rich HTML for the UI (math via MathJax)


# --------------------------------------------------------------------------- #
# Offline fallback: the original calibrated "Count Pairs With Sum K" problem.
# Used only when Codeforces can't be reached, so the app still runs offline.
# --------------------------------------------------------------------------- #

_FALLBACK_STATEMENT = """\
Problem: Count Pairs With Sum K  (offline fallback)

You are given an array of n integers and a target value K.
Count the number of pairs of positions (i, j) with i < j such that
a_i + a_j == K.

Input
  Line 1: two integers  n  K
  Line 2: n integers a_1 a_2 ... a_n

Output
  A single integer: the number of valid pairs.

Constraints
  1 <= n <= 1_000_000 ; -1e9 <= a_i, K <= 1e9

Example
  Input:  5 6 / 1 2 3 4 5      Output: 2   (pairs 1+5 and 2+4)
"""

_FALLBACK_IO = """\
Standard input:
  - Line 1: two integers, separated by a space. Call them n and k.
  - Line 2: n integers, separated by spaces.

Standard output:
  - Write the program's result to standard output.
"""


def _count_pairs(nums: list[int], k: int) -> int:
    seen: dict[int, int] = {}
    count = 0
    for x in nums:
        count += seen.get(k - x, 0)
        seen[x] = seen.get(x, 0) + 1
    return count


def _fmt(n: int, k: int, nums: list[int]) -> str:
    return f"{n} {k}\n{' '.join(map(str, nums))}\n"


def _fallback_tests() -> list[Test]:
    tests: list[Test] = []
    small = [
        ("example", 6, [1, 2, 3, 4, 5]),
        ("duplicates", 4, [2, 2, 2, 2, 0, 4]),
        ("no_pairs", 100, [1, 2, 3]),
        ("negatives", 0, [-3, 3, -1, 1, 0, 0]),
        ("single", 5, [5]),
    ]
    for name, k, nums in small:
        tests.append(Test(name, _fmt(len(nums), k, nums), f"{_count_pairs(nums, k)}\n"))
    rng = random.Random(20260722)
    n, k = 1_000_000, 1_000
    nums = [rng.randint(1, 1_000) for _ in range(n)]
    tests.append(Test("big_n_1e6", _fmt(n, k, nums), f"{_count_pairs(nums, k)}\n"))
    return tests


def fallback_problem() -> Problem:
    return Problem(
        name="Count Pairs With Sum K (offline fallback)",
        statement=_FALLBACK_STATEMENT,
        io_format=_FALLBACK_IO,
        tags=["arrays", "hashing", "two-pointers"],
        tests=_fallback_tests(),
        time_limit_ms=1500,
        memory_limit_mb=256,
        url=None,
        rating=800,
        source="fallback",
    )
