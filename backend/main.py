"""FastAPI app tying router + tutor + translator together, plus a static chat UI.

Per-session memory (SQLite): every session gets a cookie `sid`; attempts,
progress, the current problem, and the full conversation are persisted and keyed
by it. Problems come from Codeforces and are chosen adaptively from history.

POST /chat        {message}                -> {intent, reply, meta}
POST /new-problem                          -> {problem}
GET  /problem                              -> current problem summary
GET  /history                              -> prior chat messages (restore UI)
GET  /progress                             -> attempt/solve stats
GET  /                                      -> the chat UI
"""
from __future__ import annotations

import os
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import memory, router, state, translator, tutor
from .problem import Problem
from .prompts import REFUSAL_MESSAGE

app = FastAPI(title="CP Tutor")

_DIST = os.path.join(os.path.dirname(__file__), "..", "frontend", "dist")


@app.on_event("startup")
def _startup() -> None:
    memory.init()


@app.middleware("http")
async def ensure_session(request: Request, call_next):
    sid = request.cookies.get("sid")
    fresh = sid is None
    if fresh:
        sid = uuid4().hex
    request.state.sid = sid
    response = await call_next(request)
    if fresh:
        response.set_cookie("sid", sid, httponly=True, samesite="lax",
                            max_age=60 * 60 * 24 * 365)
    return response


class ChatRequest(BaseModel):
    message: str


class ChatResponse(BaseModel):
    intent: str
    reply: str
    meta: dict = {}


def _summary(p: Problem) -> dict:
    return {
        "name": p.name, "statement": p.statement, "statement_html": p.statement_html,
        "tags": p.tags, "url": p.url, "rating": p.rating, "source": p.source,
        "time_limit_ms": p.time_limit_ms, "num_sample_tests": len(p.tests),
    }


@app.get("/problem")
def problem(request: Request) -> dict:
    return _summary(state.current(request.state.sid))


@app.post("/new-problem")
def new_problem(request: Request) -> dict:
    return _summary(state.load_new(request.state.sid))


@app.get("/history")
def history(request: Request) -> dict:
    return {"messages": memory.get_messages(request.state.sid)}


@app.get("/progress")
def progress(request: Request) -> dict:
    return memory.progress(request.state.sid)


@app.post("/chat", response_model=ChatResponse)
def chat(request: Request, req: ChatRequest) -> ChatResponse:
    sid = request.state.sid
    try:
        resp = _handle(sid, req.message)
    except Exception as exc:  # keep the UI usable on transient LLM/sandbox errors
        return ChatResponse(
            intent="error",
            reply=("Sorry — I hit a temporary hiccup talking to the model "
                   "(it may be briefly overloaded). Please try that again."),
            meta={"error": type(exc).__name__, "detail": str(exc)[:300]},
        )
    # persist the exchange for restore + tutor recall
    memory.add_message(sid, "user", req.message, resp.intent)
    memory.add_message(sid, "assistant", resp.reply, resp.intent)
    return resp


def _handle(sid: str, message: str) -> ChatResponse:
    routed = router.route(message)
    prob = state.current(sid)

    if routed.intent == "new_problem":
        prob = state.load_new(sid)
        rating = f" (rating {prob.rating})" if prob.rating else ""
        return ChatResponse(
            intent="new_problem",
            reply=(f"Here's a new problem: {prob.name}{rating}. It's shown on the "
                   "left. Read it, then describe how you'd solve it and I'll build "
                   "and run your approach — or ask me about any general concept."),
            meta={"problem": _summary(prob), "reason": routed.reason},
        )

    if routed.intent == "strategy":
        return ChatResponse(intent="strategy", reply=REFUSAL_MESSAGE,
                            meta={"reason": routed.reason})

    if routed.intent == "concept":
        reply = tutor.answer(prob, message, memory.tutor_history(sid))
        return ChatResponse(intent="concept", reply=reply, meta={"reason": routed.reason})

    if routed.intent == "solution":
        outcome = translator.translate_and_run(prob, message)
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
