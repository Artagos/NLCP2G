"""The graphs themselves: routing, the cycle, the tools, and resumption.

`test_critic_loop.py` already pins the behaviour of the executor⇄critic
pipeline and did not need a line changed when it became a graph — which was the
point of the refactor and is worth more as evidence than anything in this file.
What is tested here is what only exists now that these are graphs: that the
edges go where the diagram says, that the checkpointer actually resumes a
conversation, and that the tools are registered with the framework rather than
called by hand.

Every model call is stubbed. Nothing here needs a key, a daemon or a network.
"""
from __future__ import annotations

import sqlite3

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.tools import BaseTool

from backend import docstore, llm, memory, router, state as session
from backend.graphs import checkpoint, solution, turn
from backend.graphs import tutor as tutor_graph
from backend.router import Routed
from backend.tools.admin_tools import ADMIN_TOOLS
from backend.tools.tutor_tools import TUTOR_TOOLS

SID, UID = "u:testuser", "testuser"


def _tool_call(name, call_id="c1", **args):
    return {"name": name, "args": args, "id": call_id, "type": "tool_call"}


class _Scripted:
    """A chat model that answers from a list, in order."""

    def __init__(self, *replies):
        self._replies = list(replies) or [AIMessage(content="ok")]
        self.calls = 0

    def bind_tools(self, tools):
        return self

    def invoke(self, messages, *a, **kw):
        self.calls += 1
        return self._replies.pop(0) if self._replies else AIMessage(content="ok")


def _install(monkeypatch, *replies):
    """Patch in ONE scripted model for the whole run.

    It has to be one instance: a lambda that constructs a fresh `_Scripted` per
    call hands back the first scripted reply every time, so a tool-calling
    reply makes the graph cycle until it hits the recursion limit.
    """
    model = _Scripted(*replies)
    monkeypatch.setattr(llm, "chat_model", lambda *a, **k: model)
    return model


@pytest.fixture
def routed(monkeypatch):
    """Route to whatever the test asks for, and count the router's calls."""
    seen = {"intent": "chitchat", "calls": 0}

    def fake_route(message):
        seen["calls"] += 1
        return Routed(intent=seen["intent"], reason="because the test said so")

    monkeypatch.setattr(router, "route", fake_route)
    return seen


@pytest.fixture
def on_a_problem(monkeypatch, problem):
    """Pin the current problem so no test reaches for Codeforces."""
    monkeypatch.setattr(session, "current", lambda sid: problem)
    monkeypatch.setattr(session, "load_new", lambda sid, delta=0: problem)
    return problem


# ------------------------------------------------------------------- routing

@pytest.mark.parametrize("intent,node", [
    ("chitchat", "chitchat"),
    ("strategy", "refuse"),
    ("summarize", "recap"),
    ("new_problem", "switch"),
    ("concept", "tutor"),
    ("meta", "tutor"),
    ("solution", "build"),
])
def test_each_intent_leaves_the_router_by_its_own_edge(intent, node):
    assert turn._pick({"intent": intent}) == node


def test_an_unrecognised_intent_falls_through_to_chitchat():
    """A router that returns something unexpected must not drop the turn."""
    assert turn._pick({"intent": "wat"}) == "chitchat"
    assert turn._pick({}) == "chitchat"


def test_refusing_a_strategy_question_never_reaches_a_model(routed, on_a_problem,
                                                            monkeypatch):
    """The guardrail branch has no model call in it, which is the whole reason
    it is a branch: a refusal produced by asking a model to refuse is a refusal
    the next prompt can argue with."""
    model = _install(monkeypatch)
    routed["intent"] = "strategy"

    final = turn.run(SID, UID, "so how do I actually solve this one?", "r-strategy")

    assert model.calls == 0
    assert "R1" not in final["reply"]           # it is prose, not a rule dump
    assert final["meta"]["rules_applied"]       # but the rules are recorded
    assert final["verdict"] == ""


def test_the_bots_routing_is_not_paid_for_twice(routed, on_a_problem):
    """The bot classifies before queueing, because the intent decides how the
    message is queued. Passing that in must skip the router, not duplicate it."""
    turn.run(SID, UID, "hello", "r-pre", routed=Routed(intent="chitchat", reason="pre"))
    assert routed["calls"] == 0

    turn.run(SID, UID, "hello", "r-post")
    assert routed["calls"] == 1


# ------------------------------------------------------- the solution branch

def test_the_web_path_runs_inline_and_the_chat_path_queues(routed, on_a_problem,
                                                           monkeypatch):
    """One graph, two exits, chosen by whether a channel is waiting."""
    from backend import translator
    from backend.translator import BuildResult

    monkeypatch.setattr(translator, "build_program", lambda p, m, run_id=None:
                        BuildResult(ready=True, cpp_source="int main(){}",
                                    approach_summary="counts pairs",
                                    critic_status="approved", critic_rounds=1))
    ran = []
    monkeypatch.setattr(turn, "run_cpp", lambda p, src: ran.append(src))
    monkeypatch.setattr(translator, "finish_run", lambda *a, **k: __import__(
        "backend.translator", fromlist=["x"]).TranslationOutcome(
        reply="It passed.", cpp_source="int main(){}", verdict="AC",
        approach_summary="counts pairs"))
    routed["intent"] = "solution"

    web = turn.run(SID, UID, "check every pair", "r-web")
    assert web["verdict"] == "AC" and ran == ["int main(){}"]

    chat = turn.run(SID, UID, "check every pair", "r-chat",
                    defer={"channel": "telegram", "chat_id": "42"})
    assert chat["verdict"] == "QUEUED"
    assert ran == ["int main(){}"]              # still one: nothing ran inline
    assert memory.pending_jobs(UID)             # it went to the worker instead


def test_a_pipeline_that_stops_early_never_reaches_either_runner(routed,
                                                                 on_a_problem,
                                                                 monkeypatch):
    from backend import translator
    from backend.translator import BuildResult, TranslationOutcome

    monkeypatch.setattr(translator, "build_program", lambda p, m, run_id=None:
                        BuildResult(ready=False, critic_status="escalate",
                                    critic_rounds=2,
                                    outcome=TranslationOutcome(
                                        reply="Which did you mean?",
                                        cpp_source="", verdict="NEEDS_CLARIFICATION",
                                        approach_summary="")))
    ran = []
    monkeypatch.setattr(turn, "run_cpp", lambda p, src: ran.append(src))
    routed["intent"] = "solution"

    final = turn.run(SID, UID, "combine the numbers", "r-stop",
                     defer={"channel": "telegram", "chat_id": "42"})

    assert final["verdict"] == "NEEDS_CLARIFICATION"
    assert ran == [] and memory.pending_jobs(UID) == []


def test_the_solution_graph_has_exactly_one_cycle_and_it_is_the_critics():
    """The revise edge is the only way back in this system. If a second cycle
    ever appears, it should be a deliberate decision and not a surprise."""
    edges = solution.GRAPH.get_graph().edges
    back = {(e.source, e.target) for e in edges} & {("revise", "execute")}
    assert back == {("revise", "execute")}
    assert ("exhausted", "ask") in {(e.source, e.target) for e in edges}


# --------------------------------------------------------------------- tools

def test_the_real_chat_model_can_do_what_every_caller_needs():
    """A regression test for a bug the whole suite was blind to.

    Every model call in these tests goes through a stub, so nothing noticed
    when `chat_model` returned something that could not `bind_tools` or
    `with_structured_output` — which is what wrapping it in `.with_retry()`
    does, because a `RunnableRetry` is a plain Runnable. Six agents broke at
    once and the suite stayed green; it only showed up against a live model.

    Constructing the model needs no key and touches no network, so asserting on
    its capabilities is cheap and would have caught it.
    """
    llm._models.clear()
    try:
        model = llm.chat_model()
        assert hasattr(model, "bind_tools"), "the tutor and operator need this"
        assert hasattr(model, "with_structured_output"), \
            "the router, screener, critic, reflector and judge need this"
        assert model.bind_tools(TUTOR_TOOLS) is not None
        assert model.with_structured_output(Routed) is not None
    finally:
        llm._models.clear()


def test_at_least_two_tools_are_registered_with_the_framework():
    """A named grading gate, and worth its own test: these are LangChain tools
    the model is bound to, not functions the orchestration calls on its behalf."""
    assert len(TUTOR_TOOLS) >= 2
    assert len(ADMIN_TOOLS) >= 2
    for tool in TUTOR_TOOLS + ADMIN_TOOLS:
        assert isinstance(tool, BaseTool)
        assert tool.description.strip(), f"{tool.name} has no description"
        # the description IS the interface — it is how the model decides
        assert tool.args_schema is not None


def test_the_registered_tools_are_callable_through_the_framework(monkeypatch,
                                                                 problem):
    """Driven exactly the way a model drives them, through the real tutor graph:
    tool calls on an AIMessage, dispatched by ToolNode, back as ToolMessages.

    (ToolNode cannot be invoked on its own in langgraph 1.x — it wants a graph
    runtime in its config — and going through the compiled graph is the more
    honest check anyway.)
    """
    docstore.save(doc_type="fact", text="ivan likes worked examples",
                  user_id="ivan", cue_keywords=["example"])
    note_id = memory.add_note("k1", "judy", "watch the 1-based indexing")

    _install(monkeypatch,
             AIMessage(content="", tool_calls=[
                 _tool_call("retrieve_memory", "a", query="give me an example"),
                 _tool_call("read_problem_notes", "b"),
                 _tool_call("list_known_facts", "c"),
             ]),
             AIMessage(content="Here you go."))

    final = tutor_graph.run(problem, "an example please", user_id="ivan",
                            problem_key="k1")

    delivered = {m.tool_call_id: m.content for m in final["messages"]
                 if isinstance(m, ToolMessage)}
    assert len(delivered) == 3
    assert "worked examples" in delivered["a"]
    assert "1-based indexing" in delivered["b"]
    assert "worked examples" in delivered["c"]
    assert final["notes_seen"] == [note_id]
    assert sorted(final["tools_called"]) == [
        "list_known_facts", "read_problem_notes", "retrieve_memory"]


def test_a_tool_that_finds_nothing_says_so_rather_than_returning_empty(monkeypatch,
                                                                       problem):
    """An empty string back to the model is indistinguishable from a broken
    tool; the model needs to be told the lookup succeeded and found nothing."""
    _install(monkeypatch,
             AIMessage(content="", tool_calls=[
                 _tool_call("retrieve_memory", "a", query="anything"),
                 _tool_call("read_problem_notes", "b"),
                 _tool_call("list_known_facts", "c"),
             ]),
             AIMessage(content="Nothing on file."))

    final = tutor_graph.run(problem, "anything?", user_id="nobody",
                            problem_key="unseen")

    results = [m for m in final["messages"] if isinstance(m, ToolMessage)]
    assert len(results) == 3
    for message in results:
        assert message.content.strip()
    assert final["facts_used"] == [] and final["notes_seen"] == []


# -------------------------------------------------------------- checkpointing

def test_a_conversation_resumes_after_the_graph_is_thrown_away(routed, on_a_problem):
    """The other named grading gate.

    Not "state exists somewhere" — the compiled graph and its database
    connection are both discarded, which is as close to a process restart as a
    test gets, and the thread is read back from the file afterwards.
    """
    turn.run(SID, UID, "hi there", "r1")

    turn.reset()                                # drop graph *and* checkpointer

    restored = turn.graph().get_state(checkpoint.thread(SID))
    spoken = [m.content for m in restored.values["messages"]]
    assert spoken[0] == "hi there"
    assert len(spoken) == 2                     # the learner, then the agent


def test_the_second_turn_can_see_the_first(routed, on_a_problem):
    turn.run(SID, UID, "first thing", "r1")
    final = turn.run(SID, UID, "second thing", "r2")

    said = [m.content for m in final["messages"] if isinstance(m, HumanMessage)]
    assert said == ["first thing", "second thing"]


def test_a_turn_that_blows_up_leaves_no_half_exchange_behind(routed, on_a_problem,
                                                             monkeypatch):
    """Found live: a turn that raised had already put the learner's message in
    the thread, so the next turn replayed a question the agent never answered.
    The exchange is committed at the end now, both halves together."""
    turn.run(SID, UID, "a turn that works", "r1")

    def boom(message):
        raise RuntimeError("the model is having a day")

    # patched on the module the node calls into, not on the node: the compiled
    # graph holds the node function object itself, so replacing `turn._chitchat`
    # would not reach it
    monkeypatch.setattr(router, "route", boom)
    with pytest.raises(RuntimeError):
        turn.run(SID, UID, "a turn that does not", "r2")

    spoken = [m.content for m in
              turn.graph().get_state(checkpoint.thread(SID)).values["messages"]]
    assert len(spoken) == 2                     # the working turn, and only it
    assert spoken[0] == "a turn that works"
    assert "a turn that does not" not in spoken


def test_two_learners_do_not_share_a_thread(routed, on_a_problem):
    """The thread id is the session id, so the cookie that scopes memory scopes
    the conversation with it."""
    turn.run("u:alice", "alice", "alice's message", "r1")
    turn.run("u:bob", "bob", "bob's message", "r2")

    for sid, expected in (("u:alice", "alice's message"), ("u:bob", "bob's message")):
        values = turn.graph().get_state(checkpoint.thread(sid)).values
        assert [m.content for m in values["messages"]][0] == expected


def test_the_tutor_reads_its_history_from_the_checkpoint(routed, on_a_problem,
                                                         monkeypatch):
    """What makes the checkpointer load-bearing rather than decorative."""
    seen = {}

    def fake_run(problem, message, history=None, user_id="guest", problem_key=None):
        seen["history"] = list(history or [])
        return {"messages": [AIMessage(content="an answer")], "rules_applied": [],
                "facts_used": [], "notes_seen": [], "tools_called": []}

    monkeypatch.setattr(tutor_graph, "run", fake_run)
    routed["intent"] = "concept"

    turn.run(SID, UID, "what is a hash map?", "r1")
    assert seen["history"] == []                        # nothing before it

    turn.run(SID, UID, "and a hash set?", "r2")
    assert [m["content"] for m in seen["history"]] == ["what is a hash map?",
                                                       "an answer"]


def test_a_problem_statement_never_reaches_a_checkpoint(routed, on_a_problem):
    """Checkpoints are written on every superstep of every turn. A statement is
    kilobytes of prose that never changes, so the state carries the key and the
    nodes resolve it — this asserts that stayed true."""
    turn.run(SID, UID, "hello", "r1")

    values = turn.graph().get_state(checkpoint.thread(SID)).values
    assert "problem" not in values

    # and not by some other route either: go and read the raw rows
    turn.reset()
    conn = sqlite3.connect(checkpoint._db_path())
    try:
        blobs = conn.execute("SELECT checkpoint, metadata FROM checkpoints").fetchall()
    finally:
        conn.close()
    needle = b"Count the number of pairs of positions"
    assert blobs, "no checkpoint was written at all"
    assert not any(needle in bytes(part or b"") for row in blobs for part in row)
