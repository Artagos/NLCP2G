# Trace 07 — the queue, and the interactive trigger

## The queue: a message arriving mid-turn

A turn that reaches the sandbox takes 20-60 seconds. Strategy chosen:
**per-learner FIFO**, cap 2 waiting. Statuses returned by `submit()`, in order:

```
started  (ahead: 0)  'my first idea'
queued   (ahead: 1)  'wait, what is a hash map?'
queued   (ahead: 2)  'and another thing'
dropped  (ahead: 2)  'one more'
dropped  (ahead: 2)  'and one more'
```

Execution order — nothing accepted was reordered or lost:

```
  start my first idea
  done my first idea
  start wait, what is a hash map?
  done wait, what is a hash map?
  start and another thing
  done and another thing
```

The two refusals are **loud**: the learner is told the queue is full and
that nothing already sent was lost. What FIFO costs is head-of-line
blocking — the hash-map question waited behind a sandbox run. We pay that
deliberately: the queued message is a paragraph the learner wrote
describing an algorithm, and discarding or interrupting it would throw
away their work and leave the attempt counter disagreeing with what ran.

## The interactive trigger: a signed run-complete callback

The sandbox run happens in a separate worker process, so the verdict
arrives as an inbound HTTP callback, not as a reply to anything the
learner sent. Four POSTs to the real endpoint:

| request | status | result |
|---|---|---|
| no signature | 401 | `bad_signature` |
| wrong signature | 401 | `bad_signature` |
| valid signature | 200 | `delivered` |
| same payload again | 200 | `already_finished` |

The replay matters: workers retry, and a learner must not receive two
verdicts for one run, nor have the attempt counted twice.

### What landed in the chat, unprompted

```
[attempt 1 · TLE]

Your program passed 5 of 6 tests. On the big test it ran past the time limit: at least 5000 ms against a 5000 ms limit.
```

Messages delivered for 5 inbound and 4 callbacks: **1** — one verdict, once.
