"""The system under test for the generation metrics: retrieve, then answer.

    query ─► retriever.search(rerank=…) ─► pack_for_llm ─► model ─► answer

It calls `backend.rag.retriever.search` — the same function `search_corpus`
calls — and prompts with `prompts.CORPUS_ANSWER_SYSTEM`, which lives in the
application. So the two things being measured, retrieval and the grounding
instruction, are both production artefacts. What is harness-only is the loop
around them, which is the part that should be.

**Why this and not the tutor.** The tutor is wrapped in R1 and would decline a
good share of this eval set on principle. Faithfulness scored over a set of
principled refusals measures the guardrail, not the retrieval, and would go up
if retrieval got worse. The assignment scopes agent-level evaluation to the next
increment for what turns out to be a very concrete reason.

**Why the answers are cached.** Same argument as everywhere else here: a
generation re-run must produce the same text, or the judged metrics move between
runs for reasons that have nothing to do with the change being evaluated.

Note this is where `pack_for_llm` is used — the packed order goes to the *model*,
while the metrics read `chunk_ids`, which stay in retriever order. That is the
whole point of the two being separate functions.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field

from backend import llm, prompts
from backend.rag import retriever
from backend.rag.diskcache import JsonCache

CACHE_DIR = os.path.join(os.path.dirname(__file__), "cache")

# The answerer. Named separately from the judge so the report can state that
# they differ, and so changing one does not silently change the other.
GENERATOR_MODEL = os.environ.get("CP_TUTOR_EVAL_GENERATOR", llm.MAIN_MODEL)


@dataclass
class Answered:
    """One answer, and the retrieval it was built on."""

    case_id: str
    query: str
    answer: str
    chunk_ids: list[str] = field(default_factory=list)   # RETRIEVER order
    contexts: list[str] = field(default_factory=list)    # texts, same order


def _cache() -> JsonCache:
    return JsonCache(os.path.join(CACHE_DIR, "answers.json"))


def generate(case_id: str, query: str, k: int = 5, rerank: bool = True) -> Answered:
    """Retrieve, pack, answer."""
    results = retriever.search(query, k=k, rerank=rerank)
    chunk_ids = [r.chunk_id for r in results]

    if not results:
        return Answered(case_id, query, "The passages do not cover this.", [], [])

    packed = retriever.pack_for_llm(results)
    cache = _cache()
    key = JsonCache.key("answer-v1", GENERATOR_MODEL,
                        prompts.CORPUS_ANSWER_SYSTEM, query, packed)
    text = cache.get(key)

    if text is None:
        text = llm.generate(
            prompts.CORPUS_ANSWER_SYSTEM,
            [{"role": "user",
              "content": f"Passages:\n\n{packed}\n\nQuestion: {query}"}],
            GENERATOR_MODEL,
        )
        cache.put(key, text)

    return Answered(case_id, query, text, chunk_ids, [r.text for r in results])
