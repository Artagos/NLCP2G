"""The evidence for HW6: span trees, trajectories, and a resisted attack.

    python -m scripts.demo_agent            # writes traces/10-agent-and-safety.md
    python -m scripts.demo_agent --stdout

Everything here is rendered from the **committed traces** — the same
`eval/traces/*.jsonl` the metrics are computed from — so the narrative cannot
drift from what the system did. Nothing is re-run and no API key is needed.

Which turns get shown is chosen from the data, not by hand: the flakiest
scenario (the one whose runs disagreed most), one clean multi-tool turn, and the
attacks whose detector output is most interesting. The first version of the HW5
trace picked cases by hand and seven of eight read "nothing changed"; the lesson
generalised.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

from eval.agent import store, trajectory  # noqa: E402
from eval.safety import detector  # noqa: E402

TRACE_DIR = os.path.join(os.path.dirname(__file__), "..", "traces")
AGENT_TRACES = os.path.join(os.path.dirname(__file__), "..", "eval", "traces",
                            "agent.jsonl")
SAFETY_TRACES = os.path.join(os.path.dirname(__file__), "..", "eval", "traces",
                             "safety.jsonl")
RESULTS = os.path.join(os.path.dirname(__file__), "..", "eval", "results")


def tree(trace, indent: str = "") -> list[str]:
    """The span tree, as MLflow's UI shows it, in text."""
    children: dict[str | None, list] = {}
    for span in trace.spans:
        children.setdefault(span.parent_id, []).append(span)

    lines: list[str] = []

    def walk(parent, depth):
        for span in sorted(children.get(parent, []), key=lambda s: s.start_ns):
            extra = ""
            if span.tokens:
                extra = (f"  tokens={span.tokens.get('total', '?')}"
                         f" model={span.model}")
            elif span.span_type == store.TOOL:
                name = span.attributes.get("gen_ai.tool.name") or span.name
                extra = f"  gen_ai.tool.name={name}"
            lines.append(f"{indent}{'  ' * depth}{span.span_type:<11} "
                         f"{span.name:<26} {span.duration_ms:7.0f}ms{extra}")
            walk(span.span_id, depth + 1)

    walk(None, 0)
    return lines


def reply_of(trace) -> str:
    outputs = getattr(trace.root, "outputs", None)
    return str((outputs or {}).get("reply", "")) if isinstance(outputs, dict) else ""


def pick_flakiest(traces, agent_json) -> str:
    """The scenario whose runs disagreed — chosen from the results, not by hand."""
    flaky = (agent_json or {}).get("flaky") or []
    return flaky[0] if flaky else ""


def render(agent_traces, safety_traces, agent_json, safety_json) -> str:
    out: list[str] = [
        "# The agent, traced — and what the traces caught",
        "",
        "Every block below is rendered from the committed traces in",
        "`eval/traces/`, by `scripts/demo_agent.py`. Nothing here was typed by",
        "hand and nothing was re-run to produce it: regenerate and it",
        "re-derives from the same files the metrics are computed from.",
        "",
        "---",
        "",
        "## 1. What one traced turn looks like",
        "",
        "The span tree Part 2 asks for: a root span for the turn, child spans",
        "for every model call, every tool execution and retrieval, with model",
        "name, token counts and latency on the spans that have them.",
        "",
    ]

    # Picked from the data: the richest tree, meaning one that actually
    # contains a tool execution AND a retrieval. Hardcoding a case id picked a
    # run where the model chose not to search, and the surrounding prose then
    # described a RETRIEVER span that was not in the diagram underneath it.
    def richness(t):
        kinds = {s.span_type for s in t.spans}
        return (store.TOOL in kinds, store.RETRIEVER in kinds, len(t.spans))

    showcase = max(agent_traces, key=richness, default=None)

    if showcase is not None:
        out += [f"**`{showcase.case_id}`** — "
                f"{showcase.latency_ms / 1000:.1f}s, "
                f"{showcase.tokens.get('total', 0)} tokens, "
                f"models {', '.join(showcase.models) or '—'}",
                "",
                "```",
                *tree(showcase),
                "```",
                "",
                "Two models appear because routing and tutoring are different",
                "jobs: the router runs on `gemini-2.5-flash-lite` and the tutor",
                "on `gemini-2.5-flash`.",
                ""]
        if any(s.span_type == store.RETRIEVER for s in showcase.spans):
            out += ["The `RETRIEVER` span is nested inside the `TOOL` span",
                    "because retrieval happened *because* the model chose to",
                    "search — the tree shows the causal order, not just a list",
                    "of things that happened. Neither span comes from autolog:",
                    "the retriever is numpy and BM25, not a LangChain runnable,",
                    "and the root span had to be opened by hand because a turn",
                    "is three graphs reached through plain function calls",
                    "rather than one Runnable.",
                    ""]
        out += [
                "Tags on this trace:",
                "",
                "```json",
                json.dumps({k: v for k, v in showcase.tags.items()
                            if not k.startswith("mlflow.")},
                           indent=2, sort_keys=True),
                "```",
                "",
                "`request_origin` separates eval runs from real conversations;",
                "`eval_case_id` is the join key that ties this trace back to the",
                "scenario that produced it — which is how the metrics find it,",
                "rather than by assuming the most recent trace is the right one.",
                "A single `/chat` produces more than one trace: the reflector",
                "runs after the turn and autolog gives it a root of its own.",
                "",
                "**The trajectory this reduces to:**",
                "",
                "```",
                *([f"  {c}" for c in trajectory.tool_calls(showcase)]
                  or ["  (no tools)"]),
                "```",
                "",
                "Arguments, never results — see `eval/agent/trajectory.py`.",
                "",
                "---",
                ""]

    # ---- the flaky scenario ------------------------------------------------
    flaky_id = pick_flakiest(agent_traces, agent_json)
    if flaky_id:
        runs = sorted((t for t in agent_traces
                       if t.case_id.startswith(flaky_id + "#")),
                      key=lambda t: t.case_id)
        out += ["## 2. A scenario that did not behave the same way twice",
                "",
                f"`{flaky_id}` — the same question, {len(runs)} times, at "
                f"temperature {agent_json.get('temperature', '?')}.",
                ""]
        for t in runs:
            calls = trajectory.tool_names(t) or ["(no tools)"]
            row = next((r for r in (agent_json.get("rows") or [])
                        if r["trace_id"] == t.trace_id), {})
            verdict = "passed" if row.get("passed") else "FAILED"
            out += [f"**{t.case_id}** — {verdict}; trajectory "
                    f"`{' → '.join(calls)}`",
                    "",
                    "> " + (reply_of(t)[:280].replace("\n", " ") or "(empty reply)"),
                    ""]
        out += ["This is the case requirement 6 asks for, and it is why three",
                "runs are worth more than one. A single run of this scenario",
                "would have reported whichever answer it happened to get, and",
                "`pass^3` is the only one of the three headline numbers that",
                "notices the difference.",
                "",
                "---",
                ""]

    # ---- safety -------------------------------------------------------------
    if safety_traces:
        rows = {r["attack"]: r for r in (safety_json.get("rows") or [])}
        out += ["## 3. Attacks, and what the detector said",
                "",
                "Each of these was posted to the running system exactly as a",
                "learner would post it. `resisted` means two things agreed: the",
                "judge found the reply did not do what the attacker demanded,",
                "**and** the trace detector raised no alert.",
                ""]
        interesting = sorted(
            safety_traces,
            key=lambda t: (0 if not rows.get(t.case_id.split("#")[0], {}).get("resisted")
                           else 1, t.case_id))
        for t in interesting[:4]:
            attack_id = t.case_id.split("#")[0]
            row = rows.get(attack_id, {})
            found = detector.inspect(t)
            out += [f"### `{attack_id}` — {row.get('category', '?')}",
                    "",
                    f"**Verdict:** {'resisted' if row.get('resisted') else 'COMPLIED'}",
                    "",
                    "Tools it reached for: "
                    f"`{' → '.join(trajectory.tool_names(t)) or '(none)'}`",
                    "",
                    "> " + (reply_of(t)[:300].replace("\n", " ") or "(empty reply)"),
                    "",
                    "Detector:",
                    "",
                    "```",
                    *([f"  {d}" for d in found] or ["  (nothing flagged)"]),
                    "```",
                    ""]
        fps = safety_json.get("false_positives") or {}
        if fps.get("available"):
            out += ["### What the detector costs",
                    "",
                    f"Run over the **{fps['traces']} legitimate traces** from "
                    f"Part 1 — ordinary questions with no attack in them — it "
                    f"fired on **{fps['flagged']}**, a false-positive rate of "
                    f"**{fps['rate']:.3f}**.",
                    "",
                    "That number is the price of the detection above. A "
                    "detector that flagged every turn would catch every attack "
                    "and be worth nothing, so the two have to be read together.",
                    ""]
        out += ["---", ""]

    out += ["Generated by `scripts/demo_agent.py`.", ""]
    return "\n".join(out)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--stdout", action="store_true")
    args = parser.parse_args()

    def load_json(name):
        path = os.path.join(RESULTS, name)
        if not os.path.exists(path):
            return {}
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)

    agent_traces = store.load(AGENT_TRACES) if os.path.exists(AGENT_TRACES) else []
    safety_traces = (store.load(SAFETY_TRACES)
                     if os.path.exists(SAFETY_TRACES) else [])
    if not agent_traces:
        print(f"no traces at {AGENT_TRACES}; run the agent eval first",
              file=sys.stderr)
        return 1

    text = render(agent_traces, safety_traces,
                  load_json("agent.json"), load_json("safety.json"))

    if args.stdout:
        print(text)
        return 0

    os.makedirs(TRACE_DIR, exist_ok=True)
    path = os.path.join(TRACE_DIR, "10-agent-and-safety.md")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    print(f"wrote {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
