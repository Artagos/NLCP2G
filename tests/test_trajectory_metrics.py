"""The trajectory adapter and the four metrics, against hand-computed values.

Hand-computed, not recorded from the implementation. A fixture captured from the
code asserts only that it still does what it did — which is exactly what a wrong
scorer also does, and the whole reason these numbers are worth anything is that
somebody worked them out independently.

Nothing here needs MLflow, a tracking server or a network: a `TraceView` is a
plain dataclass, so a trace can be built in six lines.
"""
from __future__ import annotations

import pytest

from eval.agent import metrics, store, trajectory
from eval.agent.scenario import Scenario


def span(span_id, name, span_type, start, *, parent=None, attributes=None,
         inputs=None, outputs=None):
    return store.Span(span_id=span_id, parent_id=parent, name=name,
                      span_type=span_type, start_ns=start, end_ns=start + 1000,
                      attributes=attributes or {}, inputs=inputs,
                      outputs=outputs)


def trace(*spans, tags=None):
    return store.TraceView(trace_id="tr-test", tags=tags or {},
                           spans=list(spans))


def scenario(accepted, arguments=None, category="concept"):
    return Scenario(id="s", category=category, task="t", outcome="o",
                    accepted=accepted, arguments=arguments or {})


# ------------------------------------------------------------- the adapter --

def test_tool_calls_come_back_in_the_order_they_started():
    """Storage order is not event order; the trajectory is the second one."""
    t = trace(
        span("c", "search_corpus", store.TOOL, 300,
             attributes={"gen_ai.tool.name": "search_corpus"}, inputs={"query": "c"}),
        span("a", "retrieve_memory", store.TOOL, 100,
             attributes={"gen_ai.tool.name": "retrieve_memory"}, inputs={"query": "a"}),
        span("b", "read_problem_notes", store.TOOL, 200,
             attributes={"gen_ai.tool.name": "read_problem_notes"}, inputs={}),
    )
    assert trajectory.tool_names(t) == [
        "retrieve_memory", "read_problem_notes", "search_corpus"]


def test_the_tool_name_falls_back_to_the_span_name():
    """MLflow 3.15 does not populate gen_ai.tool.name; the span name carries it.

    `backend/tracing.py` sets the conventional key for spans we control, but a
    trace captured before that, or opened by a future framework, has only the
    name — and the adapter must still work.
    """
    t = trace(span("a", "search_corpus", store.TOOL, 1, inputs={"query": "x"}))
    assert trajectory.tool_names(t) == ["search_corpus"]


def test_injected_parameters_are_not_arguments():
    """LangGraph fills state and tool_call_id in; the model never chose them."""
    t = trace(span("a", "retrieve_memory", store.TOOL, 1, inputs={
        "query": "recursion", "state": {"uid": "bob"}, "tool_call_id": "c1"}))
    assert trajectory.tool_calls(t)[0].arguments == {"query": "recursion"}


def test_the_trajectory_never_reads_a_span_output():
    """The assignment's rule: arguments, never results.

    A trajectory is what the agent DECIDED to do. Folding in what came back
    would score the corpus and the memory store instead, and two runs that made
    identical decisions would diverge because one search returned more. The
    outputs here are booby-trapped: if anything in the adapter reads one, it
    lands in the trajectory and this fails.
    """
    t = trace(span("a", "search_corpus", store.TOOL, 1,
                   inputs={"query": "binary search"},
                   outputs={"query": "POISON", "tool": "POISON",
                            "arguments": {"query": "POISON"}}))
    calls = trajectory.tool_calls(t)
    assert calls == [trajectory.ToolCall("search_corpus", {"query": "binary search"})]
    assert "POISON" not in repr(calls)


def test_a_trace_survives_a_round_trip_through_the_committed_file(tmp_path):
    t = trace(span("a", "search_corpus", store.TOOL, 1,
                   attributes={"gen_ai.tool.name": "search_corpus"},
                   inputs={"query": "x"}),
              tags={"eval_case_id": "s#1", "request_origin": "batch"})
    path = str(tmp_path / "t.jsonl")
    store.export(path, [t])
    back = store.load(path)[0]
    assert back.to_dict() == t.to_dict()
    assert back.case_id == "s#1" and back.origin == "batch"


def test_token_usage_sums_over_chat_model_spans():
    """LangChain emits CHAT_MODEL, never LLM. Filtering on LLM finds nothing."""
    usage = {"input_tokens": 10, "output_tokens": 4, "total_tokens": 14}
    t = trace(
        span("a", "m1", store.CHAT_MODEL, 1,
             attributes={"mlflow.chat.tokenUsage": usage,
                         "mlflow.llm.model": "gemini-2.5-flash"}),
        span("b", "m2", store.CHAT_MODEL, 2,
             attributes={"mlflow.chat.tokenUsage": usage,
                         "mlflow.llm.model": "gemini-2.5-flash-lite"}),
    )
    assert t.tokens == {"input": 20, "output": 8, "total": 28}
    assert t.models == ["gemini-2.5-flash", "gemini-2.5-flash-lite"]


# -------------------------------------------------------------- the metrics --

def test_tool_selection_is_a_multiset_not_a_sequence():
    """Which tools is selection; the order is trajectory precision's business."""
    accepted = [["retrieve_memory", "search_corpus"]]
    assert metrics.tool_selection(["retrieve_memory", "search_corpus"], accepted) == 1.0
    assert metrics.tool_selection(["search_corpus", "retrieve_memory"], accepted) == 1.0
    assert metrics.tool_selection(["search_corpus"], accepted) == 0.0
    assert metrics.tool_selection(
        ["search_corpus", "search_corpus", "retrieve_memory"], accepted) == 0.0


def test_the_refusal_case_passes_only_when_nothing_was_called():
    assert metrics.tool_selection([], [[]]) == 1.0
    assert metrics.tool_selection(["search_corpus"], [[]]) == 0.0


@pytest.mark.parametrize("actual,expected,lcs", [
    (["a", "b", "c"], ["a", "b", "c"], 3),
    (["a", "b"], ["b", "a"], 1),          # order costs: only one in sequence
    (["a", "x", "b"], ["a", "b"], 2),     # an extra in the middle is skipped
    ([], ["a"], 0),
])
def test_longest_common_subsequence(actual, expected, lcs):
    assert metrics._lcs(actual, expected) == lcs


def test_trajectory_precision_and_recall_by_hand():
    """actual = [s, x, s], expected = [s, s]  ->  LCS 2, P 2/3, R 2/2."""
    accepted = [["search_corpus", "search_corpus"]]
    actual = ["search_corpus", "retrieve_memory", "search_corpus"]
    assert metrics.trajectory_precision(actual, accepted) == pytest.approx(2 / 3)
    assert metrics.trajectory_recall(actual, accepted) == 1.0


def test_precision_and_recall_are_undefined_rather_than_zero_on_a_refusal():
    """Nothing was wanted and nothing was called. Recall has no denominator."""
    assert metrics.trajectory_recall([], [[]]) is None
    assert metrics.trajectory_precision([], [[]]) == 1.0
    # something was wanted but nothing happened: recall catches it, precision
    # has nothing to judge
    assert metrics.trajectory_recall([], [["search_corpus"]]) == 0.0
    assert metrics.trajectory_precision([], [["search_corpus"]]) is None


def test_scoring_uses_the_most_favourable_acceptable_alternative():
    """A model must not be punished for picking the alternative listed second."""
    accepted = [["search_corpus"], ["retrieve_memory", "search_corpus"]]
    actual = ["retrieve_memory", "search_corpus"]
    assert metrics.best_alternative(actual, accepted) == ["retrieve_memory",
                                                          "search_corpus"]
    assert metrics.trajectory_precision(actual, accepted) == 1.0


def test_an_omitted_optional_argument_is_not_a_failure():
    """search_corpus(query, k=5): leaving k alone is the correct call.

    The first version of this scored every well-formed search zero, because the
    model sensibly omitted `k` and `between` was applied to None.
    """
    s = scenario([["search_corpus"]],
                 {"search_corpus": {"query": {"includes_any": ["bound"]},
                                    "k": {"between": [1, 10]}}})
    calls = [trajectory.ToolCall("search_corpus", {"query": "lower_bound"})]
    assert metrics.tool_parameters(calls, s) == 1.0


def test_a_present_but_wrong_argument_is_a_failure():
    s = scenario([["search_corpus"]],
                 {"search_corpus": {"k": {"between": [1, 10]}}})
    calls = [trajectory.ToolCall("search_corpus", {"query": "x", "k": 500})]
    assert metrics.tool_parameters(calls, s) == 0.0


def test_tool_parameters_is_undefined_when_there_is_nothing_to_check():
    s = scenario([["search_corpus"]], {"search_corpus": {"query": {"includes_any": ["x"]}}})
    assert metrics.tool_parameters([], s) is None
    assert metrics.tool_parameters(
        [trajectory.ToolCall("retrieve_memory", {})], s) is None


def test_undefined_values_are_excluded_from_the_mean_and_counted():
    scores = [
        metrics.RunScores(1.0, None, 1.0, 1.0, None),
        metrics.RunScores(0.0, 0.5, 1.0, 1.0, 1.0),
    ]
    agg = metrics.aggregate(scores)
    assert agg.means["tool_selection"] == 0.5
    assert agg.means["tool_parameters"] == 0.5      # the None is not a zero
    assert agg.undefined["tool_parameters"] == 1
    assert agg.undefined["trajectory_recall"] == 1


# ------------------------------------------------------------- pass rates --

def _run(passed: bool) -> metrics.RunScores:
    return metrics.RunScores(1.0 if passed else 0.0, None,
                             1.0 if passed else 0.0, None, None)


def test_the_three_pass_rates_differ_and_flakiness_is_named():
    """Three scenarios: all-pass, flaky, all-fail.

    pass@1 = 4 passing runs of 9         = 0.444
    pass@3 = 2 scenarios ever passed / 3 = 0.667
    pass^3 = 1 scenario always passed / 3 = 0.333
    """
    by_scenario = {
        "always": [_run(True), _run(True), _run(True)],
        "flaky": [_run(True), _run(False), _run(False)],
        "never": [_run(False), _run(False), _run(False)],
    }
    rates = metrics.pass_rates(by_scenario)
    assert rates.pass_at_1 == pytest.approx(4 / 9)
    assert rates.pass_at_3 == pytest.approx(2 / 3)
    assert rates.pass_hat_3 == pytest.approx(1 / 3)
    assert rates.flaky == ["flaky"]
    assert rates.runs_per_scenario == 3


def test_pass_at_3_hides_what_pass_hat_3_reveals():
    """The reason all three are reported: on this data they disagree sharply."""
    by_scenario = {f"s{i}": [_run(True), _run(False), _run(False)]
                   for i in range(4)}
    rates = metrics.pass_rates(by_scenario)
    assert rates.pass_at_3 == 1.0      # "it always worked at least once"
    assert rates.pass_hat_3 == 0.0     # it never worked reliably, not once
