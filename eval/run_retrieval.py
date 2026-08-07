"""Part 2: the five rank metrics, swept over k, with reranking off and on.

    python -m eval.run_retrieval                 # k = 3, 5, 10, both configs
    python -m eval.run_retrieval --k 5           # one k
    python -m eval.run_retrieval --no-rerank     # skip the reranked half

Writes `eval/results/retrieval.md` and `eval/results/retrieval.json`.

These metrics cost nothing to compute and are exactly reproducible: with the
committed embedding and rerank caches, this runs with no API key and produces
byte-identical output every time. That is checked rather than asserted — see the
`--check-reproducible` flag, which re-runs and diffs against what is on disk.

One retrieval call per (case, config) rather than per (case, config, k): the
pipeline is identical for every k and only the final slice differs, so
retrieving the deepest k and slicing gives exactly the same results as
retrieving each k separately, without re-running the reranker three times.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from dotenv import load_dotenv  # noqa: E402

load_dotenv()

from backend.rag import index as index_module, rerank as rerank_module  # noqa: E402
from backend.rag import retriever  # noqa: E402
from backend.rag.fusion import RRF_K  # noqa: E402
from eval import anchors, tables  # noqa: E402
from eval.metrics import rank  # noqa: E402

DEFAULT_KS = [3, 5, 10]
RESULTS = os.path.join(os.path.dirname(__file__), "results")

PRETTY = {"hit_rate": "hit rate@k", "precision": "precision@k",
          "recall": "recall@k", "mrr": "MRR", "ndcg": "nDCG@k"}


def retrieve_all(cases, depth: int, rerank: bool) -> dict[str, list[str]]:
    """Every case's ranked chunk ids, in retriever order.

    Retriever order, not packed order — see the note in eval/metrics/rank.py.
    """
    return {case.id: [r.chunk_id for r in retriever.search(
        case.query, k=depth, rerank=rerank)] for case in cases}


def score(cases, retrieved: dict[str, list[str]], k: int) -> dict[str, rank.CaseScores]:
    return {case.id: rank.score_case(retrieved[case.id], case.golden_ids, k)
            for case in cases}


def build_report(cases, runs: dict[bool, dict[str, list[str]]],
                 ks: list[int], configs: list[bool]) -> tuple[str, dict]:
    idx = index_module.get()
    scored = {(cfg, k): score(cases, runs[cfg], k) for cfg in configs for k in ks}

    lines: list[str] = []
    payload: dict = {
        "corpus": {"documents": len({c.doc for c in idx.chunks}),
                   "chunks": len(idx.chunks)},
        "parameters": {"dense_n": retriever.DENSE_N,
                       "lexical_n": retriever.LEXICAL_N,
                       "rrf_k": RRF_K,
                       "rerank_depth": rerank_module.RERANK_DEPTH},
        "cases": {"total": len(cases),
                  "answerable": sum(1 for c in cases if c.answerable),
                  "empty_golden": sum(1 for c in cases if not c.answerable)},
        "results": {},
    }

    lines.append("### Retrieval metrics\n")
    lines.append(
        f"{len(idx.chunks)} chunks from "
        f"{payload['corpus']['documents']} documents. Retrieve "
        f"{retriever.DENSE_N} dense + {retriever.LEXICAL_N} lexical, fuse with "
        f"RRF (k={RRF_K}), rerank the top {rerank_module.RERANK_DEPTH}.\n")

    answerable = [c for c in cases if c.answerable]
    lines.append(
        f"Averaged over the {len(answerable)} answerable cases. The "
        f"{len(cases) - len(answerable)} out-of-corpus cases have an empty "
        "golden set, so all five metrics are undefined on them; they are "
        "excluded here and measured in the generation table instead.\n")

    # Precision@k is bounded above by |golden|/k, and these cases have one or
    # two golden chunks each. Without the ceiling printed next to it, a
    # precision@10 of 0.138 reads as poor retrieval when it is in fact 92% of
    # everything achievable. Stating the bound is the difference between a
    # number that informs and a number that misleads.
    ceilings = {k: sum(min(k, len(c.golden_ids)) / k for c in answerable)
                   / len(answerable) for k in ks}
    lines.append(
        "**Reading precision@k.** Each case has one or two golden chunks, so "
        "precision@k cannot exceed |golden|/k. The ceiling is "
        + ", ".join(f"{tables.num(ceilings[k])} at k={k}" for k in ks)
        + " — compare the precision column against those, not against 1.0.\n")
    payload["precision_ceiling"] = {f"k={k}": ceilings[k] for k in ks}

    # ---- the main grid ----------------------------------------------------
    for cfg in configs:
        label = "reranked" if cfg else "no rerank"
        rows = []
        for k in ks:
            agg = rank.aggregate(list(scored[(cfg, k)].values()))
            rows.append([str(k)] + [tables.num(agg.means[m]) for m in rank.METRIC_NAMES])
            payload["results"].setdefault(label, {})[f"k={k}"] = {
                "means": agg.means, "scored": agg.scored, "undefined": agg.undefined}
        lines.append(f"\n**{label}**\n")
        lines.append(tables.table(
            ["k"] + [PRETTY[m] for m in rank.METRIC_NAMES], rows))

    # ---- what reranking changed ------------------------------------------
    if len(configs) == 2:
        lines.append("\n**What reranking changed**\n")
        rows = []
        for k in ks:
            off = rank.aggregate(list(scored[(False, k)].values())).means
            on = rank.aggregate(list(scored[(True, k)].values())).means
            rows.append([str(k)] + [tables.delta(on[m], off[m])
                                    for m in rank.METRIC_NAMES])
        lines.append(tables.table(
            ["k"] + [PRETTY[m] for m in rank.METRIC_NAMES], rows))

    # ---- per category, at the middle k ------------------------------------
    focus = ks[len(ks) // 2]
    best = configs[-1]
    lines.append(f"\n**By category** (k={focus}, "
                 f"{'reranked' if best else 'no rerank'})\n")
    rows = []
    for category, group in anchors.by_category(cases).items():
        agg = rank.aggregate([scored[(best, focus)][c.id] for c in group])
        if agg.scored == 0:
            rows.append([category, f"{agg.undefined} (undefined)"]
                        + ["—"] * len(rank.METRIC_NAMES))
            continue
        rows.append([category, str(agg.scored)]
                    + [tables.num(agg.means[m]) for m in rank.METRIC_NAMES])
        payload["results"].setdefault("by_category", {})[category] = agg.means
    lines.append(tables.table(
        ["category", "cases"] + [PRETTY[m] for m in rank.METRIC_NAMES], rows))

    # ---- per case, so a bad average can be traced to a case ---------------
    lines.append(f"\n**Per case** (k={focus}, "
                 f"{'reranked' if best else 'no rerank'})\n")
    rows = []
    for case in cases:
        s = scored[(best, focus)][case.id]
        rows.append([case.id, case.category]
                    + [tables.num(v) for v in
                       (s.hit_rate, s.precision, s.recall, s.mrr, s.ndcg)])
        payload.setdefault("per_case", {})[case.id] = {
            "category": case.category,
            "scores": s.as_dict(),
            "retrieved": runs[best][case.id][:focus],
            "golden": sorted(case.golden_ids),
        }
    lines.append(tables.table(
        ["case", "category"] + [PRETTY[m] for m in rank.METRIC_NAMES], rows))

    return "\n".join(lines) + "\n", payload


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--k", type=int, nargs="+", default=DEFAULT_KS)
    parser.add_argument("--no-rerank", action="store_true",
                        help="only the un-reranked configuration")
    parser.add_argument("--out", default=RESULTS)
    parser.add_argument("--check-reproducible", action="store_true",
                        help="re-run and fail if the output differs from disk")
    args = parser.parse_args()

    ks = sorted(set(args.k))
    configs = [False] if args.no_rerank else [False, True]

    idx = index_module.get()
    cases = anchors.load(idx=idx)
    depth = max(ks)

    runs = {cfg: retrieve_all(cases, depth, cfg) for cfg in configs}
    report, payload = build_report(cases, runs, ks, configs)

    md_path = os.path.join(args.out, "retrieval.md")
    json_path = os.path.join(args.out, "retrieval.json")

    if args.check_reproducible:
        if not os.path.exists(md_path):
            print(f"nothing to compare against at {md_path}", file=sys.stderr)
            return 1
        with open(md_path, encoding="utf-8") as fh:
            existing = fh.read()
        if existing != report:
            print("REPRODUCIBILITY FAILURE: this run differs from "
                  f"{md_path}", file=sys.stderr)
            return 1
        print(f"reproduced {md_path} exactly")
        return 0

    os.makedirs(args.out, exist_ok=True)
    with open(md_path, "w", encoding="utf-8") as fh:
        fh.write(report)
    with open(json_path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, sort_keys=True)

    print(report)
    print(f"wrote {md_path} and {json_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
