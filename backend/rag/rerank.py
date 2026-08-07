"""Reranking the fused candidates with a cross-encoding model call.

Dense and lexical retrieval both score a query against a passage *without ever
seeing them together* — the embedding was computed before the query existed, and
BM25 counts term overlap. That is what makes them fast enough to run over the
whole corpus, and it is also their ceiling: neither can tell that a passage
mentioning `lower_bound` six times is about the C++ function while the query
meant the mathematical concept.

A reranker reads the query and the passage together and scores the pair. That is
strictly more information, and it is why reranking is worth a stage of its own.

**What the latency buys.** One extra model call, about a second, per query. It
is spent on the top `RERANK_DEPTH` fused candidates only — reranking all 300
chunks would be accurate and absurd. The bet is that hybrid retrieval gets the
right chunk into the top 20 reliably (which recall@20 confirms) but puts it at
rank 9 rather than rank 2, and that the ordering is what the answer quality
depends on. Part 2's tables are where that bet gets checked: if nDCG@5 does not
move, the second is not being well spent.

**Position bias, handled rather than noted.** The obvious implementation shows
the model the candidates in fused order, and a model asked to rank a list is
measurably biased towards whatever is at the top of it. That would make the
reranker partly a re-statement of the fusion it is supposed to second-guess. So
the candidates are shuffled — deterministically, seeded by the query, so the
run is still reproducible — and the presentation order carries no signal. Where
the model has no opinion, ties fall back to fused order.
"""
from __future__ import annotations

import hashlib
import os
import random

from pydantic import BaseModel, Field

from .. import llm
from .diskcache import JsonCache

# The fused list is cut to this before reranking. Deep enough that the right
# chunk is nearly always inside it, shallow enough for one prompt.
RERANK_DEPTH = 20

# How much of each candidate the reranker sees. A chunk's first few hundred
# characters carry its heading path and topic sentence, which is what the
# relevance decision turns on; sending all of every candidate multiplies the
# prompt for very little.
SNIPPET_CHARS = 700

# Reranking is a judgement call about meaning, so it goes to the main model
# rather than the cheap classifier the router uses.
RERANK_MODEL = os.environ.get("CP_TUTOR_RERANK_MODEL", llm.MAIN_MODEL)

SYSTEM = """\
You rank reference passages by how well each one answers a specific question.

You will be given a question and a numbered list of passages. Score every
passage from 0 to 10:

  0-2   unrelated, or about a different topic that shares vocabulary
  3-5   related subject matter, but does not answer the question
  6-8   contains part of the answer
  9-10  directly and substantially answers the question

Judge each passage on its own merits against the question. The order the
passages are listed in is arbitrary and carries no information — do not let a
passage's position influence its score. Score every passage you are given, and
use its number exactly as shown.
"""


class _Score(BaseModel):
    number: int = Field(description="the passage's number, as shown")
    score: int = Field(description="relevance from 0 to 10")


class _Scores(BaseModel):
    scores: list[_Score]


def _cache() -> JsonCache:
    from .index import index_root
    return JsonCache(os.path.join(index_root(), "rerank_cache.json"))


def _shuffled(candidate_ids: list[str], query: str) -> list[int]:
    """A presentation order that is unrelated to fusion rank but reproducible."""
    seed = int(hashlib.sha256(query.encode("utf-8")).hexdigest()[:16], 16)
    order = list(range(len(candidate_ids)))
    random.Random(seed).shuffle(order)
    return order


def rerank(query: str, candidates: list[tuple[str, str]]) -> list[str]:
    """Reorder `(chunk_id, text)` candidates by judged relevance, best first.

    Returns chunk ids. Candidates the model omits keep their fused position
    behind the ones it scored, so a truncated or partial response degrades to
    "fusion order" rather than to "some chunks vanished".
    """
    if len(candidates) <= 1:
        return [cid for cid, _ in candidates]

    ids = [cid for cid, _ in candidates]
    cache = _cache()
    key = JsonCache.key("rerank-v1", RERANK_MODEL, query, ids)
    cached = cache.get(key)
    if cached is not None:
        return list(cached)

    presentation = _shuffled(ids, query)
    lines = []
    for shown, original in enumerate(presentation, start=1):
        snippet = candidates[original][1][:SNIPPET_CHARS].replace("\n", " ")
        lines.append(f"[{shown}] {snippet}")

    prompt = f"Question: {query}\n\nPassages:\n" + "\n\n".join(lines)

    try:
        result = llm.generate_structured(
            SYSTEM, [{"role": "user", "content": prompt}], _Scores, RERANK_MODEL)
    except Exception:
        # A reranker that fails should cost the improvement, not the answer.
        return ids

    fused_rank = {cid: i for i, cid in enumerate(ids)}
    scored: dict[str, int] = {}
    for item in result.scores:
        shown = item.number - 1
        if 0 <= shown < len(presentation):
            scored[ids[presentation[shown]]] = item.score

    ordered = sorted(
        ids,
        key=lambda cid: (-scored.get(cid, -1), fused_rank[cid]),
    )
    cache.put(key, ordered)
    return ordered
