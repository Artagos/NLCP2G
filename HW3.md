# HW3 — NLCP2G on a channel

Evidence: [`traces/06`](traces/06-triggers-and-silence.md),
[`07`](traces/07-queue-and-webhook.md), [`08`](traces/08-admin-boundary.md) ·
boundary: [`ADMIN_BOUNDARY.md`](ADMIN_BOUNDARY.md).

## What I built

**Channel + disposable identity** (`channels.py`): Telegram behind a `Channel`
interface, plus a `FakeChannel`. Long polling, so no public URL. The token is a
@BotFather bot in a gitignored `.env` — the agent posts as itself, never as a
person. Long replies are chunked, not truncated.

**Two triggers, neither a user message.** *Interactive*: a sandbox run takes
20–60s, which a chat can't hold open, so it goes to an out-of-process worker and
the verdict returns as a signed callback to `/hooks/run-complete`, where the
agent speaks unprompted. *Background*: a heartbeat (`scheduler.py`) reviewing
every linked learner.

**Silence branch.** `triggers.decide(state, now)` returns `Fire` or
`Silence(reason)`; the sweep records both. Two of the eight reasons come from the
product's promise, not politeness: `awaiting_learner` (the agent already asked a
question, so a nudge repeats it or supplies the missing step) and `would_hint`
(after two timeouts, an unprompted message can only read as *your approach is too
slow*, which rule R1 forbids). Nudge text is templated, never model-written —
it's the one message nobody asked for.

**Queue:** per-learner FIFO, 3 messages allowed to wait (2 in the demo, so the
overflow branch is short to show). Not *drop* — the queued message is a
paragraph describing an algorithm, the most expensive thing a learner produces.
Not *interrupt* — killing a run mid-flight leaves the attempt counter disagreeing
with what ran. **It costs head-of-line blocking**, paid deliberately, with an ack
saying your place in line; overflow is refused out loud.

**Admin subagent:** an allow-listed chat reaches a separate agent that can
rewrite the operating rules, force the auditor, mute the trigger, purge notes,
erase a learner's memory. It **cannot** read the text of a learner's private
memory — `learner_overview` counts documents and never touches `text`, keeping
rule R8 literally true. Authorisation is a chat-id check made before the agent
exists for that message, so nobody talks their way in. Every action is audited.

## Ideas from the session

Moving the agent somewhere it acts without being asked; triggers that are not
requests, and the discipline that a firing trigger doesn't oblige speech.
Concurrency as a decision with a named cost. Privilege separation whose boundary
is enforced by what a tool returns, not by what a prompt promises.

## How I tested it

**65 new tests, 153 total** (`pytest tests/ -q`, ~30s, no token, no API key). The
trigger and the channel are what "I sent it a message" can't test, so both were
made injectable: `decide()` takes `now` and reads no clock; every send goes
through a `FakeChannel` that records instead of transmitting.

- **Firing the trigger on purpose:** `python -m backend.scheduler --once --now
  2026-07-29T03:00` puts the system at 3am; `--dry-run` decides without sending;
  `/tick` forces a pass and prints every decision.
- **Knowing silence works:** all eight reasons are table-driven cases asserting
  the reason *and* its detail. The integration test asserts both halves — the
  channel sent nothing **and** a row exists saying why. Without the record,
  silence and an outage are the same observation. A delivery *failure* records
  `delivery_error`, so an outage can't masquerade as judgement.
- **Queue:** the slow turn is a future the test releases, not a real turn hoped
  to be slow. Asserts order, the ack, loud overflow, cross-learner isolation, and
  that one exploding turn doesn't stop the next.
- **Webhook:** POSTed unsigned, wrongly-signed, tampered, valid, replayed.
  Unsigned is 401 with nothing reaching the chat; the replay returns
  `already_finished`, so a retrying worker can't deliver two verdicts.
- **Boundary:** a learner's private fact is stored, the admin tool runs, and the
  test asserts that text is absent from the output. A non-allow-listed chat
  sending `/admin …` reaches the tutor instead.
- **Idempotency:** the same Telegram `update_id` twice yields one reply.

One test initially passed for the wrong reason — my isolation case had both
learners awaiting the same gate — an argument for controllable fakes over real
timing.

**Not yet live:** the above runs against the fake channel; the token and one real
exchange are pending. `python -m backend.bot` logs which identity it acts as
via `getMe`.
