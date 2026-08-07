"""The judged scorers, driven offline by a scripted model.

A scorer nobody tested is a number nobody should trust — and these are the
numbers the report leads with. What is testable without a model is everything
around the judgement: how verdicts become a fraction, what happens when the
judge answers with the wrong number of verdicts, and which inputs make a metric
undefined rather than zero. That is where the bugs would be, because the model
call itself is one line.

The seam is `llm.chat_model`, which is what the rest of this suite already
stubs. Using it here rather than a library's own client is a large part of why
the scorers are hand-rolled at all.

Two real cases from `eval/dataset/cases.yaml` serve as the fixture, so the shapes
being tested are the shapes the harness actually passes.
"""
from __future__ import annotations

import pytest

from backend import llm
from backend.rag.diskcache import JsonCache
from eval.metrics import judge

# Lifted verbatim from the eval set: one answerable, one out-of-corpus.
CASE_ANSWERABLE = {
    "query": "what is the difference between lower_bound and upper_bound",
    "contexts": ["lower_bound returns the first element not less than x. "
                 "upper_bound returns the first element strictly greater."],
    "answer": "lower_bound gives the first element >= x. upper_bound gives the "
              "first element > x. The distance between them counts the copies.",
}
CASE_OUT_OF_CORPUS = {
    "query": "how do I read input quickly in Python",
    "contexts": ["ios::sync_with_stdio(false) stops the C++ streams mirroring "
                 "the C stdio buffers."],
    "answer": "The passages do not cover Python; they describe C++ streams.",
}


class _Scripted:
    """A chat model that returns pre-built schema instances, in order."""

    def __init__(self, *results):
        self.results = list(results)
        self.prompts: list[str] = []

    def with_structured_output(self, schema):
        return self

    def invoke(self, messages, *a, **kw):
        self.prompts.append(messages[-1]["content"])
        if not self.results:
            raise AssertionError("the scorer made more calls than were scripted")
        return self.results.pop(0)


@pytest.fixture(autouse=True)
def no_cache(monkeypatch, tmp_path):
    """A fresh cache per test.

    Without this the committed judge cache would answer, and these tests would
    pass whatever the scorers did.
    """
    monkeypatch.setattr(
        judge, "_cache", lambda: JsonCache(str(tmp_path / "judge.json")))


def install(monkeypatch, *results) -> _Scripted:
    model = _Scripted(*results)
    monkeypatch.setattr(llm, "chat_model", lambda *a, **k: model)
    return model


# ------------------------------------------------------------- faithfulness

def test_faithfulness_is_the_fraction_of_claims_the_context_supports(monkeypatch):
    install(monkeypatch,
            judge._Claims(claims=["a", "b", "c", "d"]),
            judge._Support(supported=[True, True, False, True]))

    assert judge.faithfulness(CASE_ANSWERABLE["answer"],
                              CASE_ANSWERABLE["contexts"]) == pytest.approx(0.75)


def test_faithfulness_decomposes_so_padding_an_answer_lowers_it(monkeypatch):
    """The verbosity-bias defence, as a property rather than a comment.

    Same two supported claims, two unsupported ones bolted on. A holistic
    rubric would likely score the longer answer at least as high; scoring the
    fraction makes it strictly worse, which is the intended direction.
    """
    install(monkeypatch,
            judge._Claims(claims=["a", "b"]),
            judge._Support(supported=[True, True]))
    tight = judge.faithfulness("short", CASE_ANSWERABLE["contexts"])

    install(monkeypatch,
            judge._Claims(claims=["a", "b", "padding", "more padding"]),
            judge._Support(supported=[True, True, False, False]))
    padded = judge.faithfulness("long", CASE_ANSWERABLE["contexts"])

    assert tight == 1.0
    assert padded == pytest.approx(0.5)
    assert padded < tight


def test_a_short_judge_response_cannot_inflate_the_score(monkeypatch):
    """Four claims, two verdicts returned. The missing two count as unsupported;
    scoring 2/2 instead of 2/4 would reward the judge for replying briefly."""
    install(monkeypatch,
            judge._Claims(claims=["a", "b", "c", "d"]),
            judge._Support(supported=[True, True]))

    assert judge.faithfulness("x", CASE_ANSWERABLE["contexts"]) == pytest.approx(0.5)


def test_extra_verdicts_are_discarded_rather_than_counted(monkeypatch):
    install(monkeypatch,
            judge._Claims(claims=["a", "b"]),
            judge._Support(supported=[True, True, True, True]))

    assert judge.faithfulness("x", CASE_ANSWERABLE["contexts"]) == 1.0


def test_an_answer_making_no_claims_is_undefined_not_zero(monkeypatch):
    """A correct refusal has nothing to be unfaithful about. Zero would say it
    hallucinated; one would reward saying nothing. Neither is true."""
    install(monkeypatch, judge._Claims(claims=[]))

    assert judge.faithfulness(CASE_OUT_OF_CORPUS["answer"],
                              CASE_OUT_OF_CORPUS["contexts"]) is None


def test_claims_with_no_context_at_all_score_zero(monkeypatch):
    install(monkeypatch, judge._Claims(claims=["a"]))
    assert judge.faithfulness("something", []) == 0.0


# ---------------------------------------------------------- answer relevance

def test_answer_relevance_rescales_the_rubric_to_zero_one(monkeypatch):
    # Each assertion needs a distinct answer: identical inputs hit the cache,
    # which is correct behaviour and would make this loop test one rating four
    # times.
    for raw, expected in [(3, 1.0), (2, 2 / 3), (1, 1 / 3), (0, 0.0)]:
        install(monkeypatch, judge._Rating(score=raw))
        assert judge.answer_relevance("q", f"answer {raw}") == \
            pytest.approx(expected)


def test_a_rating_outside_the_rubric_is_clamped(monkeypatch):
    install(monkeypatch, judge._Rating(score=9))
    assert judge.answer_relevance("q", "too high") == 1.0

    install(monkeypatch, judge._Rating(score=-4))
    assert judge.answer_relevance("q", "too low") == 0.0


# --------------------------------------------------------- context precision

def test_context_precision_judges_each_passage_in_its_own_call(monkeypatch):
    """The position-bias defence. Three passages, three calls, and no call sees
    more than one passage — so there is no list order to be biased by."""
    model = install(monkeypatch,
                    judge._Useful(useful=True),
                    judge._Useful(useful=False),
                    judge._Useful(useful=True))

    score = judge.context_precision("q", ["alpha", "beta", "gamma"])

    assert score == pytest.approx(2 / 3)
    assert len(model.prompts) == 3
    for prompt, expected in zip(model.prompts, ["alpha", "beta", "gamma"]):
        assert expected in prompt
    assert "beta" not in model.prompts[0], "a call saw more than its own passage"


def test_context_precision_with_nothing_retrieved_is_undefined(monkeypatch):
    install(monkeypatch)
    assert judge.context_precision("q", []) is None


# ------------------------------------------------------------ context recall

def test_context_recall_measures_the_golden_answer_against_the_context(monkeypatch):
    install(monkeypatch,
            judge._Claims(claims=["x", "y", "z", "w"]),
            judge._Support(supported=[True, False, False, False]))

    assert judge.context_recall("golden text",
                                CASE_ANSWERABLE["contexts"]) == pytest.approx(0.25)


def test_context_recall_is_undefined_for_an_out_of_corpus_case(monkeypatch):
    """Its golden answer is an instruction to decline, which contains no claims
    to recall. Scoring zero would punish the eval set for being honest."""
    install(monkeypatch, judge._Claims(claims=[]))

    assert judge.context_recall("Decline; the notes do not cover this.",
                                CASE_OUT_OF_CORPUS["contexts"]) is None


# ---------------------------------------------------------------- abstention

def test_abstention_is_detected_on_the_out_of_corpus_case(monkeypatch):
    install(monkeypatch, judge._Abstained(abstained=True))
    assert judge.abstained(CASE_OUT_OF_CORPUS["answer"]) == 1.0

    install(monkeypatch, judge._Abstained(abstained=False))
    assert judge.abstained("Use sys.stdin.readline in Python.") == 0.0


# --------------------------------------------------------------------- cache

def test_a_repeated_judgement_is_served_from_cache(monkeypatch):
    """What makes a re-run free and identical. Without it the report's numbers
    would drift between runs for reasons unrelated to any change."""
    model = install(monkeypatch, judge._Rating(score=3))

    first = judge.answer_relevance("q", "a")
    second = judge.answer_relevance("q", "a")

    assert first == second == 1.0
    assert len(model.prompts) == 1, "the second call reached the model"


def test_the_cache_key_separates_different_judge_models(monkeypatch):
    """`--judge-swap` re-judges the same answers with another model. If the key
    ignored the model, the swap would silently return the first judge's scores
    and the measured self-preference would always be zero."""
    install(monkeypatch, judge._Rating(score=3))
    original = judge.answer_relevance("q", "a")

    monkeypatch.setattr(judge, "JUDGE_MODEL", "some-other-model")
    model = install(monkeypatch, judge._Rating(score=0))
    swapped = judge.answer_relevance("q", "a")

    assert original == 1.0
    assert swapped == 0.0
    assert len(model.prompts) == 1, "the swapped judge was served a stale entry"
