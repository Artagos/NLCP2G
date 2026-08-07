"""The agent eval report: tables, and the sentences that stop them misleading.

Rendering only — every number arrives already computed. It reuses
`eval/tables.py` so an undefined value prints as an em dash here exactly as it
does in the retrieval and generation reports, and nobody has to remember which
report renders `None` as `0.000`.
"""
from __future__ import annotations

from eval import tables
from eval.metrics import judge

from . import metrics as metrics_module
from . import scenario as scenario_module
from . import scorers

PRETTY = {
    "tool_selection": "tool selection",
    "tool_parameters": "tool parameters",
    "goal_completion": "goal completion",
    "trajectory_precision": "trajectory precision",
    "trajectory_recall": "trajectory recall",
}
RAG_METRICS = ["faithfulness", "answer_relevance", "context_precision",
               "context_recall"]


def _mean(values):
    defined = [v for v in values if v is not None]
    return (sum(defined) / len(defined)) if defined else None


def build(scenarios, traces, scored, temperature: float, runs: int,
          offline: bool) -> tuple[str, dict]:
    rows = scored["rows"]
    per_scenario = scored["per_scenario"]
    by_id = {s.id: s for s in scenarios}

    rates = metrics_module.pass_rates(per_scenario)
    every = [s for group in per_scenario.values() for s in group]
    overall = metrics_module.aggregate(every)

    # Offline scoring is handed traces, not a run plan, so the requested `runs`
    # says nothing about what is actually in the file. Report what was measured.
    runs = rates.runs_per_scenario or runs

    models = sorted({m for r in rows for m in r.get("models") or []})
    lines: list[str] = ["### Agent evaluation\n"]
    lines.append(
        f"{len(per_scenario)} scenarios x {runs} runs = {len(rows)} traced "
        f"turns, at temperature **{temperature:g}**, judged by "
        f"**{judge.JUDGE_MODEL}**. Models exercised: "
        f"{', '.join(f'`{m}`' for m in models) or 'none recorded'}.\n")
    lines.append(
        "The temperature is stated because it is the reason three runs can "
        "differ at all — but it was not raised *from* anything. This project "
        "never pinned one: `llm.chat_model()` passes no `temperature`, so the "
        "tutor has always run at the provider default. Setting it explicitly "
        "puts the number on the record instead of leaving it inherited.\n")
    if offline:
        lines.append(
            "Scored **offline** from the committed traces in "
            "`eval/traces/agent.jsonl` — no agent was run and no API key was "
            "needed to produce this table.\n")

    # ---- the three pass rates -------------------------------------------
    lines.append("\n**Pass rates**\n")
    lines.append(tables.table(
        ["pass@1", f"pass@{runs}", f"pass^{runs}", "scenarios", "runs"],
        [[tables.num(rates.pass_at_1), tables.num(rates.pass_at_3),
          tables.num(rates.pass_hat_3), str(len(per_scenario)), str(len(rows))]]))
    lines.append(
        f"\nA run passes when it reached for an acceptable set of tools **and** "
        f"the reply achieved the scenario's stated outcome. At n={runs} "
        f"`pass@{runs}` only asks whether the agent *ever* succeeded, so a "
        f"scenario it fails on {max(runs - 1, 0)} of {runs} runs still scores "
        f"1.0 — on its own it says almost nothing. `pass^{runs}` asks whether it "
        f"succeeded *every* time, and that is the number worth putting in front "
        f"of a learner.\n")

    if rates.flaky:
        lines.append(f"\n**Flaky: {len(rates.flaky)} scenario(s) passed on some "
                     f"runs and failed on others.**\n")
        flaky_rows = []
        for sid in rates.flaky:
            pattern = "".join("✓" if s.passed else "✗" for s in per_scenario[sid])
            reasons = []
            for run_scores in per_scenario[sid]:
                if not run_scores.passed:
                    if run_scores.tool_selection != 1.0:
                        reasons.append("tool selection")
                    elif run_scores.goal_completion != 1.0:
                        reasons.append("goal completion")
            trajectories = {" → ".join(r["trajectory"]) or "(no tools)"
                            for r in rows if r["scenario"] == sid}
            flaky_rows.append([sid, by_id[sid].category, pattern,
                               ", ".join(sorted(set(reasons))) or "—",
                               " / ".join(sorted(trajectories))])
        lines.append(tables.table(
            ["scenario", "category", "runs", "what failed", "trajectories seen"],
            flaky_rows))
        lines.append(
            "\nThe trajectories column is what varied. A scenario that is "
            "3-for-3 or 0-for-3 everywhere would mean the set is too easy or "
            "the tolerance too loose; these are the cases where the agent "
            "genuinely made a different decision on different runs.\n")
    else:
        lines.append(
            "\n**No scenario was flaky** — every one was all-pass or all-fail "
            "across its runs. That is a finding about the eval set as much as "
            "about the agent: either the scenarios do not probe anything the "
            "model is uncertain about, or the pass predicate is too loose to "
            "notice the variation. Both are reasons to tighten, not to "
            "celebrate.\n")

    # ---- the metric table -----------------------------------------------
    lines.append("\n**Trajectory metrics** (mean over defined values)\n")
    header, cells = ["metric", "mean", "undefined"], []
    for name in metrics_module.METRIC_NAMES:
        cells.append([PRETTY[name], tables.num(overall.means[name]),
                      str(overall.undefined[name]) or "0"])
    lines.append(tables.table(header, cells))
    lines.append(
        "\nUndefined is not zero. A refusal scenario expects **no** tool call, "
        "so there is nothing for trajectory recall to divide by and no argument "
        "to check; those runs are excluded from the mean and counted here "
        "instead. Scoring them zero would make the guardrail look like a "
        "failure every time it worked.\n")

    # ---- per category ----------------------------------------------------
    lines.append("\n**By category**\n")
    cells = []
    for category, group in scenario_module.by_category(scenarios).items():
        ids = [s.id for s in group if s.id in per_scenario]
        if not ids:
            continue
        group_scores = [s for sid in ids for s in per_scenario[sid]]
        agg = metrics_module.aggregate(group_scores)
        passed = sum(1 for s in group_scores if s.passed)
        cells.append([category, str(len(ids)),
                      f"{passed}/{len(group_scores)}",
                      *[tables.num(agg.means[n])
                        for n in metrics_module.METRIC_NAMES]])
    lines.append(tables.table(
        ["category", "scenarios", "runs passed",
         *[PRETTY[n] for n in metrics_module.METRIC_NAMES]], cells))
    lines.append(
        "\nAn average over a mixed set hides which kind of task the agent is "
        "bad at, which is the reason the scenarios are tagged at all.\n")

    # ---- per scenario ----------------------------------------------------
    lines.append("\n**Per scenario**\n")
    cells = []
    for scenario in scenarios:
        group = per_scenario.get(scenario.id)
        if not group:
            continue
        pattern = "".join("✓" if s.passed else "✗" for s in group)
        agg = metrics_module.aggregate(group)
        latency = _mean([r["latency_ms"] for r in rows
                         if r["scenario"] == scenario.id])
        tokens = _mean([(r["tokens"] or {}).get("total")
                        for r in rows if r["scenario"] == scenario.id])
        cells.append([
            scenario.id, scenario.category, pattern,
            tables.num(agg.means["tool_selection"]),
            tables.num(agg.means["goal_completion"]),
            f"{latency / 1000:.1f}s" if latency else "—",
            f"{int(tokens)}" if tokens else "—",
        ])
    lines.append(tables.table(
        ["scenario", "category", "runs", "tool selection", "goal completion",
         "latency", "tokens"], cells))

    # ---- the HW5 scorers, over traces ------------------------------------
    rag_rows = [r for r in rows if r.get("rag")]
    if rag_rows:
        lines.append(
            f"\n**The HW5 judged scorers, run over traces** (run "
            f"{scored.get('rag_run', 1)} of each scenario)\n")
        cells = []
        for name in RAG_METRICS:
            values = [r["rag"].get(name) for r in rag_rows]
            defined = [v for v in values if v is not None]
            cells.append([name.replace("_", " "), tables.num(_mean(values)),
                          str(len(values) - len(defined))])
        lines.append(tables.table(["metric", "mean", "undefined"], cells))
        lines.append(
            "\nThese are the *same scorer functions* HW5 used, unmodified — "
            "`eval/metrics/judge.py` has an empty diff in this increment. Only "
            "the input changed: an adapter lifts the question, the answer and "
            "the retrieved passages off a trace instead of out of a case file. "
            "They are undefined on turns that never retrieved (a refusal, or a "
            "memory question), because there is no context for an answer to be "
            "faithful *to* — and reporting 0.000 there would say the tutor "
            "hallucinated when it correctly declined to search.\n")

    payload = {
        "temperature": temperature,
        "runs": runs,
        "judge": judge.JUDGE_MODEL,
        "goal_tag": scorers.GOAL_TAG,
        "models": models,
        "offline": offline,
        "pass_at_1": rates.pass_at_1,
        f"pass_at_{runs}": rates.pass_at_3,
        f"pass_hat_{runs}": rates.pass_hat_3,
        "flaky": rates.flaky,
        "means": overall.means,
        "undefined": overall.undefined,
        "rows": rows,
    }
    return "\n".join(lines) + "\n", payload
