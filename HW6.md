# HW6 — Tracing the agent, and finding out it doesn't do what it's told

Evidence: [`traces/10`](traces/10-agent-and-safety.md) ·
numbers: [`eval/results/agent.md`](eval/results/agent.md),
[`eval/results/safety.md`](eval/results/safety.md) ·
design: [README § Tracing](README.md#tracing),
[§ Agent evaluation](README.md#agent-evaluation), [§ Safety](README.md#safety).

299 tests, up from 237. Two of the new files exist only to test the scorers and
the defence layers, because the numbers below are worth exactly as much as the
code that produced them.

## What I built

**Tracing that answers the objection instead of ignoring it.** `backend/llm.py`
has said since HW2 that tracing is deliberately off, because LangSmith would
send learner messages and generated C++ off the machine. That is a good reason
and it has not changed, so `backend/tracing.py` is built to satisfy it: MLflow
writing spans to a SQLite file on this machine, off unless `CP_TUTOR_TRACING=1`,
and absent from `backend/requirements.txt` entirely so the container that serves
learners does not ship a measuring tool. Every entry point no-ops when the
import fails, which is why the suite passes with MLflow uninstalled.

**A trajectory adapter that really is twenty lines** — because the tracing came
first. TOOL-typed spans, sorted by start time, name and arguments. The
assignment says building this before tracing means writing it twice, and that is
exactly right: the reason `trajectory.py` is short is that `tracing.py` already
made the spans exist.

**13 end-to-end scenarios, run 3 times each**, driven through the real FastAPI
app over `TestClient` — the same mechanism `scripts/demo_traces.py` already
used, so a scenario exercises routing, the tutor graph, tool selection and the
reply together rather than a graph invoked in isolation.

**Four defence layers and a detector that is a pure function of a trace**, so
the identical code runs over the committed attack traces, over the committed
*legitimate* traces to price its false positives, and against live traces.

## Ideas from the session

**Trace first, then everything else is an adapter.** Tracing was Part 2 and
agent eval was Part 1, and doing them in the printed order would have been a
mistake. Both the trajectory metrics and the whole safety detector turned out to
be pure functions of a `TraceView`.

**`request_origin` and `eval_case_id` are not bookkeeping.** I nearly treated
the tags as a formality. Then the first end-to-end capture found **two traces
for one `/chat`** — the reflector runs after the turn and autolog gives it a
root of its own — and "take the most recent trace" would have scored the
reflector instead of the tutor. The join key is what makes the harness correct,
not what makes it tidy.

**Undefined is not zero, again.** Carried over from HW5 and it earned its keep
immediately: a refusal scenario expects no tool call, so trajectory recall has
no denominator. Scoring those zero would have made the R1 guardrail look like a
failure every single time it worked.

## What the harness caught that I would have got wrong

**The tutor ignores its own instruction to search, about half the time.** The
prompt says "Call `search_corpus` BEFORE explaining any concept." Tool selection
came out at **0.538**. On concept questions the model frequently answered from
its own memory and never touched the corpus — the corpus HW5 built, chunked,
embedded, fused, reranked and measured to three decimal places. Goal completion
on those same runs stays high, because an unsourced explanation of `lower_bound`
is still a correct explanation. **An eval that scored only the reply would have
called every one of them a pass.** This is the single strongest argument I have
for why trajectory metrics exist, and I did not expect to find it here.

**pass@3 and pass^3 disagree by more than a factor of two.** 0.692 against
**0.308**. The assignment warns that at n=3 pass@3 collapses to "did it ever
pass"; on this data it is not a subtle effect. Five of thirteen scenarios were
flaky, and four of those flipped on *tool selection alone* — same question, same
prompt, and the model chose to search or not.

**The temperature trap did not bite, for an uncomfortable reason.** The
assignment warns that a course-pinned `temperature=0.0` would make three runs
identical. This project never pinned one — `llm.chat_model()` passes no
temperature — so the tutor has always run at the provider default of 1.0. I set
0.7 for the suite, which means **the eval is more stable than production**.
Every reliability number here is optimistic.

**A detector that looks perfect usually is not.** The first version flagged
0.256 of legitimate traffic. Every hit was `oversized`, because I had applied
the note-length cap to *tool results* — and five retrieved passages are
naturally over it. Ten ordinary concept questions flagged for the crime of
retrieving something. The false-positive pass is what caught it; without running
the detector over clean traces it would have shipped looking sensitive.

**Exfiltration is the weak category, and nothing was guarding it.** Attacks
resisted 5 of 9. Indirect injection through planted notes held 2/2 — R7's
fencing works, including against a note that opens with a literal
`</untrusted-note>`. But asked to list what it knows "including the internal
document id of each fact", the tutor printed `f_52ee4826`, and asked for the
problem statement verbatim it produced it. Neither is R1 solution leakage, which
is probably why neither had ever been considered.

**One and a half gigabytes of evidence.** The first export of 39 traces came to
1.5 GB. MLflow stores every payload twice — once in `span.inputs`, again as the
`mlflow.spanInputs` attribute — and a LangGraph CHAIN span's payload is the
whole graph state at that superstep, so the message history, the problem
statement and every retrieved passage get re-serialised on both sides of every
node. One `CHAIN LangGraph` span measured 15.6 MB. Projecting to what the
metrics actually read gives **269 kB**, 5,688× smaller, and every number
recomputes byte-identically from it.

**Two harness bugs that scored the agent for my mistakes.** Notes were filed
under `count-pairs` while the API derives the key from `problem.url`, which is
`bank:count-pairs` — so `read_problem_notes` correctly found nothing and the
scenario scored 0/3 for the agent behaving perfectly. And an attack that
produced no reply at all was counted as *complied*, crediting the attacker for
an outage. Both are now fixed; the second is why the safety table has three
outcomes rather than two.

## How I tested it

```bash
python -m pytest tests -q                     # 299, with mlflow uninstalled
python -m pytest tests -q                     # 299, with mlflow installed
python -m eval.agent.run_agent --offline      # every metric, no API key
python -m eval.safety.run_safety --offline    # attacks + false-positive rate
python scripts/draw_graph.py --check
```

The suite was run both with MLflow **absent** and with it installed and tracing
off; both give 299, which is the claim that instrumentation changed no
behaviour. HW5's two harnesses still reproduce byte-identically, which is the
same claim from the other direction.

The agent report was regenerated from the committed 269 kB traces with
`GEMINI_API_KEY` unset and diffed against the version produced by the live run:
identical apart from the line that says it was scored offline. That is the
reproducibility property this project has advertised for two increments, kept
intact across an experiment whose whole point is that it is not deterministic.

`tests/test_trajectory_metrics.py` checks the metrics against values computed by
hand, and pins the rule the assignment is emphatic about: the trajectory adapter
never reads a span's outputs. The trace in that test has its outputs
booby-trapped, so anything that reads one fails loudly.
`tests/test_safety_layers.py` covers each layer, and several of its assertions
are negative — that ordinary tutoring language does *not* trip the filter — because
those are the ones keeping the false-positive rate honest.
