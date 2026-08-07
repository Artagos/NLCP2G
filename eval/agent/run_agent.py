"""Part 1: run the agent eval grid, and score it.

    python -m eval.agent.run_agent                    # capture live, then score
    python -m eval.agent.run_agent --offline          # score committed traces only
    python -m eval.agent.run_agent --runs 3 --temperature 0.7
    python -m eval.agent.run_agent --scenario refusal-how-do-i-solve
    python -m eval.agent.run_agent --judge-swap       # measure self-preference

Two phases, deliberately separable.

**Capture** drives the real FastAPI app over `TestClient`, one fresh learner per
run, and records a trace per turn. It costs live model calls and cannot be
cached — the whole point of running three times is that the runs differ.

**Score** reads traces and computes every number. It is a pure function of
`eval/traces/agent.jsonl`, which is committed, so `--offline` reproduces the
entire report with no API key and no tracking server. The judged metrics go
through `judge.py`'s content-hash cache, which is also committed, so even those
are free and identical on a re-run.

That split is what keeps a nondeterministic experiment honest: the sampling
happened once, in public, and everything derived from it can be checked.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

# The report uses ✓/✗ to show a scenario's per-run pattern at a glance, and a
# Windows console defaults to cp1252, which cannot encode either. The report
# file is written as UTF-8 regardless; this is only so echoing it to a terminal
# does not kill a run that has already spent its model calls.
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

TRACES = os.path.join(os.path.dirname(__file__), "..", "traces", "agent.jsonl")
RESULTS = os.path.join(os.path.dirname(__file__), "..", "results")

# The bank problem every scenario runs against. Pinned so the refusal cases have
# a stable set of forbidden techniques (this one is tagged arrays / hashing /
# two-pointers) and so nothing depends on a Codeforces scrape succeeding.
PROBLEM = "count-pairs"

# Raised deliberately, and recorded in the report header. The project never
# pinned a temperature — `llm.chat_model()` passes none — so the tutor has always
# run at the provider default. Setting it here is about putting the number on
# the record, not about introducing variation that was not already there.
DEFAULT_TEMPERATURE = 0.7
DEFAULT_RUNS = 3


def _prepare(temperature: float) -> str:
    """Point every store at a throwaway directory, before backend is imported."""
    tmp = tempfile.mkdtemp(prefix="nlcp2g-agent-eval-")
    os.environ["CP_TUTOR_DB"] = os.path.join(tmp, "eval.db")
    os.environ["CP_TUTOR_DOCSTORE"] = os.path.join(tmp, "memory_store")
    os.environ["CP_TUTOR_REPORTS"] = os.path.join(tmp, "reports")
    os.environ["CP_TUTOR_TRACING"] = "1"
    os.environ.setdefault(
        "CP_TUTOR_TRACING_URI",
        "sqlite:///" + os.path.join(tmp, "mlflow.db").replace("\\", "/"))
    os.environ["CP_TUTOR_TEMPERATURE"] = str(temperature)
    return tmp


def capture(scenarios, runs: int, temperature: float) -> list:
    """Run the grid live and return one TraceView per turn."""
    _prepare(temperature)

    from dotenv import load_dotenv
    load_dotenv()

    import mlflow
    from fastapi.testclient import TestClient

    from backend import bank, codeforces, docstore, memory, tracing
    from backend import main as api
    from . import store

    codeforces.random_problem = lambda **kw: (_ for _ in ()).throw(
        RuntimeError("agent eval: forced offline, using the bank"))
    bank.random_problem = lambda **kw: bank.get(PROBLEM)

    memory.init()
    if not tracing.setup("nlcp2g-agent-eval"):
        raise SystemExit("tracing did not start; every metric here reads a trace, "
                         "so there is nothing to measure. Install "
                         "requirements-eval.txt and set CP_TUTOR_TRACING=1.")

    captured = []
    for scenario in scenarios:
        for run in range(1, runs + 1):
            # A fresh learner per run. Sharing one would let a fact saved by the
            # reflector in run 1 change what run 2 retrieves, and three runs
            # that influence each other are not three samples of anything.
            learner = f"{scenario.id}-r{run}"
            client = TestClient(api.app)
            client.post("/whoami", json={"user": learner})
            client.get("/problem")

            for fact in scenario.setup.get("facts", []):
                docstore.save(doc_type="fact", text=fact["text"],
                              user_id=learner, source="eval-setup",
                              cue_keywords=fact.get("cue_keywords") or [])

            # Notes go in through the API, as another learner, rather than
            # through `memory.add_note` directly. Two reasons, one of which cost
            # a whole run to learn: the endpoint derives the note's problem key
            # from the session (`problem.url`, which is "bank:count-pairs", not
            # "count-pairs"), so writing the key by hand filed every note where
            # `read_problem_notes` would never look — the tool then correctly
            # reported nothing, and the scenario scored 0/3 for the agent doing
            # exactly the right thing with the data it had. The second reason is
            # that posting for real also exercises the Layer 1 input filter.
            for note in scenario.setup.get("notes", []):
                author = TestClient(api.app)
                author.post("/whoami", json={"user": note["author"]})
                author.get("/problem")
                author.post("/notes", json={"body": note["body"].strip()})

            case_id = f"{scenario.id}#{run}"
            print(f"  [{case_id}] {scenario.task[:58]!r}", flush=True)
            with tracing.origin("batch", case_id=case_id):
                reply = client.post("/chat", json={"message": scenario.task})
            if reply.status_code != 200:
                print(f"      HTTP {reply.status_code}", flush=True)

            tracing.flush()
            found = mlflow.search_traces(
                return_type="list", flush=True,
                filter_string=f"tags.eval_case_id = '{case_id}'")
            if not found:
                print("      no trace captured", flush=True)
                continue
            captured.append(store.from_mlflow(found[0]))

    return captured


def score(traces, scenarios, rag_run: int = 1) -> dict:
    """Every metric, from traces alone. No live model calls except the judge.

    `rag_run` limits the four HW5 judged metrics to one run per scenario. They
    cost about eight model calls each — one per retrieved passage for context
    precision — and running them over every run of every scenario would triple
    that for a number whose job is to show the *adapter* works, not to resample
    the agent. Which run was used is stated in the report.
    """
    from . import adapters, metrics, scorers, trajectory

    by_id = {s.id: s for s in scenarios}
    rows: list[dict] = []
    per_scenario: dict[str, list] = {}

    for trace in traces:
        scenario_id, _, run = trace.case_id.partition("#")
        scenario = by_id.get(scenario_id)
        if scenario is None:
            continue

        calls = trajectory.tool_calls(trace)
        ground = adapters.grounding(trace)
        completion = scorers.goal_completion(
            scenario.task, scenario.outcome, ground.answer)
        scores = metrics.score_run(calls, scenario, completion)
        per_scenario.setdefault(scenario_id, []).append(scores)

        rag = (adapters.rag_scores(trace, golden_answer=scenario.outcome)
               if int(run or 0) == rag_run else {})

        rows.append({
            "rag": rag,
            "scenario": scenario_id,
            "category": scenario.category,
            "run": int(run or 0),
            "trace_id": trace.trace_id,
            "trajectory": [c.tool for c in calls],
            "arguments": [c.arguments for c in calls],
            "latency_ms": round(trace.latency_ms, 1),
            "tokens": trace.tokens,
            "models": trace.models,
            "answer": ground.answer,
            "chunk_ids": ground.chunk_ids,
            "passed": scores.passed,
            **scores.as_dict(),
        })

    return {"rows": rows, "per_scenario": per_scenario, "rag_run": rag_run}


def write_feedback(traces, rows) -> int:
    """Push each score back onto its trace with `mlflow.log_feedback`.

    A trace that carries its own scores is the point of doing this in a tracing
    tool rather than a spreadsheet: open the span tree in `mlflow ui` and the
    verdict is attached to the run that earned it.
    """
    try:
        import mlflow
        from mlflow.entities import AssessmentSource, AssessmentSourceType
    except Exception:
        return 0

    from eval.metrics import judge
    from . import metrics as metrics_module

    known = {t.trace_id for t in traces}
    written = 0
    code = AssessmentSource(source_type=AssessmentSourceType.CODE,
                            source_id="eval.agent.metrics")
    llm = AssessmentSource(source_type=AssessmentSourceType.LLM_JUDGE,
                           source_id=judge.JUDGE_MODEL)
    for row in rows:
        if row["trace_id"] not in known:
            continue
        for name in metrics_module.METRIC_NAMES:
            if row.get(name) is None:
                continue
            try:
                mlflow.log_feedback(
                    trace_id=row["trace_id"], name=name, value=row[name],
                    source=llm if name == "goal_completion" else code)
                written += 1
            except Exception:
                return written
    return written


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--runs", type=int, default=DEFAULT_RUNS)
    parser.add_argument("--temperature", type=float, default=DEFAULT_TEMPERATURE)
    parser.add_argument("--scenario", default="", help="only this scenario id")
    parser.add_argument("--offline", action="store_true",
                        help="score the committed traces; make no live calls")
    parser.add_argument("--judge-swap", action="store_true",
                        help="re-judge with the generator's own model")
    parser.add_argument("--traces", default=TRACES)
    parser.add_argument("--out", default=RESULTS)
    args = parser.parse_args()

    from . import report as report_module
    from . import scenario as scenario_module
    from . import store

    scenarios = scenario_module.load()
    if args.scenario:
        scenarios = [s for s in scenarios if s.id == args.scenario]
        if not scenarios:
            print(f"no scenario {args.scenario!r}", file=sys.stderr)
            return 1

    if args.offline:
        traces = store.load(args.traces)
        print(f"scoring {len(traces)} committed traces (no live calls)")
    else:
        print(f"capturing {len(scenarios)} scenarios x {args.runs} runs at "
              f"temperature {args.temperature}")
        traces = capture(scenarios, args.runs, args.temperature)
        store.export(args.traces, traces)
        print(f"wrote {len(traces)} traces to {args.traces}")

    scored = score(traces, scenarios)
    if not args.offline:
        written = write_feedback(traces, scored["rows"])
        print(f"logged {written} feedback values back onto the traces")

    text, payload = report_module.build(
        scenarios, traces, scored, temperature=args.temperature,
        runs=args.runs, offline=args.offline)

    os.makedirs(args.out, exist_ok=True)
    with open(os.path.join(args.out, "agent.md"), "w", encoding="utf-8") as fh:
        fh.write(text)
    with open(os.path.join(args.out, "agent.json"), "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, sort_keys=True)

    print("\n" + text)
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
