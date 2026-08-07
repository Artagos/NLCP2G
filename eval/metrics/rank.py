"""The five rank-aware retrieval metrics, hand-written.

No evaluation library ships this family — DeepEval and Ragas both lack all five,
and DeepEval's `ContextualPrecisionMetric` and `ContextualRecallMetric` are
*judged* metrics that ask a model whether a chunk was useful. Those measure
something different, cost an API call each, and do not reproduce. These are
arithmetic over a golden set: free, exact, and identical on every run.

That is why they run first. A retrieval bug caught here costs nothing; the same
bug caught after the judged metrics have run has already been paid for.

Three of them — hit rate, precision, recall — depend only on *which* chunks came
back. Two — MRR and nDCG — depend on the order. That split is the whole reason
the retriever's output order must reach this module untouched: run these against
a list that has been repacked for a model's attention and the set-based three
are unchanged while the rank-based two silently degrade. `retriever.pack_for_llm`
is a separate function for exactly this reason, and
`tests/test_eval_metrics.py::test_packing_for_the_model_degrades_only_the_rank_aware_metrics`
pins the difference with numbers.

**Undefined is not zero.** A case whose golden set is empty — a question the
corpus genuinely cannot answer — has no correct chunks to find, so "what
fraction of the relevant chunks made the cut" has no value. Scoring it zero
would drag every average down in proportion to how many honest unanswerable
cases the set contains, which would punish having written them. Every function
here returns `None` on an empty golden set, and `aggregate` excludes those and
reports their count separately.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

METRIC_NAMES = ["hit_rate", "precision", "recall", "mrr", "ndcg"]


@dataclass(frozen=True)
class CaseScores:
    """One case at one k. `None` means undefined, which is not the same as 0.0."""

    hit_rate: float | None
    precision: float | None
    recall: float | None
    mrr: float | None
    ndcg: float | None

    def as_dict(self) -> dict[str, float | None]:
        return {name: getattr(self, name) for name in METRIC_NAMES}


def hit_rate_at_k(retrieved: list[str], golden: set[str], k: int) -> float | None:
    """Did any relevant chunk make the cut? 1.0 or 0.0."""
    if not golden:
        return None
    return 1.0 if any(c in golden for c in retrieved[:k]) else 0.0


def precision_at_k(retrieved: list[str], golden: set[str], k: int) -> float | None:
    """What fraction of what was returned is relevant?

    Divided by how many were actually returned, not by k. When the retriever
    hands back fewer than k results, dividing by k would score it down for
    results it never claimed to have — which measures the corpus's size rather
    than the retriever's quality.
    """
    if not golden:
        return None
    top = retrieved[:k]
    if not top:
        return 0.0
    return sum(1 for c in top if c in golden) / len(top)


def recall_at_k(retrieved: list[str], golden: set[str], k: int) -> float | None:
    """What fraction of the relevant chunks made the cut?"""
    if not golden:
        return None
    return sum(1 for c in retrieved[:k] if c in golden) / len(golden)


def mrr_at_k(retrieved: list[str], golden: set[str], k: int) -> float | None:
    """Reciprocal of the rank of the first relevant chunk; 0.0 if none in top k.

    Rank 1 scores 1.0, rank 2 scores 0.5, rank 5 scores 0.2. It answers "how far
    down did the user have to read", and it notices nothing after the first hit —
    which is what distinguishes it from nDCG.
    """
    if not golden:
        return None
    for position, chunk_id in enumerate(retrieved[:k], start=1):
        if chunk_id in golden:
            return 1.0 / position
    return 0.0


def ndcg_at_k(retrieved: list[str], golden: set[str], k: int) -> float | None:
    """Rank-weighted gain, normalised against the best possible ordering.

    Binary relevance: a chunk is golden or it is not, so the gain is 1 or 0 and
    the discount is 1/log2(rank + 1). The ideal ordering puts every golden chunk
    that could fit into the top k at the front, so IDCG sums the discount over
    min(k, |golden|) positions.

    This is the metric that notices a relevant chunk sliding from position 1 to
    position 5 while hit rate, precision and recall all stay flat — which is
    precisely the movement a reranker is bought to produce.
    """
    if not golden:
        return None
    dcg = sum(1.0 / math.log2(position + 1)
              for position, chunk_id in enumerate(retrieved[:k], start=1)
              if chunk_id in golden)
    ideal = sum(1.0 / math.log2(position + 1)
                for position in range(1, min(k, len(golden)) + 1))
    return dcg / ideal if ideal else None


def score_case(retrieved: list[str], golden: set[str], k: int) -> CaseScores:
    """All five, for one case at one k."""
    return CaseScores(
        hit_rate=hit_rate_at_k(retrieved, golden, k),
        precision=precision_at_k(retrieved, golden, k),
        recall=recall_at_k(retrieved, golden, k),
        mrr=mrr_at_k(retrieved, golden, k),
        ndcg=ndcg_at_k(retrieved, golden, k),
    )


@dataclass(frozen=True)
class Aggregate:
    """Means over the defined cases, plus how many were skipped and why."""

    means: dict[str, float]
    scored: int
    undefined: int

    def as_row(self) -> dict[str, float]:
        return dict(self.means)


def aggregate(scores: list[CaseScores]) -> Aggregate:
    """Average each metric over the cases where it is defined.

    Cases with an empty golden set contribute to `undefined` and to nothing
    else. Reporting that count alongside the means is the point — an average
    over 21 of 24 cases means something different from an average over 24, and a
    table that does not say which it is cannot be checked.
    """
    means: dict[str, float] = {}
    undefined = sum(1 for s in scores if s.hit_rate is None)

    for name in METRIC_NAMES:
        values = [v for v in (getattr(s, name) for s in scores) if v is not None]
        means[name] = sum(values) / len(values) if values else 0.0

    return Aggregate(means=means, scored=len(scores) - undefined, undefined=undefined)
