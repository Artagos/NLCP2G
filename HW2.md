# HW2 — NLCP2G

Non-programmers solve competitive programming problems by describing their
algorithm in plain words. The system builds exactly what they described, runs it,
and reports back — without ever telling them how to solve it.

`github.com/Artagos/NLCP2G` · detail in [`IMPLEMENTATION.md`](IMPLEMENTATION.md)
· evidence in [`traces/`](traces/). *Group note: adjust "what I did" to your slice.*

## What I did in this commit

**Three stores, each fitting what it holds.** SQLite (`memory.py`) keeps the
domain model as rows we query — problems by rating and tag, attempts with their
verdicts, notes, the run log, the agents' scratchpad. A JSON document store
(`docstore.py`) holds memory the agent writes *in its own words*, where no fixed
schema fits. `rules/operating_rules.md` is markdown an admin edits by hand,
re-read on change so a fix is live next message.

**Context filled both ways.** *Push*: the rules and the learner's rule documents,
every run, unasked. *Pull*: facts and shared notes fetched mid-run through real
tools. The function-calling loop is driven manually, so every call is recorded —
a monitor that can't see which memories were fetched can't judge whether they
were used.

**Facts and rules.** `reflect.py` decides, per turn, whether anything is worth
keeping. A *fact* is saved with a **cue** — the words a future request would
plausibly contain — and returns when the cue matches; the model then decides what
to do with it. A *rule* is attached always, and the model decides only whether it
applies. Most turns save nothing, which is the harder half: a system that saves
everything drowns its own retrieval.

**Private and shared.** Identity is a `uid` cookie — no password, because the name
*scopes* memory rather than protecting it. Private documents live in a per-user
file, so B's agent cannot read A's facts: not because a filter says no, but
because they are not on its read path. Notes are shared, and arrive fenced in
`<untrusted-note author=…>` with any closing tag in the body neutralised.

**A second agent and a monitor.** `critic.py` reviews the generated C++ against
the learner's exact words on a written rubric, blind to the problem exactly as
the executor is; they exchange `Handoff(status, result, needs_approval)` and
branch on the fields — approved runs, revise rebuilds once, escalate asks the
learner, two rounds maximum. Separately, `python -m backend.monitor` grades the
run log after the fact, out of band, on three axes with named values and no
numeric score: prompt adherence (the line being whether the *learner's outcome*
changed), hint leakage, task completion. Every verdict must quote its evidence;
one without a rationale is stored marked untrustworthy, not reported as fact.

## Which ideas from class this puts into practice

**Memory.** The obvious version is one store and one save/retrieve pair. The point
that stuck is that memory is several different things wearing one name, so this
splits them by *what they are*: structured records that get queried, free-form
notes the agent writes for itself, and static rules a human maintains. Push/pull
falls out of that — rules are always in force, so waiting to be asked for them is
a bug, while a learner accumulates far more facts than fit in any one context, so
those are fetched and the cue decides relevance. Scoping shows up as
private-vs-shared, enforced at the store rather than by asking a prompt nicely.

**Multi-agent systems.** Agents hand each other *structured objects* and branch on
fields rather than reading each other's prose. And LLM-as-a-judge is wired
deliberately as a **background** monitor, because a judge inside the request loop
is a judge the loop can be tuned to satisfy.

## Why our agent architecture is shaped this way

The standard reasons for going multi-agent didn't quite fit, so here is a fifth:
**an agent whose value comes from what it is denied.**

Our promise is that we build exactly what the learner described. The threat isn't
a bug — it's competence. A code model told "look at every pair" will often write
the sorted two-pointer version, because that's what good code looks like in its
training data. The learner is then told their O(n²) idea passed, when what passed
was the model's O(n log n) idea. They learn the opposite of the truth, and nothing
in the output looks wrong.

One agent cannot catch this: asking the generator to check its own fidelity is
asking the thing that improved the code to notice that it improved the code, and
it has every reason to rationalise, because its version genuinely *is* better. So
the check is a separate agent, given **less** information rather than more — it
never learns what the problem is. That is what makes it incorruptible here: it
cannot approve code on the grounds that the code is correct, because it has no
idea what correct would mean. The only question available to it is whether the
program matches the words. **What one agent would have cost is the product's only
real claim.**

**Coordination.** The two agents share a table. Shared memory is the hardest kind
to debug because there's no message to inspect: state changes, and by the time you
see a bad outcome the write that caused it is indistinguishable from the one that
didn't. We kept it append-only, keyed by run id, and attributed, so a handoff is
*replayed* rather than reconstructed — and the monitor reads the same table.

## How I tested it

**88 automated tests** (`pytest tests/ -q`, ~35s, no API key — model calls are
stubbed), aimed at what breaks silently:

- *Privacy at the store, not the prompt* — A's fact is absent from B's `all_docs`
  and `retrieve`; a `../../etc/passwd` user id can't escape the store directory.
- *Push and pull really differ* — rules appear in the pushed block, facts don't; a
  fact arrives only when its cue matches.
- *Notes can't break their fence* — a note containing `</untrusted-note>` still
  renders as one block.
- *Every branch of the handoff* — approve runs; revise rebuilds once and the
  rebuilt code is what runs; escalate reaches the sandbox zero times; two failed
  reviews escalate instead of looping; `needs_approval` is derived from `status`,
  so a model that sets the flag inconsistently can't stall it.
- *Every reference solution* is checked against an independent brute force. A
  wrong expected output is the worst bug available here — the learner describes a
  correct approach, gets WA, and is told their thinking is wrong when it wasn't.

**Four bugs were found by the tests, not by us.** The identity middleware appended
a second `Set-Cookie` on the very request that switched user, handing the session
back to `guest`. A violation caught in round 1 and fixed in round 2 was dropped
from the record, so the monitor would have seen a clean run where the critic had
caught something. Selection repeated problems once a learner exhausted their
rating band. And one problem's time limit sat *below* its own intended solution's
runtime — a correct answer would have been failed.

**Five live traces** (`python -m scripts.demo_traces`) against the real model, for
the claims unit tests can't make. In trace 02 the reply names a planted note as an
injection attempt, cites the rules it would have broken, refuses — and still
relays the other learner's legitimate note.

**What the monitor found on its first pass** (trace 05): a real contradiction
between two of our rules — one says quote a note's contents, the other says never
relay solution content, and nothing said which wins. No single run can reveal
that, because each run touches only one of the two. It also graded a good run as a
serious violation by misreading another rule, which was itself the evidence that
that rule was unclear. Both fixed; the trace kept as a dated snapshot so the
finding survives the fix.
