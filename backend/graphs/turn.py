"""One chat turn: the graph that decides which agent, if any, gets to speak.

    START ─► route ─┬─ chitchat ────────────────────────────► respond ─► END
                    ├─ strategy ──► refuse ─────────────────► respond ─► END
                    ├─ summarize ─► recap ──────────────────► respond ─► END
                    ├─ new_problem ► switch ────────────────► respond ─► END
                    ├─ concept|meta ► tutor ────────────────► respond ─► END
                    └─ solution ──► build ─┬─ not ready ────► respond ─► END
                                           ├─ defer ─► queue ► respond ─► END
                                           └─ run ───► verdict ► respond ─► END

The router is the first layer of the guardrail and the reason this is a graph
rather than a chain: `strategy` — "how should I solve this?" — reaches `refuse`,
which is the one branch with no model call in it at all. A refusal that came
from asking a model to refuse would be a refusal the next prompt could argue
with.

This is the only graph in the system compiled with a checkpointer. `messages`
accumulates and everything else is per-turn; the thread id is the session id, so
the cookie that scopes memory scopes the conversation too. See `checkpoint.py`.

`_handle` in main.py is the entry point and its signature did not change, which
is why `bot.py` — the whole Telegram path, queue and webhook included — needed
no edits at all.
"""
from __future__ import annotations

from langchain_core.messages import AIMessage, HumanMessage

from langgraph.graph import END, START, StateGraph

from .. import (memory, router, rules, state as session, summarizer, translator,
                tutor)
from ..prompts import REFUSAL_MESSAGE
from ..sandbox import run_cpp
from . import checkpoint
from .schema import TurnState


# ------------------------------------------------------------------- routing

def _route(state: TurnState) -> dict:
    """Classify the message — unless the caller already did.

    The bot has to know the intent *before* this graph runs, because the intent
    decides how the message is queued. It passes what it found rather than
    paying for a second classification of the same sentence.
    """
    if state.get("intent"):
        return {}
    routed = router.route(state["message"])
    return {"intent": routed.intent, "rating_delta": routed.rating_delta,
            "reason": routed.reason}


def _pick(state: TurnState) -> str:
    intent = state.get("intent") or "chitchat"
    if intent in ("concept", "meta"):
        return "tutor"
    return {"summarize": "recap", "new_problem": "switch",
            "strategy": "refuse", "solution": "build"}.get(intent, "chitchat")


# ------------------------------------------------------- the cheap branches

def _chitchat(state: TurnState) -> dict:
    return {"reply": (
        "Hi! Describe the solution you have in mind for the problem on the "
        "left and I'll run it, ask me to explain any general programming "
        "concept, or say 'give me another problem' to switch."),
        "meta": {"reason": state.get("reason", "")}}


def _refuse(state: TurnState) -> dict:
    """No model call. The refusal is a constant, and that is the point: there is
    no prompt for a learner to argue with, and nothing for one to talk round."""
    return {"reply": REFUSAL_MESSAGE,
            "meta": {"reason": state.get("reason", ""), "rules_applied": rules.ids()}}


def _recap(state: TurnState) -> dict:
    sid = state["sid"]
    problem = session.current(sid)
    key = problem.url or "fallback"
    if not memory.has_activity(sid, key):
        return {"reply": ("There's nothing to summarize on this problem yet — "
                          "describe an approach and I'll run it, or ask me a "
                          "concept question first."), "meta": {}}
    recap = summarizer.summarize(problem, memory.attempts_for(sid, key),
                                 memory.concept_questions_for(sid, key))
    memory.save_summary(sid, key, problem.name, recap)
    return {"reply": recap, "meta": {"recap": recap}}


def _switch(state: TurnState) -> dict:
    """Load a different problem, recapping the one being left."""
    sid = state["sid"]
    leaving = session.current(sid)
    old_key = leaving.url or "fallback"

    recap = None
    if memory.has_activity(sid, old_key):
        recap = summarizer.summarize(leaving, memory.attempts_for(sid, old_key),
                                     memory.concept_questions_for(sid, old_key))
        memory.save_summary(sid, old_key, leaving.name, recap)

    delta = max(-500, min(500, state.get("rating_delta") or 0))
    fresh = session.load_new(sid, delta)
    label = ("a harder problem" if delta > 0 else
             "an easier problem" if delta < 0 else "a new problem")
    rating = f" (rating {fresh.rating})" if fresh.rating else ""
    announce = (f"Here's {label}: {fresh.name}{rating}. It's shown on the left. "
                "Read it, then describe how you'd solve it and I'll build and run "
                "your approach — or ask me about any general concept.")

    return {
        "reply": f"Recap of {leaving.name}:\n{recap}\n\n{announce}" if recap else announce,
        "meta": {"problem": session.summary(fresh), "rating_delta": delta,
                 "recap": recap, "reason": state.get("reason", "")},
    }


def _tutor(state: TurnState) -> dict:
    """The only agent holding retrieval tools, so anything needing memory or
    another learner's notes has to arrive here."""
    sid = state["sid"]
    problem = session.current(sid)
    answer = tutor.answer(problem, state["message"], _history(state),
                          user_id=state["uid"],
                          problem_key=problem.url or "fallback")
    return {
        "reply": answer.reply,
        "meta": {"reason": state.get("reason", ""),
                 "rules_applied": answer.rules_applied,
                 "facts_used": answer.facts_used, "notes_seen": answer.notes_seen,
                 "tools_called": answer.tools_called},
    }


def _history(state: TurnState) -> list[dict]:
    """Prior conversation for the tutor, from the checkpoint.

    The checkpointed thread is the live conversation, so this is what makes the
    checkpointer load-bearing rather than decorative. It holds completed turns
    only (see `_respond`), so everything in it is prior context — this turn's
    own message is passed to the tutor separately.

    Falls back to the relational store when the thread is empty: a learner whose
    account predates this graph, or whose checkpoints were cleared, should not
    lose their history. Trimmed either way, because the thread is unbounded and
    a context window is not.
    """
    prior = [m for m in (state.get("messages") or [])
             if isinstance(m, (HumanMessage, AIMessage))]
    if not prior:
        return memory.tutor_history(state["sid"])[-checkpoint.HISTORY_WINDOW:]
    return [{"role": "assistant" if isinstance(m, AIMessage) else "user",
             "content": m.text if isinstance(m.text, str) else str(m.content)}
            for m in prior[-checkpoint.HISTORY_WINDOW:]]


# ------------------------------------------------------- the solution branch

def _build(state: TurnState) -> dict:
    """Screen, generate, and put it past the fidelity critic. Runs nothing."""
    problem = session.current(state["sid"])
    built = translator.build_program(problem, state["message"],
                                     run_id=state["run_id"])
    if not built.ready:
        outcome = built.outcome
        n = memory.record_attempt(state["sid"], problem.url or "fallback",
                                  problem.name, problem.rating, state["message"],
                                  outcome.verdict)
        return {
            "ready": False, "verdict": outcome.verdict, "reply": outcome.reply,
            "meta": {"verdict": outcome.verdict, "attempt_number": n,
                     "critic_status": outcome.critic_status,
                     "critic_rounds": outcome.critic_rounds},
        }
    return {"ready": True, "cpp_source": built.cpp_source,
            "approach_summary": built.approach_summary,
            "critic_status": built.critic_status,
            "critic_rounds": built.critic_rounds, "violations": built.violations}


def _after_build(state: TurnState) -> str:
    if not state.get("ready"):
        return "respond"
    # A browser can hold a 20-second request open; a conversation cannot. So the
    # web path runs the program here and the chat path hands it to a worker.
    return "queue" if state.get("defer") else "verdict"


def _queue(state: TurnState) -> dict:
    """Hand the run to a worker; the verdict arrives later by signed webhook."""
    problem = session.current(state["sid"])
    defer = state["defer"]
    job_id = memory.enqueue_job(
        run_id=state["run_id"], user_id=state["uid"], channel=defer["channel"],
        chat_id=defer["chat_id"], problem_key=problem.url or "fallback",
        described_approach=state["message"],
        approach_summary=state["approach_summary"], cpp_source=state["cpp_source"],
        critic_status=state["critic_status"], critic_rounds=state["critic_rounds"],
        violations=state.get("violations") or [],
    )
    return {
        "verdict": "QUEUED",
        "reply": ("Built it, and a fidelity check passed. It's queued to run "
                  "against the tests now — I'll message you the moment there's "
                  "a verdict. You don't need to wait here."),
        "meta": {"verdict": "QUEUED", "job_id": job_id,
                 "critic_status": state["critic_status"],
                 "critic_rounds": state["critic_rounds"],
                 "violations": state.get("violations") or []},
    }


def _verdict(state: TurnState) -> dict:
    """Compile, run against the tests, and report back — inline."""
    problem = session.current(state["sid"])
    run = run_cpp(problem, state["cpp_source"])
    outcome = translator.finish_run(
        state["approach_summary"], state["cpp_source"], run,
        critic_status=state["critic_status"], critic_rounds=state["critic_rounds"],
        violations=state.get("violations") or [], run_id=state["run_id"])
    n = memory.record_attempt(state["sid"], problem.url or "fallback", problem.name,
                              problem.rating, state["message"], outcome.verdict)
    return {
        "verdict": outcome.verdict, "reply": outcome.reply,
        "meta": {
            "verdict": outcome.verdict, "attempt_number": n,
            "approach_summary": outcome.approach_summary,
            "cpp_source": outcome.cpp_source,
            "critic_status": outcome.critic_status,
            "critic_rounds": outcome.critic_rounds,
            "violations": outcome.violations,
            "handoffs": memory.scratch_for(state["run_id"]),
        },
    }


# ------------------------------------------------------------------- the end

def _respond(state: TurnState) -> dict:
    """Commit the exchange to the conversation — the question *and* the answer.

    Both are written here, at the end, rather than the question being seeded at
    the start. A turn that raises partway (an overloaded model, a sandbox that
    will not start) then leaves the thread untouched instead of a question with
    no answer, which the next turn would replay at the model as if it had been
    ignored. The thread holds completed turns only.

    Every branch lands here, which is what makes it a real transcript rather
    than a record of whichever paths remembered to write to it.
    """
    return {"messages": [HumanMessage(content=state["message"]),
                         AIMessage(content=state.get("reply") or "")]}


# ---------------------------------------------------------------- the wiring

BRANCHES = ("chitchat", "refuse", "recap", "switch", "tutor", "build")


def _build_graph() -> StateGraph:
    graph = StateGraph(TurnState)
    graph.add_node("route", _route)
    graph.add_node("chitchat", _chitchat)
    graph.add_node("refuse", _refuse)
    graph.add_node("recap", _recap)
    graph.add_node("switch", _switch)
    graph.add_node("tutor", _tutor)
    graph.add_node("build", _build)
    graph.add_node("queue", _queue)
    graph.add_node("verdict", _verdict)
    graph.add_node("respond", _respond)

    graph.add_edge(START, "route")
    graph.add_conditional_edges("route", _pick, list(BRANCHES))
    graph.add_conditional_edges("build", _after_build, ["queue", "verdict", "respond"])
    for node in ("chitchat", "refuse", "recap", "switch", "tutor", "queue", "verdict"):
        graph.add_edge(node, "respond")
    graph.add_edge("respond", END)
    return graph


BUILDER = _build_graph()

_compiled = None


def graph():
    """The compiled turn graph, checkpointed.

    Compiled on first use rather than at import: the checkpointer opens the
    SQLite file, and importing this module must not decide where that file is
    before the tests have finished pointing CP_TUTOR_DB somewhere disposable.
    """
    global _compiled
    if _compiled is None:
        _compiled = BUILDER.compile(checkpointer=checkpoint.saver())
    return _compiled


def reset() -> None:
    """Drop the compiled graph (and its checkpointer). Tests only."""
    global _compiled
    _compiled = None
    checkpoint.reset()


def run(sid: str, uid: str, message: str, run_id: str,
        defer: dict | None = None, routed=None) -> TurnState:
    """One turn against this learner's thread.

    Every per-turn field is seeded explicitly. That is not defensive noise: this
    graph resumes a thread, so anything not overwritten here is still holding
    last turn's value.
    """
    seed: TurnState = {
        "sid": sid, "uid": uid, "message": message, "run_id": run_id,
        "defer": defer,
        "intent": routed.intent if routed else "",
        "rating_delta": routed.rating_delta if routed else 0,
        "reason": routed.reason if routed else "",
        "problem_key": "",
        "ready": False, "cpp_source": "", "approach_summary": "",
        "critic_status": "", "critic_rounds": 0, "violations": [],
        "reply": "", "verdict": "", "meta": {},
        # deliberately NOT seeded with this turn's message: `_respond` commits
        # the question and the answer together, so a turn that fails partway
        # leaves no half-exchange behind
        "messages": [],
    }
    return graph().invoke(seed, checkpoint.thread(sid))
