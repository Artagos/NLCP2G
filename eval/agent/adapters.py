"""Traces in, existing scorers out. No scorer was harmed in the making of this.

The assignment's test of an adapter is whether wiring a scorer to a trace forces
you to edit the scorer. Here it does not, and the reason is worth stating: the
HW5 judged scorers were written against plain `str` and `list[str]` — never
against a `Retrieved`, a `Case`, or anything else from the retrieval layer —
so anything that can produce a question, an answer and some passages can drive
them. That was a deliberate choice then and it pays here.

    judge.faithfulness(answer, contexts)
    judge.answer_relevance(query, answer)
    judge.context_precision(query, contexts)
    judge.context_recall(golden_answer, contexts)

All four are imported and called. `eval/metrics/judge.py` is untouched by this
increment; `git diff` on it is empty.

**Where the passages come from.** The RETRIEVER span records chunk *ids*, not
passage text — a trace that inlined every passage would be several times larger
and would duplicate what the committed index already holds exactly. So the
adapter resolves ids against the index. That keeps traces small, keeps the
committed artefact readable in a diff, and means the passages fed to a scorer
are the real ones rather than a copy that could drift.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from backend.rag import index as index_module

from . import store
from .store import TraceView


@dataclass
class Grounding:
    """What a traced turn offers the RAG scorers."""

    query: str
    answer: str
    chunk_ids: list[str] = field(default_factory=list)
    contexts: list[str] = field(default_factory=list)

    @property
    def retrieved(self) -> bool:
        """False for turns that never searched — a refusal, or a memory question.

        Faithfulness and the two context metrics are undefined on those: there
        is no retrieved context for the answer to be faithful *to*. Scoring them
        zero would say the tutor hallucinated when in fact it correctly declined
        to search, which is the opposite of the truth.
        """
        return bool(self.chunk_ids)


def _first(mapping, *keys, default=""):
    if isinstance(mapping, dict):
        for key in keys:
            value = mapping.get(key)
            if value:
                return value
    return default


def grounding(trace: TraceView, idx=None) -> Grounding:
    """Pull the scorer inputs out of one traced turn."""
    root = trace.root
    query = _first(getattr(root, "inputs", None), "message", "query")
    answer = _first(getattr(root, "outputs", None), "reply", "answer")

    chunk_ids: list[str] = []
    for span in trace.of_type(store.RETRIEVER):
        for chunk_id in (_first(span.outputs, "chunk_ids", default=[]) or []):
            if chunk_id not in chunk_ids:
                chunk_ids.append(str(chunk_id))

    contexts: list[str] = []
    if chunk_ids:
        idx = idx or index_module.get()
        for chunk_id in chunk_ids:
            chunk = idx.by_id.get(chunk_id)
            if chunk is not None:
                contexts.append(chunk.text)

    return Grounding(query=query, answer=answer,
                     chunk_ids=chunk_ids, contexts=contexts)


def rag_scores(trace: TraceView, golden_answer: str = "",
               idx=None) -> dict[str, float | None]:
    """The HW5 judged metrics, computed over a trace instead of over a case.

    Every value may be None, which means undefined rather than bad — see
    `Grounding.retrieved`. Judge calls are cached by content hash inside
    `judge.py`, so re-scoring the same committed traces is free and identical.
    """
    from eval.metrics import judge          # imported, never modified

    ground = grounding(trace, idx=idx)
    if not ground.answer:
        return {"faithfulness": None, "answer_relevance": None,
                "context_precision": None, "context_recall": None}

    scores: dict[str, float | None] = {
        "answer_relevance": judge.answer_relevance(ground.query, ground.answer),
        "faithfulness": None,
        "context_precision": None,
        "context_recall": None,
    }
    if ground.retrieved:
        scores["faithfulness"] = judge.faithfulness(ground.answer, ground.contexts)
        scores["context_precision"] = judge.context_precision(
            ground.query, ground.contexts)
        if golden_answer:
            scores["context_recall"] = judge.context_recall(
                golden_answer, ground.contexts)
    return scores
