"""Reciprocal rank fusion.

Two retrievers, two rankings, one list. The problem RRF solves is that the two
score scales have nothing to do with each other: a BM25 score of 14.2 and a
cosine similarity of 0.71 cannot be compared, added, or averaged in any way that
means something. Normalising them to [0, 1] first is the obvious fix and a bad
one — it makes the fusion sensitive to the *distribution* of scores in each
list, so one retriever returning a flat spread of mediocre matches can outvote
another returning one excellent match.

RRF throws the scores away entirely and keeps only the ranks:

    score(d) = sum over retrievers of  1 / (K + rank(d))

A document ranked first by either retriever gets 1/(K+1) from it; one ranked
tenth gets 1/(K+10). Documents both retrievers like accumulate from both, which
is the behaviour wanted — agreement between two methods that fail differently is
real evidence.

K = 60 is the constant from Cormack, Clarke and Buettcher (2009), and it is
inherited rather than tuned. What it controls is how sharply the head of each
list is preferred: with K = 0 the first result is worth twice the second, which
lets one retriever's confident mistake dominate. At 60 the difference between
rank 1 and rank 10 is about 14%, so the fusion is decided by *broad agreement*
rather than by either list's top entry. That damping is the whole point, and
tuning K against the eval set would be fitting the retriever to its own test.
"""
from __future__ import annotations

RRF_K = 60


def reciprocal_rank_fusion(
    rankings: list[list[str]], k: int = RRF_K
) -> list[tuple[str, float]]:
    """Fuse ranked id lists into one, best first.

    Each input is ordered best-first. Ties are broken by first appearance across
    the inputs, so the output is deterministic — which matters because the eval
    harness compares runs and a nondeterministic tie-break would show up as a
    metric that moves for no reason.
    """
    scores: dict[str, float] = {}
    first_seen: dict[str, int] = {}
    counter = 0

    for ranking in rankings:
        for position, chunk_id in enumerate(ranking, start=1):
            scores[chunk_id] = scores.get(chunk_id, 0.0) + 1.0 / (k + position)
            if chunk_id not in first_seen:
                first_seen[chunk_id] = counter
                counter += 1

    return sorted(scores.items(), key=lambda kv: (-kv[1], first_seen[kv[0]]))
