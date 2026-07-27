"""An offline bank of original problems, with generated stress tests.

Why this exists. Problems normally come from Codeforces, but the statement pages
sit behind an anti-bot challenge that we are not going to try to defeat. When the
scrape fails the app used to fall back to a single hardcoded problem, which
quietly turned off everything interesting: "give me another problem", the
adaptive difficulty band, repeat-avoidance, and the calibrated easier/harder
deltas all become no-ops when there is exactly one problem to choose from.

So the fallback is a real bank instead: original problems (written for this
project — nothing copied from any judge) spread across ratings 800–1500.

The other thing this buys back is a **real TLE signal**. Scraped problems only
ever come with their published samples, which are tiny, so a hopeless O(n²) idea
passes and the learner is told it worked. Here we own the reference solution, so
each problem generates its own large case, sized so that the naive approach a
beginner reaches for times out and a reasonable one doesn't. That is the whole
teaching mechanism of this product, and it only works with big inputs.

Tests are generated lazily, on selection, and the built problem is cached — the
large cases are a megabyte or so of text and there is no reason to hold seven of
them in memory at startup.
"""
from __future__ import annotations

import random
import zlib
from dataclasses import dataclass
from html import escape
from typing import Callable

from .problem import Problem, Test

KEY_PREFIX = "bank:"


@dataclass
class Spec:
    id: str
    name: str
    rating: int
    tags: list[str]
    statement: str          # plain text, shown to the tutor and rendered in the UI
    io_format: str          # the ONLY thing the blind agents see
    build_tests: Callable[[], list[Test]]
    time_limit_ms: int = 2000
    memory_limit_mb: int = 256


def key_of(spec_or_problem) -> str:
    """Stable key for memory (`seen`, `attempts`, notes)."""
    if isinstance(spec_or_problem, Spec):
        return f"{KEY_PREFIX}{spec_or_problem.id}"
    url = getattr(spec_or_problem, "url", None) or ""
    return url if url.startswith(KEY_PREFIX) else url


def parse_key(key: str) -> str | None:
    """`bank:two-sum` -> `two-sum`, anything else -> None."""
    return key[len(KEY_PREFIX):] if key.startswith(KEY_PREFIX) else None


# --------------------------------------------------------------------- helpers

def _rng(seed: str) -> random.Random:
    """Deterministic per problem, so the same problem always has the same tests.

    crc32, not hash() — string hashing is salted per process, which would give a
    problem different tests after every restart and quietly invalidate the
    verdicts already recorded against it.
    """
    return random.Random(zlib.crc32(seed.encode()))


def _ints(values) -> str:
    return " ".join(map(str, values))


def _html(statement: str) -> str:
    """Minimal HTML so the UI renders these as nicely as the scraped ones.

    Blank-line separated blocks; a block whose lines are indented becomes a
    <pre>, everything else a paragraph with a bolded first line if it looks like
    a heading.
    """
    out = []
    for block in statement.strip().split("\n\n"):
        lines = block.split("\n")
        if all(ln.startswith("  ") or not ln.strip() for ln in lines):
            out.append(f"<pre>{escape(block)}</pre>")
        elif len(lines) > 1 and lines[0].rstrip().endswith(("Input", "Output",
                                                            "Constraints", "Example")):
            out.append(f'<div class="section-title">{escape(lines[0])}</div>'
                       f"<p>{escape(chr(10).join(lines[1:]))}</p>")
        elif lines[0].rstrip() in ("Input", "Output", "Constraints", "Example",
                                   "Examples", "Note"):
            body = escape("\n".join(lines[1:]))
            out.append(f'<div class="section-title">{escape(lines[0])}</div><p>{body}</p>')
        else:
            out.append(f"<p>{escape(block)}</p>")
    return "\n".join(out)


# --------------------------------------------------------------------- problems

def _t(name: str, stdin: str, expected: str) -> Test:
    return Test(name, stdin, expected)


# 800 — count even numbers. No algorithmic trap; this is the one that teaches the
# loop of "describe it, watch it run".
def _even_tests() -> list[Test]:
    def solve(a): return sum(1 for x in a if x % 2 == 0)
    cases = [
        ("example", [1, 2, 3, 4, 5, 6]),
        ("all_odd", [1, 3, 5]),
        ("all_even", [2, 4, 6, 8]),
        ("single", [7]),
        ("negatives", [-4, -3, 0, 3, 4]),
    ]
    tests = [_t(n, f"{len(a)}\n{_ints(a)}\n", f"{solve(a)}\n") for n, a in cases]
    rng = _rng("count-evens")
    big = [rng.randint(-10**9, 10**9) for _ in range(200_000)]
    tests.append(_t("big_n_2e5", f"{len(big)}\n{_ints(big)}\n", f"{solve(big)}\n"))
    return tests


# 900 — best buy/sell. Naive is "try every pair", which is O(n^2) and dies at 2e5.
def _profit_tests() -> list[Test]:
    def solve(a):
        best, lo = 0, a[0]
        for x in a[1:]:
            best = max(best, x - lo)
            lo = min(lo, x)
        return best
    cases = [
        ("example", [7, 1, 5, 3, 6, 4]),
        ("decreasing", [9, 7, 5, 3]),
        ("increasing", [1, 2, 3, 4]),
        ("two", [3, 10]),
        ("single", [42]),
        ("flat", [5, 5, 5, 5]),
    ]
    tests = [_t(n, f"{len(a)}\n{_ints(a)}\n", f"{solve(a)}\n") for n, a in cases]
    rng = _rng("best-profit")
    big = [rng.randint(0, 10**9) for _ in range(200_000)]
    tests.append(_t("big_n_2e5", f"{len(big)}\n{_ints(big)}\n", f"{solve(big)}\n"))
    return tests


# 1000 — the original calibrated problem: count pairs summing to k.
def _pairs_tests() -> list[Test]:
    def solve(a, k):
        seen: dict[int, int] = {}
        count = 0
        for x in a:
            count += seen.get(k - x, 0)
            seen[x] = seen.get(x, 0) + 1
        return count
    cases = [
        ("example", 6, [1, 2, 3, 4, 5]),
        ("duplicates", 4, [2, 2, 2, 2, 0, 4]),
        ("no_pairs", 100, [1, 2, 3]),
        ("negatives", 0, [-3, 3, -1, 1, 0, 0]),
        ("single", 5, [5]),
    ]
    tests = [_t(n, f"{len(a)} {k}\n{_ints(a)}\n", f"{solve(a, k)}\n")
             for n, k, a in cases]
    rng = _rng("count-pairs")
    big = [rng.randint(1, 1000) for _ in range(1_000_000)]
    tests.append(_t("big_n_1e6", f"{len(big)} 1000\n{_ints(big)}\n",
                    f"{solve(big, 1000)}\n"))
    return tests


# 1100 — most frequent value, smallest on a tie. Naive counts each value by
# rescanning the array: O(n^2).
def _mode_tests() -> list[Test]:
    def solve(a):
        counts: dict[int, int] = {}
        for x in a:
            counts[x] = counts.get(x, 0) + 1
        best = max(counts.values())
        return min(v for v, c in counts.items() if c == best)
    cases = [
        ("example", [3, 1, 3, 2, 1, 3]),
        ("tie_picks_smallest", [5, 5, 2, 2]),
        ("all_distinct", [9, 4, 7]),
        ("single", [1]),
        ("negatives", [-2, -2, 8, 8, -5]),
    ]
    tests = [_t(n, f"{len(a)}\n{_ints(a)}\n", f"{solve(a)}\n") for n, a in cases]
    rng = _rng("most-frequent")
    big = [rng.randint(1, 5000) for _ in range(200_000)]
    tests.append(_t("big_n_2e5", f"{len(big)}\n{_ints(big)}\n", f"{solve(big)}\n"))
    return tests


# 1200 — range sum queries. Naive re-adds the range per query: O(n*q).
def _rangesum_tests() -> list[Test]:
    def solve(a, queries):
        pref = [0]
        for x in a:
            pref.append(pref[-1] + x)
        return [pref[r] - pref[l - 1] for l, r in queries]

    def fmt(a, queries, out):
        lines = [f"{len(a)} {len(queries)}", _ints(a)]
        lines += [f"{l} {r}" for l, r in queries]
        return "\n".join(lines) + "\n", "\n".join(map(str, out)) + "\n"

    cases = [
        ("example", [1, 2, 3, 4, 5], [(1, 3), (2, 5), (4, 4)]),
        ("whole_range", [10, -10, 10], [(1, 3)]),
        ("single_element", [7], [(1, 1), (1, 1)]),
        ("negatives", [-1, -2, -3, -4], [(2, 3), (1, 4)]),
    ]
    tests = []
    for name, a, q in cases:
        stdin, expected = fmt(a, q, solve(a, q))
        tests.append(_t(name, stdin, expected))

    rng = _rng("range-sum")
    n, qn = 200_000, 200_000
    a = [rng.randint(-10**6, 10**6) for _ in range(n)]
    queries = []
    for _ in range(qn):
        l = rng.randint(1, n)
        r = rng.randint(l, n)
        queries.append((l, r))
    stdin, expected = fmt(a, queries, solve(a, queries))
    tests.append(_t("big_n_q_2e5", stdin, expected))
    return tests


# 1300 — subarrays summing to zero, via prefix sums in a hash map. Naive is
# every (start, end) pair: O(n^2).
def _zerosum_tests() -> list[Test]:
    def solve(a):
        seen = {0: 1}
        total = count = 0
        for x in a:
            total += x
            count += seen.get(total, 0)
            seen[total] = seen.get(total, 0) + 1
        return count
    cases = [
        ("example", [1, -1, 2, -2]),
        ("all_zero", [0, 0, 0]),
        ("none", [1, 2, 3]),
        ("single_zero", [0]),
        ("long_cancel", [3, -1, -2, 5, -5]),
    ]
    tests = [_t(n, f"{len(a)}\n{_ints(a)}\n", f"{solve(a)}\n") for n, a in cases]
    rng = _rng("zero-sum")
    big = [rng.choice([-2, -1, 1, 2]) for _ in range(200_000)]
    tests.append(_t("big_n_2e5", f"{len(big)}\n{_ints(big)}\n", f"{solve(big)}\n"))
    return tests


# 1500 — inversions. The naive double loop is the natural description, and it is
# hopeless at 1e5; a merge sort or a Fenwick tree passes.
def _inversion_tests() -> list[Test]:
    def solve(a):
        # merge sort, counting as we go — fast enough to build a 1e5 case
        def sort_count(xs):
            if len(xs) < 2:
                return xs, 0
            mid = len(xs) // 2
            left, cl = sort_count(xs[:mid])
            right, cr = sort_count(xs[mid:])
            merged, c, i, j = [], cl + cr, 0, 0
            while i < len(left) and j < len(right):
                if left[i] <= right[j]:
                    merged.append(left[i]); i += 1
                else:
                    merged.append(right[j]); j += 1
                    c += len(left) - i
            merged += left[i:] + right[j:]
            return merged, c
        return sort_count(list(a))[1]
    cases = [
        ("example", [2, 4, 1, 3, 5]),
        ("sorted", [1, 2, 3, 4]),
        ("reversed", [4, 3, 2, 1]),
        ("duplicates", [2, 2, 1, 1]),
        ("single", [1]),
    ]
    tests = [_t(n, f"{len(a)}\n{_ints(a)}\n", f"{solve(a)}\n") for n, a in cases]
    rng = _rng("inversions")
    big = [rng.randint(1, 10**9) for _ in range(100_000)]
    tests.append(_t("big_n_1e5", f"{len(big)}\n{_ints(big)}\n", f"{solve(big)}\n"))
    return tests


_ARRAY_IO = """\
Standard input:
  - Line 1: one integer. Call it n.
  - Line 2: n integers, separated by spaces.

Standard output:
  - Write the program's result to standard output.
"""

_SPECS: list[Spec] = [
    Spec(
        id="count-evens", name="Count the Even Numbers", rating=800,
        tags=["implementation", "arrays"], time_limit_ms=4000,
        statement="""\
Count the Even Numbers

You are given a list of n whole numbers. Count how many of them are even
(that is, exactly divisible by 2). Zero counts as even, and negative numbers
can be even too.

Input
  Line 1: one integer n, the length of the list.
  Line 2: n integers, separated by spaces.

Output
  A single integer: how many of the n numbers are even.

Constraints
  1 <= n <= 200000
  -1000000000 <= each number <= 1000000000

Example
  Input:
    6
    1 2 3 4 5 6
  Output:
    3
""",
        io_format=_ARRAY_IO, build_tests=_even_tests,
    ),
    Spec(
        id="best-profit", name="Best Buy and Sell", rating=900,
        tags=["arrays", "greedy"], time_limit_ms=4000,
        statement="""\
Best Buy and Sell

You are given the price of one item on each of n consecutive days. You may buy
on one day and sell on a strictly later day, at most once. Report the largest
profit you could make. If every possible trade would lose money, report 0
(you are allowed to simply not trade).

Input
  Line 1: one integer n, the number of days.
  Line 2: n integers, the price on each day, in order.

Output
  A single integer: the largest achievable profit, or 0.

Constraints
  1 <= n <= 200000
  0 <= each price <= 1000000000

Example
  Input:
    6
    7 1 5 3 6 4
  Output:
    5
  Buy on day 2 at price 1, sell on day 5 at price 6.
""",
        io_format=_ARRAY_IO, build_tests=_profit_tests,
    ),
    Spec(
        id="count-pairs", name="Count Pairs With Sum K", rating=1000,
        tags=["arrays", "hashing", "two-pointers"], time_limit_ms=5000,
        statement="""\
Count Pairs With Sum K

You are given a list of n numbers and a target K. Count the pairs of positions
(i, j) with i < j whose two numbers add up to exactly K. Two positions count as
a different pair even if they hold the same value.

Input
  Line 1: two integers n and K, separated by a space.
  Line 2: n integers, separated by spaces.

Output
  A single integer: the number of pairs that add up to K.

Constraints
  1 <= n <= 1000000
  -1000000000 <= each number, K <= 1000000000

Example
  Input:
    5 6
    1 2 3 4 5
  Output:
    2
  The pairs are 1+5 and 2+4.
""",
        io_format="""\
Standard input:
  - Line 1: two integers, separated by a space. Call them n and k.
  - Line 2: n integers, separated by spaces.

Standard output:
  - Write the program's result to standard output.
""",
        build_tests=_pairs_tests,
    ),
    Spec(
        id="most-frequent", name="The Most Common Value", rating=1100,
        tags=["arrays", "hashing", "counting"], time_limit_ms=4000,
        statement="""\
The Most Common Value

You are given a list of n numbers. Find the value that appears the most times.
If several values tie for the most appearances, report the smallest of them.

Input
  Line 1: one integer n, the length of the list.
  Line 2: n integers, separated by spaces.

Output
  A single integer: the most common value, smallest one in case of a tie.

Constraints
  1 <= n <= 200000
  -1000000000 <= each number <= 1000000000

Example
  Input:
    6
    3 1 3 2 1 3
  Output:
    3
  The value 3 appears three times, more than any other.
""",
        io_format=_ARRAY_IO, build_tests=_mode_tests,
    ),
    Spec(
        id="range-sum", name="Adding Up Stretches", rating=1200,
        tags=["prefix sums", "arrays"], time_limit_ms=4000,
        statement="""\
Adding Up Stretches

You are given a list of n numbers, and then q separate questions. Each question
gives two positions l and r (counting from 1, with l <= r) and asks for the sum
of the numbers from position l to position r, inclusive.

Answer every question, one per line, in the order they are asked.

Input
  Line 1: two integers n and q, separated by a space.
  Line 2: n integers, separated by spaces.
  Then q lines follow, each with two integers l and r.

Output
  q lines. Line i holds the answer to question i.

Constraints
  1 <= n <= 200000
  1 <= q <= 200000
  1 <= l <= r <= n
  -1000000 <= each number <= 1000000

Example
  Input:
    5 3
    1 2 3 4 5
    1 3
    2 5
    4 4
  Output:
    6
    14
    4
""",
        io_format="""\
Standard input:
  - Line 1: two integers, separated by a space. Call them n and q.
  - Line 2: n integers, separated by spaces.
  - Then q further lines, each containing two integers separated by a space.

Standard output:
  - Write one result per line, in the order the q lines were given.
""",
        build_tests=_rangesum_tests,
    ),
    Spec(
        id="zero-sum", name="Stretches That Cancel Out", rating=1300,
        tags=["prefix sums", "hashing"], time_limit_ms=4000,
        statement="""\
Stretches That Cancel Out

You are given a list of n numbers. Count how many stretches of consecutive
numbers add up to exactly zero. A stretch is any run of one or more numbers
sitting next to each other in the list; two stretches are different if they
start or end at different positions, even if they contain the same values.

Input
  Line 1: one integer n, the length of the list.
  Line 2: n integers, separated by spaces.

Output
  A single integer: how many consecutive stretches sum to zero.

Constraints
  1 <= n <= 200000
  -1000000000 <= each number <= 1000000000

Example
  Input:
    4
    1 -1 2 -2
  Output:
    3
  The stretches are [1,-1], [2,-2] and [1,-1,2,-2].
""",
        io_format=_ARRAY_IO, build_tests=_zerosum_tests,
    ),
    Spec(
        id="inversions", name="Out-of-Order Pairs", rating=1500,
        tags=["sorting", "divide and conquer", "data structures"],
        time_limit_ms=3000,
        statement="""\
Out-of-Order Pairs

You are given a list of n numbers. Count the pairs of positions (i, j) with
i < j where the earlier number is strictly larger than the later one — that is,
the pairs that are in the wrong order relative to each other.

Input
  Line 1: one integer n, the length of the list.
  Line 2: n integers, separated by spaces.

Output
  A single integer: how many pairs are out of order.

Constraints
  1 <= n <= 100000
  1 <= each number <= 1000000000

Example
  Input:
    5
    2 4 1 3 5
  Output:
    3
  The out-of-order pairs are (2,1), (4,1) and (4,3).
""",
        io_format=_ARRAY_IO, build_tests=_inversion_tests,
    ),
]

_BY_ID = {s.id: s for s in _SPECS}

# Built problems carry ~1 MB of generated tests each, so keep them once built.
_cache: dict[str, Problem] = {}


def _build(spec: Spec) -> Problem:
    if spec.id not in _cache:
        _cache[spec.id] = Problem(
            name=spec.name,
            statement=spec.statement,
            io_format=spec.io_format,
            tags=list(spec.tags),
            tests=spec.build_tests(),
            time_limit_ms=spec.time_limit_ms,
            memory_limit_mb=spec.memory_limit_mb,
            url=key_of(spec),          # doubles as the memory key; not a real URL
            rating=spec.rating,
            source="offline bank",
            statement_html=_html(spec.statement),
        )
    return _cache[spec.id]


def get(problem_id: str) -> Problem | None:
    spec = _BY_ID.get(problem_id)
    return _build(spec) if spec else None


def catalogue() -> list[dict]:
    """Metadata only — no test generation."""
    return [{"key": key_of(s), "id": s.id, "name": s.name, "rating": s.rating,
             "tags": s.tags} for s in _SPECS]


def random_problem(exclude_keys: set[str] | None = None,
                   min_rating: int = 0, max_rating: int = 10_000,
                   rng: random.Random | None = None) -> Problem:
    """Pick a problem in the band, preferring one the learner hasn't seen.

    Mirrors `codeforces.random_problem` so `state.py` can treat them alike.

    Order of preference, and the reason for it: an unseen problem outside the
    band beats a repeat inside it. The bank is small and the default band is
    narrow, so band-first-always would hand a learner the same two problems
    forever — being shown a problem you already solved reads as a bug, while
    being shown one a little off your level does not.
    """
    exclude = exclude_keys or set()
    rng = rng or random
    target = (min_rating + max_rating) // 2

    in_band = [s for s in _SPECS if min_rating <= s.rating <= max_rating]

    unseen_in_band = [s for s in in_band if key_of(s) not in exclude]
    if unseen_in_band:
        return _build(rng.choice(unseen_in_band))

    unseen_anywhere = [s for s in _SPECS if key_of(s) not in exclude]
    if unseen_anywhere:                          # nearest unseen, rather than a repeat
        return _build(min(unseen_anywhere, key=lambda s: abs(s.rating - target)))

    if in_band:                                  # everything seen: repeat, in band
        return _build(rng.choice(in_band))
    return _build(min(_SPECS, key=lambda s: abs(s.rating - target)))
