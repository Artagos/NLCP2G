"""Trajectory metrics, and the three ways of counting three runs.

Four metrics, written out rather than imported, because no library ships this
family in a form that knows what an "acceptable alternative trajectory" is.

    tool selection accuracy   did it reach for the right tools at all
    tool parameter accuracy   were the arguments sane
    goal completion rate      did the reply do the job (judged; see scorers.py)
    trajectory precision/recall  did it call them in a sensible order, and all of them

Two conventions carried over from the retrieval metrics in `eval/metrics/rank.py`,
because they were right there and are right here:

**Undefined is not zero.** A scenario whose only acceptable trajectory is the
empty one — the R1 refusal, where `graphs/turn.py::_refuse` never reaches a
model — has nothing for parameter accuracy or recall to divide by. Scoring those
zero would drag the averages down in proportion to how many honest refusal cases
the set contains, which punishes the eval set for testing the guardrail. They
are excluded and counted separately.

**Score against the most favourable reading.** When several trajectories are
acceptable, precision and recall are computed against whichever one the run
matched best. Anything else would penalise a model for picking the acceptable
alternative the author happened to list second.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

from .scenario import Scenario
from .trajectory import ToolCall

METRIC_NAMES = ["tool_selection", "tool_parameters", "goal_completion",
                "trajectory_precision", "trajectory_recall"]


@dataclass(frozen=True)
class RunScores:
    """One run of one scenario. Any field may be None, meaning undefined."""

    tool_selection: float | None
    tool_parameters: float | None
    goal_completion: float | None
    trajectory_precision: float | None
    trajectory_recall: float | None

    @property
    def passed(self) -> bool:
        """The pass predicate, stated once so every count agrees on it.

        A run passes when it reached for the right tools AND the reply did the
        job. Argument accuracy and trajectory order are reported but deliberately
        not part of the predicate: a model that searched for "binary search"
        instead of "lower_bound" and still explained it correctly has not failed
        the learner, and a pass rate that says otherwise measures the eval set's
        taste rather than the agent's competence.
        """
        return self.tool_selection == 1.0 and self.goal_completion == 1.0

    def as_dict(self) -> dict[str, float | None]:
        return {name: getattr(self, name) for name in METRIC_NAMES}


# --------------------------------------------------------------- predicates --

MISSING = object()


def check(value, constraint: dict) -> bool:
    """Does one argument value satisfy one declarative constraint?

    The vocabulary is fixed in `scenario.PREDICATES` and validated at load time,
    so an unknown key never reaches here.

    An argument the model did not pass at all satisfies every constraint. That
    is not leniency: `search_corpus(query, k=5)` has a default, so omitting `k`
    is the *correct* call and the tool's own signature already rejects a missing
    required argument before a span is ever opened. The first version of this
    failed every well-formed search because the model sensibly left `k` alone,
    which measured the eval set's assumptions rather than the agent.
    """
    if value is MISSING:
        return True
    text = str(value if value is not None else "").lower()
    for predicate, wanted in constraint.items():
        if predicate == "includes_any":
            if not any(str(w).lower() in text for w in wanted):
                return False
        elif predicate == "includes_all":
            if not all(str(w).lower() in text for w in wanted):
                return False
        elif predicate == "excludes":
            if any(str(w).lower() in text for w in wanted):
                return False
        elif predicate == "equals":
            if value != wanted:
                return False
        elif predicate == "between":
            low, high = wanted
            try:
                number = float(value)
            except (TypeError, ValueError):
                return False
            if not (low <= number <= high):
                return False
    return True


# ----------------------------------------------------------------- matching --

def _lcs(left: list[str], right: list[str]) -> int:
    """Length of the longest common subsequence of two tool-name sequences.

    Subsequence rather than set intersection because order carries meaning: a
    run that reads the notes and then searches the corpus did something
    different from one that searched first and then read. Subsequence rather
    than exact match because an extra call in the middle should cost a little,
    not everything.
    """
    if not left or not right:
        return 0
    table = [[0] * (len(right) + 1) for _ in range(len(left) + 1)]
    for i, a in enumerate(left, start=1):
        for j, b in enumerate(right, start=1):
            table[i][j] = (table[i - 1][j - 1] + 1 if a == b
                           else max(table[i - 1][j], table[i][j - 1]))
    return table[len(left)][len(right)]


def best_alternative(actual: list[str], accepted: list[list[str]]) -> list[str]:
    """The acceptable trajectory this run matched most closely.

    Ranked by overlap first and by closeness in length second, so a run that
    called one tool is scored against the one-tool alternative rather than
    against a longer one that happens to contain it.
    """
    return max(accepted,
               key=lambda alt: (_lcs(actual, alt), -abs(len(alt) - len(actual))))


# ------------------------------------------------------------------ metrics --

def tool_selection(actual: list[str], accepted: list[list[str]]) -> float:
    """1.0 when the multiset of tools called is one of the accepted sets.

    A multiset, not a sequence: *which* tools were reached for is selection, and
    the order they came in is what trajectory precision and recall measure. This
    is also the metric that carries the refusal scenarios, where the only
    accepted set is the empty one and any tool call at all is a failure.
    """
    counted = Counter(actual)
    return 1.0 if any(counted == Counter(alt) for alt in accepted) else 0.0


def tool_parameters(calls: list[ToolCall], scenario: Scenario) -> float | None:
    """Fraction of calls whose arguments satisfy the scenario's predicates.

    Undefined — not zero — when the scenario declares no argument expectations
    or when nothing it constrains was called. There is a real difference between
    "every argument was wrong" and "there were no arguments to check", and a
    metric that renders both as 0.000 hides it.
    """
    if not scenario.arguments:
        return None
    checked = [c for c in calls if c.tool in scenario.arguments]
    if not checked:
        return None

    good = 0
    for call in checked:
        expectations = scenario.arguments[call.tool]
        if all(check(call.arguments.get(arg, MISSING), constraint)
               for arg, constraint in expectations.items()):
            good += 1
    return good / len(checked)


def trajectory_precision(actual: list[str],
                         accepted: list[list[str]]) -> float | None:
    """Of the calls made, how many belonged. Undefined when nothing was called.

    A run that called nothing when nothing was wanted scores 1.0 — it made no
    unwanted call. A run that called nothing when something was wanted is
    undefined here and caught by recall, which is 0.0 for it.
    """
    expected = best_alternative(actual, accepted)
    if not actual:
        return 1.0 if not expected else None
    return _lcs(actual, expected) / len(actual)


def trajectory_recall(actual: list[str],
                      accepted: list[list[str]]) -> float | None:
    """Of the calls wanted, how many were made. Undefined when none were wanted."""
    expected = best_alternative(actual, accepted)
    if not expected:
        return None
    return _lcs(actual, expected) / len(expected)


def score_run(calls: list[ToolCall], scenario: Scenario,
              goal_completion: float | None) -> RunScores:
    """Every metric for one run. `goal_completion` is judged elsewhere."""
    actual = [c.tool for c in calls]
    return RunScores(
        tool_selection=tool_selection(actual, scenario.accepted),
        tool_parameters=tool_parameters(calls, scenario),
        goal_completion=goal_completion,
        trajectory_precision=trajectory_precision(actual, scenario.accepted),
        trajectory_recall=trajectory_recall(actual, scenario.accepted),
    )


# -------------------------------------------------------------- aggregation --

@dataclass(frozen=True)
class Aggregate:
    means: dict[str, float | None]
    undefined: dict[str, int]
    runs: int


def aggregate(scores: list[RunScores]) -> Aggregate:
    """Mean over the defined values per metric, with the undefined count kept."""
    means: dict[str, float | None] = {}
    undefined: dict[str, int] = {}
    for name in METRIC_NAMES:
        values = [getattr(s, name) for s in scores]
        defined = [v for v in values if v is not None]
        undefined[name] = len(values) - len(defined)
        means[name] = sum(defined) / len(defined) if defined else None
    return Aggregate(means=means, undefined=undefined, runs=len(scores))


@dataclass(frozen=True)
class PassRates:
    """The three ways of counting the same runs.

    `pass_at_1` is the plain per-run success rate. `pass_at_3` asks whether the
    agent EVER got it right, which at n=3 collapses to almost nothing on its own
    — a scenario it fails two times in three still scores 1.0. `pass_hat_3` asks
    whether it got it right EVERY time, and that is the one that says whether
    the agent is reliable enough to put in front of a learner.

    `flaky` names the scenarios that did both, which is the point of running
    three times at all.
    """

    pass_at_1: float
    pass_at_3: float
    pass_hat_3: float
    flaky: list[str]
    runs_per_scenario: int


def pass_rates(by_scenario: dict[str, list[RunScores]]) -> PassRates:
    total_runs = sum(len(runs) for runs in by_scenario.values())
    passed_runs = sum(1 for runs in by_scenario.values() for r in runs if r.passed)

    ever = sum(1 for runs in by_scenario.values() if any(r.passed for r in runs))
    always = sum(1 for runs in by_scenario.values()
                 if runs and all(r.passed for r in runs))
    flaky = sorted(sid for sid, runs in by_scenario.items()
                   if any(r.passed for r in runs) and not all(r.passed for r in runs))

    scenarios = len(by_scenario) or 1
    counts = {len(runs) for runs in by_scenario.values()} or {0}
    return PassRates(
        pass_at_1=passed_runs / (total_runs or 1),
        pass_at_3=ever / scenarios,
        pass_hat_3=always / scenarios,
        flaky=flaky,
        runs_per_scenario=max(counts),
    )
