# HW2 — memory, a second agent, and a monitor

**Project:** NLCP2G — non-programmers solve competitive programming problems by
describing their algorithm in plain words. The system builds it, runs it, and
reports back, without ever telling them how to solve it.
Full detail in [`IMPLEMENTATION.md`](IMPLEMENTATION.md); evidence in [`traces/`](traces/).

> Draft — adjust "what I did" to your slice of the group split before submitting.

## What is in this commit

**Three stores, each fitting what it holds.** SQLite (`memory.py`) keeps the
domain model as rows we query: `problems` with rating and tags
(`find_problems(min_rating, tag)`), one `attempts` row per solution with its
verdict, `notes`, the run log, the agents' scratchpad. A JSON document store
(`docstore.py`) holds memory the agent writes *in its own words*, where no schema
fits — one file per user, plus a shared one. `rules/operating_rules.md` is
markdown an admin edits by hand; it is re-read on mtime change, so a fix is live
on the next message.

Context is filled **both ways**. *Push*: the rules file and the learner's rule
documents, on every user-facing run. *Pull*: facts and shared notes fetched
mid-run through real tools (`retrieve_memory`, `read_problem_notes`);
`llm.generate_with_tools` drives the function-calling loop manually so every call
is recorded rather than hidden in the SDK.

**Facts and rules.** After each turn, `reflect.py` decides whether anything was
worth keeping. A *fact* is saved with a cue — the keywords a future request would
contain — and returns when the cue matches; the model then decides what to do
with it. A *rule* is attached always; the model decides only whether it applies.
Most turns save nothing, which is the harder half.

**Private and shared.** Identity is a `uid` cookie (no password: it scopes
memory, it doesn't protect anything). Private documents live in a per-user file,
so B's agent cannot read A's facts — not because a filter says so, but because
they are not on its read path. Notes are shared, fenced in
`<untrusted-note author=…>` with any closing tag in the body neutralised.

**A critic** (`critic.py`) reviews the generated C++ against the learner's words
on a written rubric (C1 invented logic, C2 dropped step, C3 substituted method,
C4 silent repair), blind exactly as the executor is. They exchange
`Handoff(status, result, needs_approval)` and branch on the fields: approved
runs, revise rebuilds once, escalate asks the learner. Two rounds, then escalate
rather than loop. It has a written delegation brief in `prompts.py` and no tools.

**A monitor** (`python -m backend.monitor [--watch N]`) grades the run log out of
band, on three axes with named values and no numeric score: `prompt_adherence`
(the line being whether the learner's outcome changed), `hint_leakage`,
`task_completion`. Every verdict carries a rationale quoting the evidence; one
that arrives without a rationale is stored marked untrustworthy rather than
reported as fact.

## Ideas from class

Session 4's scoped memory, promoted from one key-value store to three chosen by
what they hold, with the save decision delegated to the model and retrieval
driven by cues. Today's multi-agent piece as a structured handoff rather than
agents reading each other's prose. LLM-as-a-judge as a *background* monitor —
deliberately, because a judge inside the loop is a judge the loop can be tuned to
satisfy.

## Why the architecture is shaped this way

The four standard reasons for going multi-agent don't quite cover ours, so here
is a fifth: **an agent whose value comes from what it is denied.**

Our promise is that we build exactly what the learner described. The threat to it
is not a bug — it is competence. A code model told "look at every pair" will
often write the sorted two-pointer version, because that is what good code looks
like in its training data. The learner is then told their O(n²) idea passed, when
what passed was the model's O(n log n) idea. They learn the opposite of the
truth, and nothing in the output looks wrong.

One agent cannot catch this: asking the generator to check its own fidelity means
asking the thing that improved the code to notice that it improved the code, and
it has every reason to rationalise, because its version *is* better. So the check
is a separate agent given **less** information, not more — it never learns what
the problem is. It cannot approve code on the grounds that the code is correct,
because it has no idea what correct would mean. The only question available to it
is whether the program matches the words. Trace 04 shows it refusing the improved
version with C1 and C3 at confidence 1.0. The cost of one agent would have been
the product's only real claim.

**Coordination.** The two agents share a table (`scratchpad`). Shared memory is
the hardest kind to debug because there is no message to inspect: state changes,
and by the time you see a bad outcome, the write that caused it is
indistinguishable from the write that didn't. We kept it traceable by making it
append-only, keyed by `run_id`, and attributed — agent, round, status, timestamp.
Nothing is updated in place, so a handoff is replayed rather than reconstructed,
and the monitor reads the same table.

## How it was tested

**55 automated tests** (`python -m pytest tests/ -q`, ~20s, no API key — model
calls are stubbed). They target what breaks silently:

- *Privacy at the store, not the prompt* — A's fact is absent from B's `all_docs`
  and `retrieve`; a `../../etc/passwd` user id cannot escape the store directory.
- *Push and pull really differ* — rules appear in the pushed block, facts don't;
  a fact arrives only when its cue matches.
- *Notes cannot break their fence* — a note containing `</untrusted-note>` still
  renders as one block.
- *Every branch of the handoff* — approve runs; revise rebuilds once and the code
  that runs is the rebuilt one; escalate reaches the sandbox zero times; two
  failed reviews escalate instead of looping; `needs_approval` is derived from
  `status`, so a model that sets the flag inconsistently cannot stall the loop.
- *The monitor* — grades the backlog without re-grading, flags a rationale-less
  verdict, and survives a judge that throws.

**Two bugs were found by the tests, not by us.** The identity middleware appended
a second `Set-Cookie` on the very request that switched user, handing the session
back to `guest`. And a violation caught in round 1 and fixed in round 2 was
dropped from the record — so the monitor would have seen a clean run where the
critic had actually caught something.

**Five live traces** (`python -m scripts.demo_traces`) against the real model,
for the claims unit tests cannot make: that the agent *decides* well about what
to remember, and that it treats a hostile note as data. In trace 02 the reply
names Mallory's note as an injection attempt, cites R1/R7/R8, refuses, and still
relays Alice's legitimate note.

**What the monitor found on its first pass** (trace 05): a real contradiction
between R7 and R9 — one says quote a note's contents, the other says never relay
solution content, and nothing said which wins. No single run reveals that,
because each run touches only one of the two rules. It also graded a good run as
a serious violation by misreading R3, which was itself the evidence that R3's
scope was unclear. Both rules were fixed; the trace is kept as a dated snapshot.

**Known limits.** Verdicts come from sample tests only, so TLE is weak on real
problems. Identity is unauthenticated. The judge is a language model grading a
language model — trace 05 contains one verdict I think is wrong, left in on
purpose. Codeforces scraping is currently blocked by a Cloudflare challenge, so
problems fall back to the offline one.
