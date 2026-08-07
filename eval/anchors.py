"""Loading the eval set, and turning content anchors into chunk ids.

Chunk ids encode the chunker's parameters — `binary-search#lower-bound-and-
upper-bound#0` says which document, which heading, and which piece of that
heading's section. Change `WINDOW_CHARS` and the third component moves; reword a
heading and the second one does. Storing ids in the eval set would mean every
tuning experiment silently invalidated the golden data, and the metrics would
report a retrieval regression that was really a bookkeeping change.

So the set stores `{doc, heading}` and this module resolves it against whatever
the index currently holds. A heading whose section split into three chunks
resolves to all three, and retrieving any of them counts as finding it — which
is the right semantics, since they are pieces of one answer.

**Unresolvable anchors raise.** An anchor that quietly resolved to the empty set
would make every metric on that case read as a total retrieval failure, which is
indistinguishable from a real one. Failing at load time turns a silent wrong
number into a loud, fixable error.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field

import yaml

from backend.rag import index as index_module

DATASET = os.path.join(os.path.dirname(__file__), "dataset", "cases.yaml")

CATEGORIES = [
    "exact-term", "acronym", "paraphrase", "near-duplicate",
    "multi-hop", "negation", "ambiguous", "out-of-corpus",
]


@dataclass
class Case:
    """One evaluation case, with its anchors already resolved."""

    id: str
    category: str
    query: str
    golden_answer: str
    notes: str
    anchors: list[tuple[str, str]]        # (doc, heading), as written
    golden_ids: set[str] = field(default_factory=set)

    @property
    def answerable(self) -> bool:
        """False for the out-of-corpus cases, whose golden set is empty.

        Ranking metrics are undefined rather than zero on these; callers filter
        on this rather than on `len(golden_ids)`, so the intent is legible.
        """
        return bool(self.anchors)


def resolve(anchors: list[tuple[str, str]], idx: index_module.Index) -> set[str]:
    """Anchors -> the chunk ids they name. Raises on an anchor matching nothing."""
    ids: set[str] = set()
    for doc, heading in anchors:
        matched = {c.chunk_id for c in idx.chunks
                   if c.doc == doc and c.heading == heading}
        if not matched:
            near = sorted({c.heading for c in idx.chunks if c.doc == doc})
            hint = f"; headings in {doc!r}: {near}" if near else \
                   f"; no document named {doc!r}"
            raise ValueError(
                f"anchor {{doc: {doc}, heading: {heading!r}}} matches no chunk{hint}")
        ids |= matched
    return ids


def load(path: str = DATASET, idx: index_module.Index | None = None) -> list[Case]:
    """The eval set, validated against the current index."""
    idx = idx or index_module.get()
    with open(path, encoding="utf-8") as fh:
        raw = yaml.safe_load(fh)

    cases: list[Case] = []
    seen: set[str] = set()
    for entry in raw:
        case_id = entry["id"]
        if case_id in seen:
            raise ValueError(f"duplicate case id {case_id!r}")
        seen.add(case_id)

        category = entry["category"]
        if category not in CATEGORIES:
            raise ValueError(
                f"{case_id}: unknown category {category!r}, expected one of "
                f"{CATEGORIES}")

        anchors = [(a["doc"], a["heading"])
                   for a in (entry.get("golden_context") or [])]
        if category == "out-of-corpus" and anchors:
            raise ValueError(
                f"{case_id}: an out-of-corpus case must have no golden context")
        if category != "out-of-corpus" and not anchors:
            raise ValueError(
                f"{case_id}: only out-of-corpus cases may have no golden context")

        cases.append(Case(
            id=case_id,
            category=category,
            query=entry["query"],
            golden_answer=(entry.get("golden_answer") or "").strip(),
            notes=(entry.get("notes") or "").strip(),
            anchors=anchors,
            golden_ids=resolve(anchors, idx),
        ))
    return cases


def by_category(cases: list[Case]) -> dict[str, list[Case]]:
    """Cases grouped, in the declared category order.

    Averaging over a mixed set hides which category the retriever is bad at,
    which is the entire reason the cases are tagged.
    """
    grouped: dict[str, list[Case]] = {c: [] for c in CATEGORIES}
    for case in cases:
        grouped[case.category].append(case)
    return {k: v for k, v in grouped.items() if v}


def stratified(cases: list[Case], per_category: int = 1) -> list[Case]:
    """A subset with `per_category` cases from each category, in file order.

    For the judged metrics when a full second run is not affordable: the point
    of a subset is that it still spans every category, so a cheaper run cannot
    accidentally exclude the categories the system is worst at.
    """
    out: list[Case] = []
    for group in by_category(cases).values():
        out.extend(group[:per_category])
    return out
