"""Part 1's before/after story: what each retrieval stage actually fixed.

    python -m scripts.demo_rag              # writes traces/09-retrieval-stages.md
    python -m scripts.demo_rag --stdout

Runs eight queries through the pipeline and shows what dense-only, BM25-only,
RRF-fused and reranked each returned. The verdict per case is *derived* — the
rank of the first golden chunk at each stage, compared — rather than asserted,
so the narrative cannot drift from what the retriever does.

Everything comes from `retriever.stages()`, which is the production pipeline
with its intermediate results exposed. No second implementation to fall out of
step with the first.

Reads from the committed caches, so it needs no API key once the index is built.
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from dotenv import load_dotenv  # noqa: E402

load_dotenv()

from backend.rag import index as index_module, rerank as rerank_module  # noqa: E402
from backend.rag import retriever  # noqa: E402
from backend.rag.fusion import RRF_K  # noqa: E402
from eval import anchors  # noqa: E402

TRACE_DIR = os.path.join(os.path.dirname(__file__), "..", "traces")
SHOW = 3

# Which cases to show is chosen from the data, not by hand.
#
# The first attempt at this picked one case per category and produced a trace in
# which seven of eight read "both arms found it at rank 1, nothing changed" —
# true, and useless as a before/after comparison. The stages only differ on some
# queries, so the honest thing is to select the ones where they do and say
# plainly how many they did not.
SHOWN = 7                       # answerable cases, most-disagreement first
# Plus one out-of-corpus case, always. It has no golden chunk, and what it
# demonstrates is that every stage returns confident, plausible, wrong-subject
# passages — the failure the answerer has to catch and the retriever cannot.
ALWAYS = "ooc-language-not-covered"

STAGES = [("dense", "dense only"), ("lexical", "BM25 only"),
          ("fused", "RRF fused"), ("reranked", "reranked")]


def first_golden(ids: list[str], golden: set[str]) -> int | None:
    for position, chunk_id in enumerate(ids, start=1):
        if chunk_id in golden:
            return position
    return None


def verdict(ranks: dict[str, int | None], answerable: bool) -> str:
    """What changed, stage by stage, in one sentence — computed, not written."""
    if not answerable:
        return ("No golden chunk exists. Every stage returns confident, "
                "on-topic, wrong-subject passages; catching that is the "
                "answerer's job, not the retriever's.")

    dense, lexical = ranks["dense"], ranks["lexical"]
    fused, reranked = ranks["fused"], ranks["reranked"]
    parts: list[str] = []

    if dense is None and lexical is not None:
        parts.append(f"vector search missed it entirely; BM25 found it at "
                     f"rank {lexical}")
    elif lexical is None and dense is not None:
        parts.append(f"BM25 missed it entirely; vector search found it at "
                     f"rank {dense}")
    elif dense is not None and lexical is not None:
        parts.append(f"both arms found it (dense {dense}, BM25 {lexical})")
    else:
        parts.append("neither arm found it in the top results")

    best_arm = min([r for r in (dense, lexical) if r is not None], default=None)
    if fused is None:
        parts.append("fusion lost it")
    elif best_arm is None:
        parts.append(f"fusion surfaced it at {fused}")
    elif fused < best_arm:
        parts.append(f"RRF promoted it to {fused}")
    elif fused == best_arm:
        parts.append(f"RRF held it at {fused}")
    else:
        parts.append(f"RRF pushed it down to {fused}")

    if reranked is None:
        parts.append("reranking dropped it")
    elif fused is None:
        parts.append(f"reranking recovered it at {reranked}")
    elif reranked < fused:
        parts.append(f"reranking lifted it to {reranked}")
    elif reranked == fused:
        parts.append(f"reranking left it at {reranked}")
    else:
        parts.append(f"reranking demoted it to {reranked}")

    sentence = "; ".join(parts)
    return sentence[0].upper() + sentence[1:] + "."


def stage_ranks(idx, case) -> dict[str, int | None]:
    """Rank of the first golden chunk at each stage, over the reranked depth."""
    deep = retriever.stages(case.query, k=rerank_module.RERANK_DEPTH, idx=idx)
    return {name: first_golden(deep[name], case.golden_ids) for name, _ in STAGES}


def disagreement(ranks: dict[str, int | None]) -> tuple[int, int]:
    """How much the stages differ on this case. Bigger sorts first.

    A stage that missed the chunk entirely counts as a large rank rather than
    as a tie with the others, since "not found" is the most interesting
    disagreement there is.
    """
    values = [r if r is not None else rerank_module.RERANK_DEPTH * 2
              for r in ranks.values()]
    return len(set(values)), max(values) - min(values)


def select(idx, everything) -> tuple[list, int]:
    """The cases worth showing, and how many were left out for being unanimous."""
    answerable = [c for c in everything.values()
                  if c.answerable and c.id != ALWAYS]
    ranked = sorted(answerable,
                    key=lambda c: disagreement(stage_ranks(idx, c)),
                    reverse=True)
    chosen = ranked[:SHOWN]
    unanimous = sum(1 for c in answerable
                    if disagreement(stage_ranks(idx, c))[0] == 1)
    return chosen + [everything[ALWAYS]], unanimous, len(answerable)


def render(idx, cases, unanimous: int, total: int) -> str:
    lines = [
        "# Retrieval, stage by stage",
        "",
        "Eight queries through the pipeline: the seven where the four stages",
        "disagree most about where the right passage sits, plus one",
        "out-of-corpus case. For each, what dense-only, BM25-only, RRF-fused and",
        "reranked returned — and which stage actually fixed it.",
        "",
        "The cases are picked from the data rather than by hand, because not",
        f"every query is interesting: of the {total} answerable cases considered,",
        f"**{unanimous}** have all four stages putting the right chunk at the",
        "same rank, with nothing to show. Reporting that is part of an honest",
        "before/after — the stages earn their keep on the hard queries, not on",
        "all of them.",
        "",
        f"Corpus: {len(idx.chunks)} chunks from "
        f"{len({c.doc for c in idx.chunks})} documents. Retrieve "
        f"{retriever.DENSE_N} dense + {retriever.LEXICAL_N} lexical, fuse with "
        f"RRF (k={RRF_K}), rerank the top {rerank_module.RERANK_DEPTH}.",
        "",
        "`✓` marks a chunk in the case's golden set. The verdict line is derived",
        "from the rank of the first golden chunk at each stage, not written by",
        "hand — regenerate with `python -m scripts.demo_rag` and it re-derives.",
        "",
        "Generated by `scripts/demo_rag.py`.",
        "",
        "---",
        "",
    ]

    for case in cases:
        stages = retriever.stages(case.query, k=SHOW, idx=idx)
        deep = {name: retriever.stages(case.query, k=rerank_module.RERANK_DEPTH,
                                       idx=idx)[name]
                for name, _ in STAGES}
        ranks = {name: first_golden(deep[name], case.golden_ids)
                 for name, _ in STAGES}

        lines.append(f"## {case.category} — {case.id}")
        lines.append("")
        lines.append(f"> {case.query}")
        lines.append("")
        if case.notes:
            lines.append(f"*{case.notes}*")
            lines.append("")
        if case.answerable:
            wanted = ", ".join(f"`{d} :: {h}`" for d, h in case.anchors)
            lines.append(f"Wanted: {wanted}")
        else:
            lines.append("Wanted: **nothing** — this question is not answerable "
                         "from the corpus.")
        lines.append("")

        for name, label in STAGES:
            lines.append(f"**{label}**")
            lines.append("")
            top = stages[name]
            if not top:
                lines.append("- *(nothing returned)*")
            for position, chunk_id in enumerate(top, start=1):
                chunk = idx.by_id[chunk_id]
                mark = "✓ " if chunk_id in case.golden_ids else "  "
                lines.append(f"{position}. {mark}`{chunk.doc}` :: "
                             f"{chunk.heading}")
            lines.append("")

        lines.append(f"**Verdict.** {verdict(ranks, case.answerable)}")
        lines.append("")
        lines.append("---")
        lines.append("")

    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--stdout", action="store_true")
    args = parser.parse_args()

    idx = index_module.get()
    everything = {c.id: c for c in anchors.load(idx=idx)}
    if ALWAYS not in everything:
        print(f"unknown case id: {ALWAYS}", file=sys.stderr)
        return 1

    cases, unanimous, total = select(idx, everything)
    text = render(idx, cases, unanimous, total)

    if args.stdout:
        print(text)
        return 0

    os.makedirs(TRACE_DIR, exist_ok=True)
    path = os.path.join(TRACE_DIR, "09-retrieval-stages.md")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    print(f"wrote {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
