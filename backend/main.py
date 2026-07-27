"""FastAPI app tying the agents, the three memory stores, and the SPA together.

Identity: a `uid` cookie names the learner (no password — this is a prototype,
and the interesting question is memory scoping, not authentication). Everything
private is scoped to that name; notes and the problem catalogue are shared.

Memory lives in three stores, each fitting what it holds:
  * SQLite (memory.py)          — problems, attempts, notes: structured, queried
  * JSON documents (docstore.py) — facts and rules the agent writes itself
  * markdown (rules/)            — operating rules an admin edits by hand

Every handled request is written to the run log, which a separate job
(backend/monitor.py) grades afterwards.

POST /chat        {message}                -> {intent, reply, meta}
GET  /problem                              -> current problem summary
POST /new-problem                          -> load a different problem
POST /reset                                -> wipe this learner's session
GET  /history                              -> prior chat messages (restore UI)
GET  /progress                             -> attempt/solve stats
GET/POST /whoami                           -> who the current learner is
GET/POST/DELETE /notes                     -> shared, learner-written notes
GET  /memory                               -> what the agent remembers (3 stores)
DELETE /memory/{doc_id}                    -> forget one document
GET  /monitor/report                       -> the latest monitor report
POST /monitor/run                          -> trigger a monitor pass (demo only)
GET  /                                     -> the built SPA
"""
from __future__ import annotations

import logging
import os
import uuid

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import docstore, memory, monitor, reflect, router, rules, state, summarizer, translator, tutor
from .problem import Problem
from .prompts import REFUSAL_MESSAGE

app = FastAPI(title="NLCP2G")
log = logging.getLogger("cp_tutor.main")

_DIST = os.path.join(os.path.dirname(__file__), "..", "frontend", "dist")


@app.on_event("startup")
def _startup() -> None:
    memory.init()


@app.middleware("http")
async def ensure_identity(request: Request, call_next):
    """Resolve the learner from the `uid` cookie; default to 'guest'.

    The session scope is derived from the identity rather than being an
    independent random id, so switching user in one browser switches the whole
    memory scope — which is what makes private-vs-shared demonstrable in two
    tabs instead of two machines.
    """
    raw = request.cookies.get("uid") or "guest"
    uid = docstore.safe_user(raw)
    request.state.uid = uid
    request.state.sid = f"u:{uid}"
    response = await call_next(request)
    # /whoami sets the cookie itself; a second Set-Cookie for the same name here
    # would race it and could switch the learner straight back.
    already_set = any("uid=" in h for h in response.headers.getlist("set-cookie"))
    if not already_set and request.cookies.get("uid") != uid:
        response.set_cookie("uid", uid, httponly=False, samesite="lax",
                            max_age=60 * 60 * 24 * 365)
    return response


class ChatRequest(BaseModel):
    message: str


class ChatResponse(BaseModel):
    intent: str
    reply: str
    meta: dict = {}


class WhoRequest(BaseModel):
    user: str


class NoteRequest(BaseModel):
    body: str
    kind: str = "note"


def _summary(p: Problem) -> dict:
    # every problem the system serves also lands in the relational catalogue,
    # so it can be queried by rating and tag later
    key = p.url or "fallback"
    memory.upsert_problem(key, p.name, p.rating, p.tags, p.url, p.source,
                          p.time_limit_ms, p.memory_limit_mb, len(p.tests))
    return {
        "key": key,
        "name": p.name, "statement": p.statement, "statement_html": p.statement_html,
        "tags": p.tags, "url": p.url, "rating": p.rating, "source": p.source,
        "time_limit_ms": p.time_limit_ms, "num_sample_tests": len(p.tests),
    }


# ------------------------------------------------------------------ identity

@app.get("/whoami")
def whoami(request: Request) -> dict:
    return {"user": request.state.uid}


@app.post("/whoami")
def set_user(req: WhoRequest) -> JSONResponse:
    """Switch learner. No password: identity here scopes memory, it doesn't
    protect anything, and pretending otherwise would be worse than not having it."""
    uid = docstore.safe_user(req.user)
    resp = JSONResponse({"user": uid})
    resp.set_cookie("uid", uid, httponly=False, samesite="lax",
                    max_age=60 * 60 * 24 * 365)
    return resp


# ------------------------------------------------------------------- problem

@app.get("/problem")
def problem(request: Request) -> dict:
    return _summary(state.current(request.state.sid))


@app.post("/new-problem")
def new_problem(request: Request) -> dict:
    return _summary(state.load_new(request.state.sid))


@app.post("/reset")
def reset(request: Request) -> dict:
    """Full reset for this learner: chat, progress, attempts, summaries, and
    their private documents. Shared notes and the audit trail survive."""
    sid, uid = request.state.sid, request.state.uid
    state.reset(sid)
    memory.reset(sid)
    docstore.clear(uid)
    return _summary(state.load_new(sid))


@app.get("/history")
def history(request: Request) -> dict:
    return {"messages": memory.get_messages(request.state.sid)}


@app.get("/progress")
def progress(request: Request) -> dict:
    return memory.progress(request.state.sid)


# --------------------------------------------------------------------- notes

@app.get("/notes")
def get_notes(request: Request) -> dict:
    """Notes on the current problem — every learner's, not just this one's."""
    key = state.current(request.state.sid).url or "fallback"
    return {"problem_key": key, "notes": memory.notes_for(key)}


@app.post("/notes")
def post_note(request: Request, req: NoteRequest) -> dict:
    key = state.current(request.state.sid).url or "fallback"
    note_id = memory.add_note(key, request.state.uid, req.body, req.kind)
    return {"id": note_id, "problem_key": key, "notes": memory.notes_for(key)}


@app.delete("/notes/{note_id}")
def remove_note(note_id: int) -> dict:
    return {"deleted": memory.delete_note(note_id)}


# -------------------------------------------------------------------- memory

@app.get("/memory")
def get_memory(request: Request) -> dict:
    """Everything the system remembers for this learner, by store. Deliberately
    inspectable: memory you cannot look at is memory you cannot debug."""
    uid, sid = request.state.uid, request.state.sid
    docs = docstore.all_docs(uid)
    key = memory.get_current_ref(sid)
    return {
        "user": uid,
        "relational": {
            "progress": memory.progress(sid),
            "summaries": memory.get_summaries(sid),
            "notes_on_current": memory.notes_for(key[0]) if key else [],
        },
        "documents": {
            "facts": [d for d in docs if d["type"] == "fact"],
            "rules": [d for d in docs if d["type"] == "rule"],
        },
        "operating_rules": {"ids": rules.ids(), "titles": rules.titles(),
                            "text": rules.text()},
    }


@app.delete("/memory/{doc_id}")
def forget(request: Request, doc_id: str) -> dict:
    return {"forgotten": docstore.forget(doc_id, request.state.uid)}


# ------------------------------------------------------------------- monitor

@app.get("/monitor/report")
def monitor_report() -> dict:
    latest = monitor.latest_report()
    return {
        "report": latest[1] if latest else None,
        "file": latest[0] if latest else None,
        "judgments": memory.judgments(limit=50),
    }


@app.post("/monitor/run")
def monitor_run(limit: int = 25) -> dict:
    """Convenience trigger for demos. The monitor is a separate job — the real
    entry point is `python -m backend.monitor [--watch N]`. This endpoint runs
    the same pass on request; it is still out of band, never part of a chat turn."""
    path = monitor.run_once(limit)
    return {"report_file": os.path.basename(path) if path else None}


@app.get("/rules")
def get_rules() -> dict:
    return {"text": rules.text(), "ids": rules.ids(), "titles": rules.titles()}


# ---------------------------------------------------------------------- chat

@app.post("/chat", response_model=ChatResponse)
def chat(request: Request, req: ChatRequest) -> ChatResponse:
    sid, uid = request.state.sid, request.state.uid
    run_id = uuid.uuid4().hex[:12]
    # the problem active when the message was sent — tags the exchange so
    # per-problem summaries can be built later (a new_problem turn switches the
    # current problem inside _handle, so capture it first)
    prob = state.current(sid)
    tag_key = prob.url or "fallback"

    try:
        resp = _handle(sid, uid, req.message, run_id)
    except Exception as exc:  # keep the UI usable on transient LLM/sandbox errors
        log.exception("chat failed")
        resp = ChatResponse(
            intent="error",
            reply=("Sorry — I hit a temporary hiccup talking to the model "
                   "(it may be briefly overloaded). Please try that again."),
            meta={"error": type(exc).__name__, "detail": str(exc)[:300]},
        )

    # persist the exchange for restore + tutor recall
    memory.add_message(sid, "user", req.message, resp.intent, tag_key)
    memory.add_message(sid, "assistant", resp.reply, resp.intent, tag_key)

    # write the run log the monitor grades, out of band, later
    memory.log_run(
        run_id=run_id, user_id=uid, sid=sid, problem_key=tag_key,
        problem_name=prob.name, intent=resp.intent, user_message=req.message,
        reply=resp.reply, verdict=resp.meta.get("verdict"),
        rules_applied=resp.meta.get("rules_applied") or rules.ids(),
        facts_used=resp.meta.get("facts_used") or [],
        notes_seen=resp.meta.get("notes_seen") or [],
        tools_called=resp.meta.get("tools_called") or [],
    )

    # decide whether this turn is worth remembering (usually it isn't)
    saved = reflect.consider(uid, req.message, resp.reply, resp.intent, tag_key)
    if saved:
        resp.meta["memory_saved"] = {"id": saved["id"], "type": saved["type"],
                                     "text": saved["text"]}
    resp.meta["run_id"] = run_id
    return resp


def _summarize_current(sid: str) -> ChatResponse:
    prob = state.current(sid)
    key = prob.url or "fallback"
    if not memory.has_activity(sid, key):
        return ChatResponse(
            intent="summarize",
            reply=("There's nothing to summarize on this problem yet — describe an "
                   "approach and I'll run it, or ask me a concept question first."),
            meta={},
        )
    recap = summarizer.summarize(
        prob, memory.attempts_for(sid, key), memory.concept_questions_for(sid, key))
    memory.save_summary(sid, key, prob.name, recap)
    return ChatResponse(intent="summarize", reply=recap, meta={"recap": recap})


def _handle(sid: str, uid: str, message: str, run_id: str) -> ChatResponse:
    routed = router.route(message)
    prob = state.current(sid)

    if routed.intent == "summarize":
        return _summarize_current(sid)

    if routed.intent == "new_problem":
        # auto-summarize the problem being left, if there was any activity on it
        old_key = prob.url or "fallback"
        recap = None
        if memory.has_activity(sid, old_key):
            recap = summarizer.summarize(
                prob, memory.attempts_for(sid, old_key),
                memory.concept_questions_for(sid, old_key))
            memory.save_summary(sid, old_key, prob.name, recap)

        delta = max(-500, min(500, routed.rating_delta or 0))
        new_prob = state.load_new(sid, delta)
        label = "a harder problem" if delta > 0 else \
                "an easier problem" if delta < 0 else "a new problem"
        rating = f" (rating {new_prob.rating})" if new_prob.rating else ""
        announce = (f"Here's {label}: {new_prob.name}{rating}. It's shown on the "
                    "left. Read it, then describe how you'd solve it and I'll build "
                    "and run your approach — or ask me about any general concept.")
        reply = (f"Recap of {prob.name}:\n{recap}\n\n{announce}") if recap else announce
        return ChatResponse(
            intent="new_problem",
            reply=reply,
            meta={"problem": _summary(new_prob), "rating_delta": delta,
                  "recap": recap, "reason": routed.reason},
        )

    if routed.intent == "strategy":
        return ChatResponse(intent="strategy", reply=REFUSAL_MESSAGE,
                            meta={"reason": routed.reason, "rules_applied": rules.ids()})

    # `concept` (general CS) and `meta` (about the learner, or about what other
    # learners wrote) both go to the tutor — it is the only agent holding the
    # retrieval tools, so anything needing memory or notes has to arrive here.
    if routed.intent in ("concept", "meta"):
        answer = tutor.answer(prob, message, memory.tutor_history(sid),
                              user_id=uid, problem_key=prob.url or "fallback")
        return ChatResponse(
            intent=routed.intent, reply=answer.reply,
            meta={"reason": routed.reason, "rules_applied": answer.rules_applied,
                  "facts_used": answer.facts_used, "notes_seen": answer.notes_seen,
                  "tools_called": answer.tools_called},
        )

    if routed.intent == "solution":
        outcome = translator.translate_and_run(prob, message, run_id=run_id)
        n = memory.record_attempt(sid, prob.url or "fallback", prob.name,
                                  prob.rating, message, outcome.verdict)
        return ChatResponse(
            intent="solution",
            reply=outcome.reply,
            meta={
                "verdict": outcome.verdict,
                "attempt_number": n,
                "approach_summary": outcome.approach_summary,
                "cpp_source": outcome.cpp_source,
                "critic_status": outcome.critic_status,
                "critic_rounds": outcome.critic_rounds,
                "violations": outcome.violations,
                "handoffs": memory.scratch_for(run_id),
            },
        )

    return ChatResponse(
        intent="chitchat",
        reply=("Hi! Describe the solution you have in mind for the problem on the "
               "left and I'll run it, ask me to explain any general programming "
               "concept, or say 'give me another problem' to switch."),
        meta={"reason": routed.reason},
    )


# Serve the built React app (frontend/dist) at "/". API routes above are
# registered first, so they take precedence over this catch-all mount.
if os.path.isdir(_DIST):
    app.mount("/", StaticFiles(directory=_DIST, html=True), name="spa")
else:
    @app.get("/")
    def _needs_build() -> HTMLResponse:
        return HTMLResponse(
            "<h1>Frontend not built</h1><p>Run <code>cd frontend &amp;&amp; npm install "
            "&amp;&amp; npm run build</code>, or use the Vite dev server "
            "(<code>npm run dev</code> on :5173).</p>",
            status_code=503,
        )
