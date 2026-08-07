"""Part 3: the judged generation metrics, with reranking off and on.

    python -m eval.run_generation                # full set, both configurations
    python -m eval.run_generation --limit 4      # first 4 cases, for a smoke test
    python -m eval.run_generation --off-subset 1 # stratified subset for rerank-off

Writes `eval/results/generation.md` and `eval/results/generation.json`.

Unlike the retrieval metrics, every number here costs model calls — roughly ten
per case per configuration, most of them the per-passage context-precision
judgements. They are cached to `eval/cache/judge.json` by content hash, so the
first run is expensive and every run after it is free and identical. The cache
is committed for that reason.

`--off-subset N` takes N cases from each category for the un-reranked run, which
is the concession the assignment allows when a full second pass is not
affordable. It is stratified rather than truncated on purpose: a cheaper run
must still span every category, or it will quietly drop the ones the system is
worst at. Whichever was used is written into the report.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from dotenv import load_dotenv  # noqa: E402

load_dotenv()

from eval import anchors, answer as answerer, tables  # noqa: E402
from eval.metrics import judge  # noqa: E402

RESULTS = os.path.join(os.path.dirname(__file__), "results")
K = 5

METRICS = ["faithfulness", "answer_relevance", "context_precision", "context_recall"]
PRETTY = {"faithfulness": "faithfulness",
          "answer_relevance": "answer relevance",
          "context_precision": "context precision",
          "context_recall": "context recall"}


def score_case(case, rerank: bool) -> dict:
    """Answer the case and judge it. Every value may be None (undefined)."""
    produced = answerer.generate(case.id, case.query, k=K, rerank=rerank)
    row = {
        "answer": produced.answer,
        "chunk_ids": produced.chunk_ids,
        "faithfulness": judge.faithfulness(produced.answer, produced.contexts),
        "answer_relevance": judge.answer_relevance(case.query, produced.answer),
        "context_precision": judge.context_precision(case.query, produced.contexts),
        "context_recall": judge.context_recall(case.golden_answer, produced.contexts),
    }
    if case.category == "out-of-corpus":
        row["abstained"] = judge.abstained(produced.answer)
    return row


def mean(values: list[float | None]) -> tuple[float | None, int]:
    """Mean over the defined values, and how many were undefined."""
    defined = [v for v in values if v is not None]
    undefined = len(values) - len(defined)
    if not defined:
        return None, undefined
    return sum(defined) / len(defined), undefined


def build_report(cases, scored: dict[bool, dict[str, dict]],
                 configs: list[bool], off_cases,
                 swapped: dict[str, dict] | None = None) -> tuple[str, dict]:
    lines: list[str] = []
    payload: dict = {
        "generator": answerer.GENERATOR_MODEL,
        "judge": judge.JUDGE_MODEL,
        "judge_temperature": judge.JUDGE_TEMPERATURE,
        "k": K,
        "results": {},
        "per_case": {},
    }

    lines.append("### Generation metrics\n")
    lines.append(
        f"Generator **{answerer.GENERATOR_MODEL}**, judge "
        f"**{judge.JUDGE_MODEL}** at temperature {judge.JUDGE_TEMPERATURE:g}, "
        f"k={K}. Deliberately different models, because a model scores its own "
        "output generously. Two caveats, stated rather than buried: the judge "
        "is the *smaller* of the two — `gemini-2.5-pro` was the first choice "
        "and returns 503 under free-tier load often enough that a run could not "
        "finish, and a judge that cannot be re-run is not reproducible. And it "
        "is the same vendor and family, so a shared prior about what a good "
        "answer looks like is reduced, not removed. The judge-sensitivity table "
        "below puts a number on how much of that is left.\n")

    if len(off_cases) != len(cases):
        names = ", ".join(sorted({c.category for c in off_cases}))
        lines.append(
            f"The un-reranked run uses a stratified subset of "
            f"{len(off_cases)} of {len(cases)} cases, spanning every category "
            f"({names}). The reranked run uses all {len(cases)}.\n")

    # ---- headline table ---------------------------------------------------
    rows = []
    for cfg in configs:
        label = "reranked" if cfg else "no rerank"
        subset = cases if cfg else off_cases
        cells = [label, str(len(subset))]
        entry: dict = {}
        for metric in METRICS:
            value, undefined = mean([scored[cfg][c.id][metric] for c in subset])
            cells.append(tables.num(value))
            entry[metric] = {"mean": value, "undefined": undefined}
        rows.append(cells)
        payload["results"][label] = entry
    lines.append(tables.table(
        ["configuration", "cases"] + [PRETTY[m] for m in METRICS], rows))

    if len(configs) == 2:
        lines.append("\n**What reranking changed**\n")
        cells = ["delta"]
        for metric in METRICS:
            on, _ = mean([scored[True][c.id][metric] for c in off_cases])
            off, _ = mean([scored[False][c.id][metric] for c in off_cases])
            cells.append(tables.delta(on, off) if on is not None and off is not None
                         else "—")
        lines.append(tables.table(["", *[PRETTY[m] for m in METRICS]], [cells]))
        lines.append(
            "\nBoth columns are over the same cases, so the delta is a like-for-"
            "like comparison even when the two runs covered different amounts.\n")

    # ---- per category -----------------------------------------------------
    best = configs[-1]
    lines.append(f"\n**By category** ({'reranked' if best else 'no rerank'})\n")
    rows = []
    for category, group in anchors.by_category(cases).items():
        cells = [category, str(len(group))]
        for metric in METRICS:
            value, undefined = mean([scored[best][c.id][metric] for c in group])
            cells.append(tables.num(value) if value is not None
                         else f"— ({undefined})")
        rows.append(cells)
        payload["results"].setdefault("by_category", {})[category] = {
            m: mean([scored[best][c.id][m] for c in group])[0] for m in METRICS}
    lines.append(tables.table(
        ["category", "cases"] + [PRETTY[m] for m in METRICS], rows))
    lines.append(
        "\nAn average over a mixed set hides which category the system is bad "
        "at, which is the reason the cases are tagged at all.\n")

    # ---- abstention on the out-of-corpus cases ----------------------------
    ooc = [c for c in cases if c.category == "out-of-corpus"]
    if ooc:
        lines.append("\n**Out-of-corpus: did it decline?**\n")
        rows = []
        for case in ooc:
            row = [case.id]
            for cfg in configs:
                if case.id not in scored[cfg]:
                    row.append("—")
                    continue
                value = scored[cfg][case.id].get("abstained")
                row.append("declined" if value else "answered anyway")
            rows.append(row)
        lines.append(tables.table(
            ["case"] + [("reranked" if c else "no rerank") for c in configs], rows))
        lines.append(
            "\nThese cases have no golden context, so every ranking metric is "
            "undefined on them. Whether the answerer invents something is the "
            "only thing there is to measure, and it is the thing that matters.\n")

    # ---- judge-swap sensitivity -------------------------------------------
    if swapped:
        lines.append(f"\n**Judge sensitivity** — the same {len(swapped)} answers, "
                     f"re-judged by {answerer.GENERATOR_MODEL}, the model that "
                     "wrote them\n")
        rows = []
        for label, source in (("different judge", scored[best]), ("own model", swapped)):
            cells = [label]
            for metric in METRICS:
                value, _ = mean([source[cid][metric] for cid in swapped])
                cells.append(tables.num(value))
            rows.append(cells)
        cells = ["self-preference"]
        for metric in METRICS:
            own, _ = mean([swapped[cid][metric] for cid in swapped])
            other, _ = mean([scored[best][cid][metric] for cid in swapped])
            cells.append(tables.delta(own, other)
                         if own is not None and other is not None else "—")
        rows.append(cells)
        lines.append(tables.table(["judge", *[PRETTY[m] for m in METRICS]], rows))
        lines.append(
            "\nA positive bottom row is the generator's own model scoring its "
            "own output more generously than an independent one does — "
            "self-preference bias, measured rather than assumed. It bounds how "
            "much of the headline number is the judge liking itself.\n")
        payload["judge_swap"] = {
            "judge": answerer.GENERATOR_MODEL,
            "cases": sorted(swapped),
            "scores": {cid: {m: swapped[cid][m] for m in METRICS}
                       for cid in swapped},
        }

    # ---- per case ---------------------------------------------------------
    lines.append(f"\n**Per case** ({'reranked' if best else 'no rerank'})\n")
    rows = []
    for case in cases:
        entry = scored[best][case.id]
        rows.append([case.id, case.category]
                    + [tables.num(entry[m]) for m in METRICS])
        payload["per_case"][case.id] = {
            "category": case.category,
            **{m: scored[best][case.id][m] for m in METRICS},
            "answer": scored[best][case.id]["answer"],
            "chunk_ids": scored[best][case.id]["chunk_ids"],
        }
    lines.append(tables.table(
        ["case", "category"] + [PRETTY[m] for m in METRICS], rows))

    return "\n".join(lines) + "\n", payload


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--limit", type=int, default=0,
                        help="only the first N cases (smoke test)")
    parser.add_argument("--off-subset", type=int, default=0,
                        help="cases per category for the un-reranked run "
                             "(0 = all of them)")
    parser.add_argument("--no-rerank-run", action="store_true",
                        help="skip the un-reranked configuration entirely")
    parser.add_argument("--judge-swap", type=int, default=0, metavar="N",
                        help="re-judge N cases per category with the "
                             "generator's own model, to measure self-preference")
    parser.add_argument("--out", default=RESULTS)
    args = parser.parse_args()

    cases = anchors.load()
    if args.limit:
        cases = cases[:args.limit]

    off_cases = (anchors.stratified(cases, args.off_subset)
                 if args.off_subset else cases)
    configs = [True] if args.no_rerank_run else [False, True]

    scored: dict[bool, dict[str, dict]] = {}
    for cfg in configs:
        subset = cases if cfg else off_cases
        label = "reranked" if cfg else "no rerank"
        scored[cfg] = {}
        for n, case in enumerate(subset, 1):
            print(f"  [{label}] {n}/{len(subset)} {case.id}", flush=True)
            scored[cfg][case.id] = score_case(case, cfg)

    # The sensitivity run: identical answers, identical scorers, one thing
    # changed — who is judging. Rebinding the module attribute is what the rest
    # of this codebase does to swap a model, and `_ask` reads it per call.
    swapped: dict[str, dict] = {}
    if args.judge_swap:
        original = judge.JUDGE_MODEL
        judge.JUDGE_MODEL = answerer.GENERATOR_MODEL
        try:
            for case in anchors.stratified(cases, args.judge_swap):
                print(f"  [judge-swap] {case.id}", flush=True)
                swapped[case.id] = score_case(case, configs[-1])
        finally:
            judge.JUDGE_MODEL = original

    report, payload = build_report(cases, scored, configs, off_cases, swapped)

    os.makedirs(args.out, exist_ok=True)
    with open(os.path.join(args.out, "generation.md"), "w", encoding="utf-8") as fh:
        fh.write(report)
    with open(os.path.join(args.out, "generation.json"), "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, sort_keys=True)

    print("\n" + report)
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
