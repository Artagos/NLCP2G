"""The five rank metrics, against fixtures worked out by hand.

Every expected value below was computed on paper before the code ran. That
distinction is the whole point of testing a scorer: a fixture recorded from the
implementation asserts only that the code still does what it did, which is
exactly what a wrong scorer also does. A number derived independently can
disagree with the code, and if it does, one of them is wrong.

The three things worth pinning here are the ones a plausible-looking
implementation gets wrong: nDCG's discount and normaliser, undefined-versus-zero
on an empty golden set, and the fact that repacking the results for a model
degrades exactly two of the five.
"""
from __future__ import annotations

import math

import pytest

from eval.metrics import rank


# A fixed scenario used by most of the tests below.
#
#   retrieved: c1  c2  c3  c4  c5      (rank 1 .. 5)
#   golden:        c2      c4
#
# By hand, at k = 5:
#   hit rate  = 1                     (c2 is in there)
#   precision = 2/5 = 0.4             (two of the five returned are golden)
#   recall    = 2/2 = 1.0             (both golden chunks came back)
#   MRR       = 1/2 = 0.5             (first golden chunk is at rank 2)
#   DCG       = 1/log2(3) + 1/log2(5) = 0.630930 + 0.430677 = 1.061607
#   IDCG      = 1/log2(2) + 1/log2(3) = 1.0      + 0.630930 = 1.630930
#   nDCG      = 1.061607 / 1.630930 = 0.650921
RETRIEVED = ["c1", "c2", "c3", "c4", "c5"]
GOLDEN = {"c2", "c4"}


def test_the_set_based_three_count_what_came_back():
    assert rank.hit_rate_at_k(RETRIEVED, GOLDEN, 5) == 1.0
    assert rank.precision_at_k(RETRIEVED, GOLDEN, 5) == pytest.approx(0.4)
    assert rank.recall_at_k(RETRIEVED, GOLDEN, 5) == pytest.approx(1.0)


def test_mrr_is_the_reciprocal_of_the_first_hit_only():
    assert rank.mrr_at_k(RETRIEVED, GOLDEN, 5) == pytest.approx(0.5)
    # Moving the SECOND golden chunk cannot change MRR — that insensitivity is
    # the property that distinguishes it from nDCG, and it is easy to lose by
    # accidentally summing over every hit.
    moved = ["c1", "c2", "c4", "c3", "c5"]
    assert rank.mrr_at_k(moved, GOLDEN, 5) == pytest.approx(0.5)
    assert rank.ndcg_at_k(moved, GOLDEN, 5) > rank.ndcg_at_k(RETRIEVED, GOLDEN, 5)


def test_ndcg_matches_the_hand_computed_value():
    expected = ((1 / math.log2(3)) + (1 / math.log2(5))) / \
               ((1 / math.log2(2)) + (1 / math.log2(3)))
    assert rank.ndcg_at_k(RETRIEVED, GOLDEN, 5) == pytest.approx(expected)
    assert rank.ndcg_at_k(RETRIEVED, GOLDEN, 5) == pytest.approx(0.650921, abs=1e-6)


def test_a_perfect_ranking_scores_one_and_the_ideal_is_truncated_at_k():
    assert rank.ndcg_at_k(["c2", "c4", "c1"], GOLDEN, 3) == pytest.approx(1.0)
    # Only one of the two golden chunks can fit in the top 1, so the ideal must
    # be truncated to k. An IDCG summed over all of `golden` regardless of k
    # would make a perfect top-1 score 0.61 instead of 1.0.
    assert rank.ndcg_at_k(["c2"], GOLDEN, 1) == pytest.approx(1.0)


def test_nothing_relevant_scores_zero_rather_than_undefined():
    """Zero is the right answer when there WAS something to find and it was
    missed. That is a different fact from `None`, and the two must not merge."""
    for value in rank.score_case(["x", "y"], GOLDEN, 2).as_dict().values():
        assert value == 0.0


def test_an_empty_golden_set_is_undefined_not_zero():
    """Out-of-corpus cases have nothing to retrieve, so there is no fraction to
    compute. Scoring them zero would drag every average down in proportion to
    how many honest unanswerable cases the eval set contains."""
    scores = rank.score_case(RETRIEVED, set(), 5)
    assert scores.as_dict() == {name: None for name in rank.METRIC_NAMES}


def test_undefined_cases_are_excluded_from_the_average_and_counted():
    scores = [
        rank.score_case(["c2"], {"c2"}, 3),      # everything 1.0
        rank.score_case(["zz"], {"c2"}, 3),      # everything 0.0
        rank.score_case(["c2"], set(), 3),       # undefined throughout
    ]
    agg = rank.aggregate(scores)

    assert agg.scored == 2
    assert agg.undefined == 1
    # The mean of 1.0 and 0.0 is 0.5. Counting the undefined case as a zero
    # would give 0.333 — the number the report would print if `None` were
    # quietly coerced.
    assert agg.means["hit_rate"] == pytest.approx(0.5)
    assert agg.means["ndcg"] == pytest.approx(0.5)


def test_precision_divides_by_what_was_returned_not_by_k():
    """Asking for 10 and getting 2, both relevant, is precision 1.0. Dividing by
    k would score it 0.2 and be measuring the corpus's size."""
    assert rank.precision_at_k(["c2", "c4"], GOLDEN, 10) == pytest.approx(1.0)


def test_k_truncates_before_anything_is_counted():
    assert rank.hit_rate_at_k(RETRIEVED, GOLDEN, 1) == 0.0    # c1 is not golden
    assert rank.recall_at_k(RETRIEVED, GOLDEN, 2) == pytest.approx(0.5)
    assert rank.mrr_at_k(RETRIEVED, GOLDEN, 1) == 0.0


def test_packing_for_the_model_degrades_only_the_rank_aware_metrics():
    """The trap this whole module is arranged around.

    `retriever.pack_for_llm` reorders results so the strongest sit at the edges
    of the context, where models attend best. Feed that order to the metrics and
    the two rank-aware ones fall while the three set-based ones do not move at
    all — so the mistake shows up as a retrieval regression rather than as a
    bug, and the three unchanged numbers make it look like a real one.
    """
    retrieved = ["c1", "c2", "c3", "c4", "c5"]
    golden = {"c1"}

    # pack_for_llm's interleave: ranks 1,3,5 forward then 4,2 reversed.
    packed = ["c1", "c3", "c5", "c4", "c2"]
    # ...and for a golden chunk at rank 2 the strongest case is c2, which the
    # packing sends to the very end.
    golden_at_two = {"c2"}
    packed_hides_it = ["c1", "c3", "c5", "c4", "c2"]

    true_scores = rank.score_case(retrieved, golden_at_two, 5)
    packed_scores = rank.score_case(packed_hides_it, golden_at_two, 5)

    # Unchanged: they only ask which chunks are present.
    assert packed_scores.hit_rate == true_scores.hit_rate == 1.0
    assert packed_scores.precision == true_scores.precision
    assert packed_scores.recall == true_scores.recall

    # Degraded: rank 2 became rank 5.
    assert true_scores.mrr == pytest.approx(0.5)
    assert packed_scores.mrr == pytest.approx(0.2)
    assert packed_scores.ndcg < true_scores.ndcg

    # And the same reordering leaves a rank-1 chunk alone, which is why the
    # error is intermittent enough to survive a casual check.
    assert rank.score_case(packed, golden, 5).mrr == pytest.approx(1.0)


def test_the_retriever_and_the_packer_really_do_disagree():
    """Pins the above against the real functions rather than a transcription of
    them, so re-coupling `search` and `pack_for_llm` fails here."""
    from backend.rag.retriever import Retrieved, pack_for_llm

    results = [Retrieved(chunk_id=f"c{i}", doc="d", heading="h", text=f"c{i}",
                         score=1.0 / i, rank=i) for i in range(1, 6)]
    packed_order = [block.splitlines()[0].strip("[]")
                    for block in pack_for_llm(results).split("\n\n---\n\n")]

    assert [r.chunk_id for r in results] == ["c1", "c2", "c3", "c4", "c5"]
    assert packed_order == ["c1", "c3", "c5", "c4", "c2"]
    assert packed_order != [r.chunk_id for r in results]
