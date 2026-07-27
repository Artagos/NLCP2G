# HW2 — memory, a second agent, and a monitor

**Project:** NLCP2G — non-programmers solve competitive programming problems by
describing their algorithm in plain words. The system builds it, runs it, and
reports back, without ever telling them how to solve it.

> Draft for submission — adjust the "what I did" section to your slice of the
> group split before handing it in.

## What is in this commit

**Three stores, each fitting what it holds.** The relational store (SQLite,
`backend/memory.py`) keeps the domain model as structured rows we actually query:
`problems` with rating and tags (`find_problems(min_rating, tag)`), one `attempts`
row per solution with its verdict, `notes` written by learners, plus the run log
and the agents' scratchpad. The non-relational store (`backend/docstore.py`) is a
JSON document store for memory the agent writes *in its own words*, where no fixed
schema fits — one file per user, plus a shared file. The operating rules
(`rules/operating_rules.md`) are markdown an admin edits by hand; the loader
re-reads on mtime change, so a fix is live on the next message with no restart.

Context is filled **both ways**. *Push*: the rules file and the learner's rule
documents go into every user-facing run, unasked. *Pull*: facts and shared notes
are fetched mid-run through real tools (`retrieve_memory`, `read_problem_notes`)
that the model calls itself — `backend/llm.py` drives the function-calling loop
manually so every call is recorded rather than hidden inside the SDK.

**Facts and rules.** After each turn `backend/reflect.py` decides whether anything
was worth keeping. A *fact* is saved with a cue — the keywords a future request
would contain — and comes back when the cue matches; when it surfaces the model
decides what to do with it. A *rule* is attached always; the model only decides
whether it applies. Most turns save nothing, which is the harder half.

**Private and shared.** Identity is a `uid` cookie (no password: it scopes memory,
it doesn't protect anything, and pretending otherwise would be worse). Private
documents live in a per-user file — B's agent cannot read A's facts because they
are not on its read path, not because a filter says so. Notes are shared: one
learner writes, every other learner's agent can read. They arrive fenced in
`<untrusted-note author=…>`, with any closing tag in the body neutralised so a
note cannot escape its own block.

**A critic that earns its place.** `backend/critic.py` reviews the generated C++
against the learner's words on a written rubric (C1 invented logic, C2 dropped
step, C3 substituted method, C4 silent repair), with the same blindness the
executor has. They exchange `Handoff(status, result, needs_approval)` and branch
on the fields: `approved` runs, `revise` rebuilds once, `escalate` stops and asks
the learner. Two rounds maximum, then it escalates rather than looping. The
critic has a written delegation brief in `prompts.py` — scope, when it acts
alone, when it escalates, effort budget — and no tools at all.

**A monitor on its own clock.** `python -m backend.monitor [--watch N]` grades the
run log after the fact, out of band. Three axes with named values, no 1-to-10
score: `prompt_adherence` (strictly_adheres / minor_violation / serious_violation,
the line being whether the learner's outcome changed), `hint_leakage`, and
`task_completion`. Every verdict carries a rationale quoting the evidence; a
verdict that arrives without one is stored marked untrustworthy rather than
reported as fact. A second pass reads the batch together with the rules file and
looks for contradictions.

## Ideas from class this puts into practice

Scoped memory from Session 4, promoted from one key-value store to three stores
chosen by what they hold. The `save_memory` / `retrieve_memory` pair, now with the
save decision delegated to the model and the retrieval driven by cues. Today's
multi-agent piece as a structured handoff rather than agents reading each other's
prose. LLM-as-a-judge from today's lecture, wired as a background monitor instead
of an inline check — deliberately, because a judge inside the loop is a judge the
loop can be tuned to satisfy.

## Why the architecture is shaped this way

The four standard reasons for going multi-agent don't quite cover ours, so here is
a fifth: **an agent whose value comes from what it is denied.**

The promise of this product is that we build exactly what the learner described.
The threat to that promise is not a bug — it is competence. A code model told
"look at every pair" will often write the sorted two-pointer version, because
that is what good code looks like in its training data. The learner is then told
their O(n²) idea passed, when what passed was the model's O(n log n) idea. They
learn the opposite of the truth, and nothing in the output looks wrong.

One agent cannot catch this. Asking the generator to check its own fidelity means
asking the thing that "improved" the code to notice that it improved the code —
and it has every reason to rationalise, because its version *is* better. So the
check is a separate agent, and crucially it is given **less** information, not
more: it never learns what the problem is. That is what makes it incorruptible on
this axis. It cannot approve code on the grounds that the code is correct, because
it has no idea what correct would mean. The only question available to it is
whether the program matches the words. Trace 04 shows it refusing the improved
version with C1 and C3 at confidence 1.0.

The cost of one agent would have been the product's only real claim.

**Coordination, and why shared memory is the hard kind.** The two agents
coordinate through a shared table (`scratchpad`). Shared memory is the hardest
coordination to debug because there is no message to inspect: state changes, and
by the time you see a bad outcome the write that caused it is indistinguishable
from the write that didn't. We kept it traceable by making it append-only, keyed
by `run_id`, and attributed — every entry records which agent wrote it, in which
round, with what status. Nothing is updated in place, so a handoff is replayed
rather than reconstructed, and the monitor reads the same table (trace 04).

## How it was tested

**55 automated tests** (`python -m pytest tests/ -q`, ~20s, no API key needed —
model calls are stubbed). They cover what breaks silently:

- *Privacy at the store, not the prompt* — A's fact is absent from B's `all_docs`
  and from B's `retrieve`; a `../../etc/passwd` user id cannot escape the store
  directory; reset clears one user only.
- *Push vs pull are actually different* — rules appear in the pushed block, facts
  do not; a fact arrives only when its cue matches and stays away when it doesn't.
- *Notes cannot break their fence* — a note containing `</untrusted-note>` still
  renders as exactly one block.
- *Control flow of the handoff* — approve runs the code; revise rebuilds once and
  the code that runs is the rebuilt one; escalate reaches the sandbox zero times;
  two failed reviews escalate instead of looping; `needs_approval` is derived from
  `status`, so a model that sets the flag inconsistently cannot stall the loop.
- *The monitor* — grades the backlog and doesn't re-grade, marks a rationale-less
  verdict untrustworthy, puts failing runs in the report, and survives a judge
  that throws.
- *Identity over HTTP* — including a regression test for a real bug this work
  introduced: the middleware appended a second `Set-Cookie` on the very request
  that switched user, handing the session straight back to `guest`.

Two bugs were found by the tests rather than by us. The cookie race above, and a
gap where a violation caught in round 1 and fixed in round 2 was dropped from the
record — so the monitor would have seen a clean run where the critic had actually
caught something.

**Five live traces** (`traces/`), generated by `python -m scripts.demo_traces`
against the real API and the real model. They are the evidence for the claims the
unit tests cannot make: that the agent *decides* well about what to remember, and
that it treats a hostile note as data. Trace 02 is the planted comment; the reply
names Mallory's note as an injection attempt, cites R1/R7/R8, and refuses, while
still relaying Alice's legitimate note.

**What the monitor found on its first real pass** (trace 05): a genuine
contradiction between R7 and R9 — one says quote a note's contents, the other says
never relay solution content, and nothing said which wins. No single run can
reveal that, because each run only touches one of the two rules. It also graded a
good run as a serious violation by misreading R3, which was itself the evidence
that R3's scope was unclear. Both rules were fixed; the trace is kept as a dated
snapshot so the finding survives the fix.

**Known limits.** Verdicts come from sample tests only, so TLE is a weak signal on
real Codeforces problems. Identity is unauthenticated. The judge is a language
model grading a language model: trace 05 contains one verdict I think is wrong,
left in on purpose.
