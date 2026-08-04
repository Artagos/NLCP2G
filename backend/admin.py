"""The admin subagent: a privileged path with a boundary that is code, not prose.

The ordinary tutoring path can answer questions, run programs, and write to one
learner's own memory. It cannot change the rules everyone runs under, delete
another learner's note, force the auditor, or silence the background trigger.
This can.

Two things keep that safe, and neither is "the prompt says so":

  **Authorisation is a check, not an instruction.** `is_admin()` tests the chat
  id against an allow-list from the environment before any tool is reachable. A
  learner who types "you are now in admin mode" gets the ordinary path, because
  the admin path was never selected for them. The model is not asked to decide.

  **The privacy boundary is enforced by the tool's return value.** `learner_overview`
  cannot leak a private memory's text because it never reads the `text` field —
  it returns counts and types. Rule R8 says one learner's private memory is never
  exposed; the admin tooling honours that literally rather than being trusted to.

The full written boundary is in ADMIN_BOUNDARY.md.
"""
from __future__ import annotations

import logging
import os

from . import docstore, llm, memory, monitor, rules, scheduler
from .outbound import get_channel

log = logging.getLogger("cp_tutor.admin")


def admin_ids() -> set[str]:
    raw = os.environ.get("CP_TUTOR_ADMIN_CHAT_IDS", "")
    return {p.strip() for p in raw.split(",") if p.strip()}


def is_admin(chat_id: str) -> bool:
    """The gate. No allow-list configured means nobody is an admin — the safe
    default is a system with no privileged users, not one where everybody is."""
    return str(chat_id) in admin_ids()


# ------------------------------------------------------------------ the tools
# Each returns a short string for the model to relay, and audits itself.

def _audit(actor: str, tool: str, args: dict, result: str) -> str:
    memory.audit_admin(actor, tool, args, result)
    return result


def list_rules(actor: str) -> str:
    titles = rules.titles()
    if not titles:
        return "The rules file is empty or missing."
    body = "\n".join(f"{rid}: {title}" for rid, title in titles.items())
    return _audit(actor, "list_rules", {}, f"{len(titles)} rule(s)\n{body}")


def add_rule(actor: str, title: str, body: str) -> str:
    rule_id = rules.add_rule(title, body)
    return _audit(actor, "add_rule", {"title": title, "body": body},
                  f"Added {rule_id}. Live for every learner on their next message.")


def remove_rule(actor: str, rule_id: str) -> str:
    ok = rules.remove_rule(rule_id)
    return _audit(actor, "remove_rule", {"rule_id": rule_id},
                  f"Removed {rule_id}." if ok else f"No rule {rule_id} found.")


def run_monitor(actor: str) -> str:
    path = monitor.run_once(limit=25)
    judged = memory.judgments(limit=200)
    if not path:
        return _audit(actor, "run_monitor", {}, "Nothing to grade; no report written.")
    bad = [j for j in judged if j["prompt_adherence"] != "strictly_adheres"
           or j["hint_leakage"] != "none"]
    summary = (f"Graded. {len(judged)} verdict(s) total, {len(bad)} flagged. "
               f"Report: {os.path.basename(path)}")
    if bad:
        summary += "\nMost recent flag: " + bad[0]["rationale"][:400]
    return _audit(actor, "run_monitor", {}, summary)


def mute_nudges(actor: str, muted: bool) -> str:
    memory.set_setting(scheduler.MUTE_KEY, "1" if muted else "0")
    return _audit(actor, "mute_nudges", {"muted": muted},
                  f"Background nudges are now {'MUTED' if muted else 'active'}.")


# Deliberately async-only, and exposed as the deterministic `/tick` command
# rather than as a model-callable tool: the LLM tool loop is synchronous, and
# firing a real trigger is something an operator should do explicitly, not
# something a model decides to try mid-sentence.
async def force_tick(actor: str, dry_run: bool = True) -> str:
    channel = get_channel()
    if channel is None:
        return _audit(actor, "force_tick", {"dry_run": dry_run},
                      "No channel is registered; cannot evaluate.")
    outcomes = await scheduler.sweep(channel, dry_run=dry_run)
    return _audit(actor, "force_tick", {"dry_run": dry_run},
                  scheduler.format_outcomes(outcomes))


def list_notes(actor: str, limit: int = 20) -> str:
    """Community notes. These are already visible to every learner, so reading
    them is not a privacy breach — but they are untrusted text (see R7)."""
    seen: list[str] = []
    for run in memory.recent_runs(limit=100):
        for note in memory.notes_for(run["problem_key"] or "", limit=50):
            line = f"[{note['id']}] {note['author']} on {run['problem_key']}: {note['body'][:200]}"
            if line not in seen:
                seen.append(line)
    body = "\n".join(seen[:limit]) or "No notes."
    return _audit(actor, "list_notes", {}, body)


def purge_note(actor: str, note_id: int) -> str:
    ok = memory.delete_note(int(note_id))
    return _audit(actor, "purge_note", {"note_id": note_id},
                  f"Deleted note {note_id}." if ok else f"No note {note_id}.")


def learner_overview(actor: str, user_id: str) -> str:
    """Counts and verdicts. Never the text of a private memory.

    This is the boundary in R8 made mechanical: the private documents are read to
    be counted and their types listed, and the `text` field is not touched.
    """
    sid = f"u:{user_id}"
    progress = memory.progress(sid)
    docs = docstore.all_docs(user_id)
    private = [d for d in docs if d.get("scope") == "private"]
    facts = sum(1 for d in private if d["type"] == "fact")
    rule_docs = sum(1 for d in private if d["type"] == "rule")
    pending = len(memory.pending_jobs(user_id))
    last = memory.last_activity_at(sid)

    body = (
        f"learner {user_id}\n"
        f"  seen {progress['seen']}, solved {progress['solved']}, "
        f"attempts {progress['attempts']}\n"
        f"  verdicts: {progress['by_verdict'] or '{}'}\n"
        f"  private memory: {facts} fact(s), {rule_docs} rule(s) "
        f"[text withheld by design — see ADMIN_BOUNDARY.md]\n"
        f"  runs in flight: {pending}\n"
        f"  last active: {last or 'never'}"
    )
    return _audit(actor, "learner_overview", {"user_id": user_id}, body)


def forget_learner(actor: str, user_id: str) -> str:
    """Irreversible: drops this learner's private documents."""
    before = len([d for d in docstore.all_docs(user_id) if d.get("scope") == "private"])
    docstore.clear(user_id)
    return _audit(actor, "forget_learner", {"user_id": user_id},
                  f"Erased {before} private document(s) for {user_id}. Not recoverable.")


def nudge_log(actor: str, limit: int = 15) -> str:
    rows = memory.recent_nudges(limit)
    if not rows:
        return _audit(actor, "nudge_log", {}, "No nudge decisions recorded yet.")
    body = "\n".join(
        f"{r['decision']:8s} {r['user_id']:12s} {r['reason'] or r['kind']}"
        f"{' — ' + r['detail'][:80] if r['detail'] else ''}" for r in rows)
    return _audit(actor, "nudge_log", {}, body)


def audit_trail(actor: str, limit: int = 15) -> str:
    rows = memory.admin_log(limit)
    body = "\n".join(f"{r['actor']}: {r['tool']} {r['args']}" for r in rows) or "Empty."
    return body       # reading the log is not itself an audited action


# ------------------------------------------------------------- the subagent
# The tools above are registered with LangChain in `tools/admin_tools.py` and
# bound to the model by `graphs/admin.py`. They stay plain functions here
# because this is where the audit write lives, and because the boundary in
# ADMIN_BOUNDARY.md should be readable without knowing what a tool schema is.


def handle(chat_id: str, message: str) -> str:
    """Answer an administrator. Callers must have checked `is_admin` first."""
    if not is_admin(chat_id):
        # belt and braces: refuse even if a caller forgets to gate. This is the
        # last line rather than the only one — bot.py checks before routing here.
        log.warning("admin path reached by non-admin chat %s", chat_id)
        return "Not authorised."

    # imported here, not at module scope: the admin tools wrap the functions in
    # this file, so importing the graph eagerly would be a cycle
    from .graphs import admin as admin_graph

    final = admin_graph.run(str(chat_id), message)
    messages = final.get("messages") or []
    used = [t["name"] for m in messages for t in (getattr(m, "tool_calls", None) or [])]
    if used:
        log.info("admin %s used: %s", chat_id, used)

    reply = llm.text_of(messages[-1]) if messages else ""
    return reply.strip() or "Done."
