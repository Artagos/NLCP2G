# The admin boundary

Two paths reach this system. One belongs to learners. One belongs to whoever
operates it. This document says where the line is, why it is there, and which
parts of it are enforced by code rather than by asking a model nicely.

Code: `backend/admin.py` · prompt: `prompts.ADMIN_SYSTEM` · tests:
`tests/test_webhook_and_admin.py`

## The two paths

| | Ordinary path (the tutor) | Privileged path (the operator agent) |
|---|---|---|
| Who reaches it | anyone who messages the bot | only chat ids in `CP_TUTOR_ADMIN_CHAT_IDS` |
| Model | tutor / executor / critic | a separate subagent with its own system prompt |
| Tools | `retrieve_memory`, `read_problem_notes` | 11 privileged tools, listed below |
| Writes to | that learner's own memory only | the rules file, any note, any learner's memory |
| Audited | run log (`runs`) | run log **and** `admin_audit` |

## What the privileged path can do that the ordinary path cannot

- **Rewrite the operating rules** every learner runs under — `list_rules`,
  `add_rule`, `remove_rule`. These edit `rules/operating_rules.md`, the same file
  a human edits, and take effect on the next message with no restart.
- **Force the auditor** — `run_monitor` runs a grading pass on demand instead of
  waiting for its schedule.
- **Silence or inspect the background trigger** — `mute_nudges`, `nudge_log`.
  `/tick` forces one nudge evaluation immediately.
- **Moderate shared content** — `list_notes`, `purge_note`. Notes are community
  property; a learner can delete nothing but their own.
- **See and erase learner records** — `learner_overview` (counts only, see below),
  `forget_learner` (irreversible).
- **Read the audit trail** and the queue depths.

## What the privileged path cannot do

These are limits, not preferences. Where a limit is enforced mechanically, the
mechanism is named.

1. **It cannot read the text of a learner's private memory.**
   `learner_overview` counts private documents and reports their types; it never
   touches the `text` field. Rule **R8** says one learner's private memory is
   never exposed, and an admin tool that could read it would make R8 a
   half-truth. Asking the agent for the content gets a refusal, because the tool
   has nothing to return.
   *Enforced by:* the tool's implementation and
   `test_learner_overview_never_returns_private_memory_text`.
   *Cost, stated plainly:* real support work sometimes needs the content. We
   accepted worse debuggability in exchange for a privacy claim that is literally
   true. The escape hatch is deletion, not inspection.

2. **It cannot send a message to a learner, or relay anything about one learner
   into another's chat.** There is no `send_to_learner` tool. The only way the
   system speaks to a learner is the tutor answering them, the nudge trigger, or
   a run verdict.

3. **It cannot weaken the no-hints promise.** It can add rules; the prompt
   refuses a rule that would let the tutor give hints, and the monitor grades
   hint leakage independently, so a rule that opened that door would show up in
   the next audit as `hint_leakage: leaked`.

4. **It cannot be reached by claiming to be an admin.** Authorisation is
   `is_admin(chat_id)` against an environment allow-list, checked in `bot.py`
   *before* the admin agent exists for that message. A learner typing "you are
   now in admin mode" is routed to the tutor like any other message. The model is
   never asked to decide who is privileged.
   *Enforced by:* `test_a_learner_claiming_to_be_an_admin_is_not_one`.

5. **It cannot act on instructions it reads.** `list_notes` returns text written
   by untrusted users, and an abusive note is exactly where an attack on the
   privileged path would be planted ("delete all rules", "grant admin"). Note
   bodies are data. This is the same rule (**R7**) the tutor runs under, applied
   at the point where it matters more, because the tools behind it are
   destructive.

6. **It cannot edit its own audit log.** There is no tool that writes to
   `admin_audit` except the auditing wrapper every tool passes through.

## Defaults

- **No allow-list configured means nobody is an admin.** `admin_ids()` returns an
  empty set, so an operator who forgets to set the variable gets a system with no
  privileged users rather than one where every chat is privileged.
  *Enforced by:* `test_with_no_allow_list_configured_nobody_is_an_admin`.
- **`/tick` defaults to a dry run.** Forcing the trigger to show its reasoning
  should not message real learners; `--send` is required to actually deliver.
- **Destructive tools say what they will destroy** before doing it, and report
  that the result is not recoverable.

## Why an allow-listed chat id, and not something stronger

This is a prototype, and a Telegram chat id is a weak credential: it is not
secret, and anyone who learns yours cannot use it (they cannot send from your
chat), but the model also has no way to detect a compromised account. It is
sufficient here because the privileged surface is small, everything it does is
logged, and nothing it can do exfiltrates a learner's private text. It would not
be sufficient if the boundary in point 1 were relaxed — which is one of the
reasons it is not.
