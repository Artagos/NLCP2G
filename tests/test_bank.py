"""The offline problem bank.

The important test here is `test_expected_outputs_match_a_brute_force`. Every
problem's expected output comes from a reference solution I wrote, and a wrong
reference is the worst possible bug in this product: the learner describes a
correct approach, the sandbox says WA, and they are told their thinking is wrong
when it wasn't. So each reference is checked against a second, deliberately dumb
implementation on the small cases.
"""
from __future__ import annotations

import pytest

from backend import bank

ALL_IDS = [s.id for s in bank._SPECS]


@pytest.fixture(scope="module")
def built():
    """Build every problem once — the generated big cases are megabytes."""
    return {pid: bank.get(pid) for pid in ALL_IDS}


# --------------------------------------------------------------- brute forces

def _parse_array(stdin):
    lines = stdin.split("\n")
    return [int(x) for x in lines[1].split()]


def _bf_count_evens(stdin):
    return str(sum(1 for x in _parse_array(stdin) if x % 2 == 0))


def _bf_best_profit(stdin):
    a = _parse_array(stdin)
    best = 0
    for i in range(len(a)):
        for j in range(i + 1, len(a)):
            best = max(best, a[j] - a[i])
    return str(best)


def _bf_count_pairs(stdin):
    lines = stdin.split("\n")
    k = int(lines[0].split()[1])
    a = [int(x) for x in lines[1].split()]
    return str(sum(1 for i in range(len(a)) for j in range(i + 1, len(a))
                   if a[i] + a[j] == k))


def _bf_most_frequent(stdin):
    a = _parse_array(stdin)
    best_val, best_count = None, -1
    for v in sorted(set(a)):
        c = a.count(v)
        if c > best_count:
            best_val, best_count = v, c
    return str(best_val)


def _bf_range_sum(stdin):
    lines = [ln for ln in stdin.split("\n") if ln.strip()]
    n, q = map(int, lines[0].split())
    a = [int(x) for x in lines[1].split()]
    out = []
    for line in lines[2:2 + q]:
        l, r = map(int, line.split())
        out.append(str(sum(a[l - 1:r])))     # re-add the range every time
    return "\n".join(out)


def _bf_zero_sum(stdin):
    a = _parse_array(stdin)
    count = 0
    for i in range(len(a)):
        total = 0
        for j in range(i, len(a)):
            total += a[j]
            if total == 0:
                count += 1
    return str(count)


def _bf_inversions(stdin):
    a = _parse_array(stdin)
    return str(sum(1 for i in range(len(a)) for j in range(i + 1, len(a))
                   if a[i] > a[j]))


BRUTE = {
    "count-evens": _bf_count_evens,
    "best-profit": _bf_best_profit,
    "count-pairs": _bf_count_pairs,
    "most-frequent": _bf_most_frequent,
    "range-sum": _bf_range_sum,
    "zero-sum": _bf_zero_sum,
    "inversions": _bf_inversions,
}


@pytest.mark.parametrize("pid", ALL_IDS)
def test_expected_outputs_match_a_brute_force(pid, built):
    """Independent check of every reference solution on the small cases."""
    brute = BRUTE[pid]
    checked = 0
    for test in built[pid].tests:
        if len(test.stdin) > 50_000:      # brute force can't do the stress case
            continue
        assert brute(test.stdin) == test.expected_stdout.strip(), (
            f"{pid}/{test.name}: reference says {test.expected_stdout.strip()!r}, "
            f"brute force says {brute(test.stdin)!r}"
        )
        checked += 1
    assert checked >= 4, f"{pid}: only {checked} small cases to verify against"


# ------------------------------------------------------------------ structure

@pytest.mark.parametrize("pid", ALL_IDS)
def test_every_problem_is_well_formed(pid, built):
    p = built[pid]
    assert p.name and p.statement and p.io_format
    assert p.rating and 800 <= p.rating <= 3500
    assert p.tags and p.tests
    assert p.statement_html and "<p>" in p.statement_html
    assert p.source == "offline bank"
    for t in p.tests:
        assert t.stdin.endswith("\n") and t.expected_stdout.endswith("\n")


@pytest.mark.parametrize("pid", ALL_IDS)
def test_every_problem_carries_a_stress_case(pid, built):
    """Without a big input, a hopeless approach passes and the learner is
    told their O(n^2) idea worked — the exact failure this bank exists to fix."""
    biggest = max(len(t.stdin) for t in built[pid].tests)
    assert biggest > 400_000, f"{pid}: largest input is only {biggest} bytes"


def test_the_blind_io_format_never_leaks_the_task(built):
    """The screener and translator see io_format and nothing else. It must
    describe the bytes, not the problem."""
    banned = ["count", "sum", "pair", "profit", "invert", "frequent", "zero",
              "buy", "sell", "even"]
    for pid, p in built.items():
        low = p.io_format.lower()
        leaked = [w for w in banned if w in low]
        assert not leaked, f"{pid}: io_format mentions {leaked}"


def test_ids_and_keys_round_trip():
    for spec in bank._SPECS:
        key = bank.key_of(spec)
        assert key.startswith("bank:")
        assert bank.parse_key(key) == spec.id
    assert bank.parse_key("https://codeforces.com/problemset/problem/4/A") is None
    assert len({s.id for s in bank._SPECS}) == len(bank._SPECS)


def test_ratings_cover_a_usable_spread():
    ratings = sorted(s.rating for s in bank._SPECS)
    assert ratings[0] <= 800 and ratings[-1] >= 1500
    assert len(set(ratings)) >= 5      # enough steps for easier/harder to mean something


def test_tests_are_stable_across_rebuilds():
    """A restart must not regenerate different inputs — verdicts already
    recorded against a problem would silently stop lining up."""
    first = [t.stdin[:200] for t in bank._even_tests()]
    second = [t.stdin[:200] for t in bank._even_tests()]
    assert first == second


# ------------------------------------------------------------------ selection

def test_selection_respects_the_rating_band():
    for _ in range(20):
        p = bank.random_problem(min_rating=1000, max_rating=1300)
        assert 1000 <= p.rating <= 1300


def test_selection_prefers_unseen_problems():
    seen = {bank.key_of(s) for s in bank._SPECS if s.rating <= 1100}
    for _ in range(20):
        p = bank.random_problem(exclude_keys=seen, min_rating=800, max_rating=1100)
        # every problem in the band is excluded, so it may repeat — but with a
        # wider band it must pick something unseen
        assert p.rating is not None
    for _ in range(20):
        p = bank.random_problem(exclude_keys=seen, min_rating=800, max_rating=1500)
        assert bank.key_of(p) not in seen


def test_an_empty_band_widens_instead_of_failing():
    p = bank.random_problem(min_rating=3000, max_rating=3500)
    assert p is not None and p.rating == 1500       # the closest one we have


def test_seen_everything_still_returns_a_problem():
    everything = {bank.key_of(s) for s in bank._SPECS}
    p = bank.random_problem(exclude_keys=everything, min_rating=800, max_rating=1500)
    assert p is not None


# ------------------------------------------------- integration with selection

@pytest.fixture
def offline(monkeypatch):
    from backend import codeforces, state
    monkeypatch.setattr(codeforces, "random_problem", lambda **kw: (_ for _ in ()).throw(
        RuntimeError("codeforces is blocked")))
    state._cache.clear()
    return state


def test_a_blocked_codeforces_falls_back_to_the_bank(offline):
    p = offline.load_new("u:learner")
    assert p.source == "offline bank"
    assert p.url.startswith("bank:")
    assert len(p.tests) >= 5


def test_the_learner_is_not_shown_the_same_problem_twice(offline):
    from backend import memory
    seen = set()
    for _ in range(len(bank._SPECS)):
        p = offline.load_new("u:learner")
        assert p.url not in seen, "repeated a problem the learner had already seen"
        seen.add(p.url)
        memory.set_current("u:learner", p.url, p.name, p.rating, None, None)
    assert len(seen) == len(bank._SPECS)


def test_harder_and_easier_actually_move_the_rating(offline):
    """With one fallback problem this was a no-op; the bank makes it mean something."""
    offline.load_new("u:learner")                      # something in the default band
    harder = offline.load_new("u:learner", rating_delta=+400)
    assert harder.rating >= 1200
    easier = offline.load_new("u:learner", rating_delta=-400)
    assert easier.rating <= 1100


def test_a_bank_problem_is_restored_after_a_restart(offline):
    p = offline.load_new("u:learner")
    offline._cache.clear()                              # simulate a process restart
    restored = offline.current("u:learner")
    assert restored.url == p.url and restored.name == p.name
    # and the regenerated tests are byte-identical, so recorded verdicts still hold
    assert [t.stdin[:120] for t in restored.tests] == [t.stdin[:120] for t in p.tests]
