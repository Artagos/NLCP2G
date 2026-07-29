# Trace 06 — the background trigger, and the silence branch

A heartbeat every 15 minutes evaluates each linked learner. Below is one
sweep per scenario, each a real `scheduler.sweep()` call against a real
`FakeChannel`. The only thing that changes between them is the learner's
state and the clock — and the clock is a **parameter**, which is the whole
reason a 03:00 case can be tested at 14:00.

| scenario | outcome | recorded reason | sent? |
|---|---|---|---|
| a learner who drifted off mid-problem | **fired** (stale_unsolved) | `stale_unsolved` | yes |
| ...but they were active four minutes ago | silent | `recently_active` | **nothing** |
| ...the same learner, at 03:00 their time | silent | `quiet_hours` | **nothing** |
| ...after two timeouts in a row | silent | `would_hint` | **nothing** |
| ...after the agent asked them a question | silent | `awaiting_learner` | **nothing** |
| ...already nudged twice about this problem | silent | `already_nudged_problem` | **nothing** |
| ...they solved it | silent | `nothing_pending` | **nothing** |

## The message it actually sends

Templated, never model-written. A nudge is the one message the learner
did not ask for, so the safest guarantee that it carries no hint is that
no model composes it. It may name the problem and count attempts; it must
never mention a verdict.

```
You left Count Pairs With Sum K unfinished after 1 attempt. Still fancy it? Describe your next idea and I'll run it — or say "another problem" for something different, or "summarize" for a recap of what you tried.
```

## The written record

This is what makes silence an outcome rather than an absence. A bot that
decided not to speak and a bot that fell over look identical from the
outside; these rows are the difference.

```
silent   recently_active        active 4 min ago; they don't need chasing
silent   quiet_hours            local time is 03:xx
silent   would_hint             2 timeouts in a row — an unprompted message here reads as 'your approach is too slow', which R1 forbids
silent   awaiting_learner       the agent already asked a question after a NEEDS_CLARIFICATION verdict; nudging would either repeat it or supply the missing step
silent   already_nudged_problem already nudged 2x about Count Pairs With Sum K
silent   nothing_pending        Count Pairs With Sum K is already solved
```

Two of those reasons come from the product's promise rather than from
politeness. `awaiting_learner`: the agent already asked a question, so a
nudge would either repeat it or supply the missing step. `would_hint`:
after two timeouts, an unprompted message can only read as *your approach
is too slow* — which rule R1 forbids. Silence is the correct answer.

## Firing it on purpose

```bash
python -m backend.scheduler --once                          # now
python -m backend.scheduler --once --now 2026-07-29T03:00   # quiet hours
python -m backend.scheduler --once --dry-run                # decide, send nothing
```

Plus `/tick` in the admin chat, which runs one pass and reports every
decision. Dry-run by default: forcing the trigger to show its reasoning
should not message real learners.
