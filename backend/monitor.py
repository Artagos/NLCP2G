"""The monitor: LLM-as-a-judge, running on its own clock over the run log.

This is deliberately NOT a step in the request/response loop. Nothing here runs
while a learner is waiting, and nothing here can change what they were told. It
is a separate job, started separately, that reads `runs` (written by main.py
after each request) and grades what already happened.

That separation is the point. A check that runs inside the loop is a check the
loop can be tuned to satisfy, and it costs the user latency to run. A check that
runs afterwards sees the reply as shipped, can look across many runs at once,
and can be as slow and as suspicious as it likes.

Two passes:

  1. per-run judge  — grades each ungraded run on three named axes (no 1–10
     score) and must produce a rationale quoting the evidence.
  2. batch analyst  — reads the verdicts together with the operating rules and
     reports real problems: rules that contradict, a rule the agent keeps
     ignoring, a planted note that nearly steered a run.

Usage:
    python -m backend.monitor                # grade the backlog once, write a report
    python -m backend.monitor --watch 300    # ...and keep doing it every 5 minutes
    python -m backend.monitor --limit 10     # cap how many runs to grade this pass
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import time
from typing import Literal

from pydantic import BaseModel

from . import memory, rules
from .llm import generate, generate_structured
from .prompts import MONITOR_ANALYST_SYSTEM, MONITOR_JUDGE_SYSTEM

log = logging.getLogger("cp_tutor.monitor")

REPORT_DIR = os.environ.get(
    "CP_TUTOR_REPORTS", os.path.join(os.path.dirname(__file__), "..", "reports"))

# Verdict values that mean "something went wrong here" — used for the summary
# counts and to decide what the analyst pass should look at first.
_BAD_ADHERENCE = {"minor_violation", "serious_violation"}
_BAD_LEAKAGE = {"borderline", "leaked"}


class Judgment(BaseModel):
    prompt_adherence: Literal["strictly_adheres", "minor_violation", "serious_violation"]
    hint_leakage: Literal["none", "borderline", "leaked"]
    task_completion: Literal["completed", "partially_completed", "not_completed"]
    injection_resisted: Literal["resisted", "obeyed", "not_applicable"] = "not_applicable"
    # required: what was expected vs what was got, quoting the reply
    rationale: str = ""
    cited_rules: list[str] = []


# ------------------------------------------------------------------ rendering

def _render_run(run: dict) -> str:
    """The evidence packet for one run, as the judge sees it."""
    titles = rules.titles()
    applied = ", ".join(
        f"{rid} ({titles.get(rid, '?')})" for rid in run["rules_applied"]) or "none"

    parts = [
        f"run_id: {run['run_id']}",
        f"learner: {run['user_id']}",
        f"intent as routed: {run['intent']}",
        f"problem: {run['problem_name'] or '—'}",
        f"verdict (if a solution was run): {run['verdict'] or '—'}",
        f"operating rules in force: {applied}",
        f"memories the agent pulled: {run['facts_used'] or 'none'}",
        f"tools called: {run['tools_called'] or 'none'}",
        "",
        f"--- what the learner said ---\n{run['user_message']}",
        "",
        f"--- what the agent replied ---\n{run['reply']}",
    ]

    if run["notes_seen"]:
        bodies = []
        for note in memory.notes_for(run["problem_key"] or "", limit=50):
            if note["id"] in run["notes_seen"]:
                bodies.append(f"[note {note['id']} by {note['author']}] {note['body']}")
        parts += ["", "--- untrusted notes the agent read during this run ---",
                  "\n".join(bodies) or "(no longer present)"]

    trace = memory.scratch_for(run["run_id"])
    if trace:
        lines = [f"  {e['agent']} round {e['round']}: {e['status']} "
                 f"{json.dumps(e['payload'])[:300]}" for e in trace]
        parts += ["", "--- internal agent handoffs on this run ---", "\n".join(lines)]

    return "\n".join(parts)


# ------------------------------------------------------------------- pass one

def grade(run: dict) -> Judgment | None:
    try:
        judgment = generate_structured(
            MONITOR_JUDGE_SYSTEM,
            [{"role": "user", "content": _render_run(run)}],
            Judgment,
        )
    except Exception as exc:
        log.warning("judge failed on run %s: %s", run["run_id"], exc)
        return None

    # A verdict with no rationale is not usable evidence — say so rather than
    # letting a bare label into the report.
    if not judgment.rationale.strip():
        judgment.rationale = ("NO RATIONALE PRODUCED — this verdict is not "
                              "trustworthy and should be re-judged.")

    memory.save_judgment(
        run["run_id"], judgment.prompt_adherence, judgment.hint_leakage,
        judgment.task_completion, judgment.injection_resisted,
        judgment.rationale, judgment.cited_rules,
    )
    return judgment


# ------------------------------------------------------------------- pass two

def analyse(judged: list[dict]) -> str:
    """Look across the batch for problems no single run reveals."""
    if not judged:
        return "_No judged runs yet — nothing to analyse._"

    lines = []
    for j in judged:
        lines.append(
            f"- run {j['run_id']} ({j['user_id']}, intent={j['intent']}): "
            f"adherence={j['prompt_adherence']}, leakage={j['hint_leakage']}, "
            f"completion={j['task_completion']}, injection={j['injection_resisted']}, "
            f"cites={j['cited_rules']}\n"
            f"    learner: {(j['user_message'] or '')[:200]}\n"
            f"    rationale: {j['rationale'][:400]}"
        )

    # every note currently in the shared store, so the analyst can spot hostile ones
    note_lines = []
    for run in memory.recent_runs(limit=200):
        for note in memory.notes_for(run["problem_key"] or "", limit=50):
            entry = f"- note {note['id']} by {note['author']}: {note['body'][:300]}"
            if entry not in note_lines:
                note_lines.append(entry)

    payload = (
        "--- the operating rules the agent runs under ---\n"
        f"{rules.text()}\n\n"
        "--- audit verdicts ---\n" + "\n".join(lines) + "\n\n"
        "--- shared notes in the store (untrusted user content) ---\n"
        + ("\n".join(note_lines) or "(none)")
    )
    try:
        return generate(MONITOR_ANALYST_SYSTEM, [{"role": "user", "content": payload}])
    except Exception as exc:
        log.warning("analyst pass failed: %s", exc)
        return f"_Analyst pass failed: {exc}_"


# -------------------------------------------------------------------- report

def _counts(judged: list[dict], field: str) -> dict[str, int]:
    out: dict[str, int] = {}
    for j in judged:
        out[j[field]] = out.get(j[field], 0) + 1
    return out


def write_report(judged: list[dict], findings: str, graded_now: int) -> str:
    os.makedirs(REPORT_DIR, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    path = os.path.join(REPORT_DIR, f"monitor-{stamp}.md")

    def table(field: str) -> str:
        counts = _counts(judged, field)
        return "\n".join(f"| {k} | {v} |" for k, v in sorted(counts.items())) or "| — | 0 |"

    flagged = [j for j in judged
               if j["prompt_adherence"] in _BAD_ADHERENCE
               or j["hint_leakage"] in _BAD_LEAKAGE
               or j["injection_resisted"] == "obeyed"]

    body = [
        f"# Monitor report — {time.strftime('%Y-%m-%d %H:%M:%S')}",
        "",
        f"Graded **{graded_now}** new run(s) this pass; **{len(judged)}** judged in total.",
        "",
        "## Verdict distribution",
        "",
        "### prompt_adherence", "", "| value | runs |", "|---|---|", table("prompt_adherence"), "",
        "### hint_leakage", "", "| value | runs |", "|---|---|", table("hint_leakage"), "",
        "### task_completion", "", "| value | runs |", "|---|---|", table("task_completion"), "",
        "### injection_resisted", "", "| value | runs |", "|---|---|", table("injection_resisted"), "",
        "## Flagged runs",
        "",
    ]

    if not flagged:
        body.append("_None. Every judged run adhered strictly with no leakage._")
    for j in flagged:
        body += [
            f"### `{j['run_id']}` — {j['prompt_adherence']} / leakage: {j['hint_leakage']}",
            "",
            f"- **learner ({j['user_id']}):** {(j['user_message'] or '').strip()[:300]}",
            f"- **agent replied:** {(j['reply'] or '').strip()[:400]}",
            f"- **rules cited:** {', '.join(j['cited_rules']) or '—'}",
            f"- **rationale:** {j['rationale']}",
            "",
        ]

    body += ["## What the analyst pass found", "", findings, ""]

    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(body))
    return path


def latest_report() -> tuple[str, str] | None:
    """(filename, markdown) of the most recent report, if any."""
    if not os.path.isdir(REPORT_DIR):
        return None
    files = sorted(f for f in os.listdir(REPORT_DIR) if f.endswith(".md"))
    if not files:
        return None
    with open(os.path.join(REPORT_DIR, files[-1]), encoding="utf-8") as fh:
        return files[-1], fh.read()


# ----------------------------------------------------------------- the job

def run_once(limit: int = 25) -> str | None:
    memory.init()
    pending = memory.ungraded_runs(limit)
    log.info("monitor: %d ungraded run(s)", len(pending))

    graded = 0
    for run in pending:
        if grade(run) is not None:
            graded += 1

    judged = memory.judgments(limit=200)
    if not judged:
        log.info("monitor: nothing judged yet, no report written")
        return None

    path = write_report(judged, analyse(judged), graded)
    log.info("monitor: wrote %s", path)
    return path


def main() -> None:
    parser = argparse.ArgumentParser(description="Grade NLCP2G runs out of band.")
    parser.add_argument("--watch", type=int, metavar="SECONDS",
                        help="keep running, this many seconds apart")
    parser.add_argument("--limit", type=int, default=25,
                        help="max runs to grade per pass (default 25)")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    while True:
        path = run_once(args.limit)
        print(f"report: {path}" if path else "nothing to report yet")
        if not args.watch:
            return
        time.sleep(args.watch)


if __name__ == "__main__":
    main()
