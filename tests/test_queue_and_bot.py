"""The queue, and the bot's dispatch into it.

The queue is the part where "it worked when I sent it a message" is most obviously
not a test: the interesting behaviour only happens when a second message arrives
during the first one. So the handler here is a controllable slow turn — a future a
test releases when it wants — rather than a real turn we hope is slow enough.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

from backend import admin, bot as botmod, memory, router
from backend.channels import FakeChannel, split_message
from backend.main import ChatResponse
from backend.turnqueue import TurnQueue


def run(coro):
    return asyncio.run(coro)


# --------------------------------------------------------------------- queue

class Recorder:
    """A handler whose turns finish only when the test says so.

    The gate applies to ONE named message, not to every turn — otherwise a test
    for cross-learner isolation would block both learners on the same future and
    "pass" for the wrong reason.
    """

    def __init__(self):
        self.started: list[str] = []
        self.finished: list[str] = []
        self.gate = None
        self.gate_on: str | None = None

    async def __call__(self, user_id, chat_id, text):
        self.started.append(text)
        if self.gate is not None and (self.gate_on is None or text == self.gate_on):
            await self.gate
        self.finished.append(text)


def test_messages_for_one_learner_are_processed_in_order():
    async def scenario():
        rec = Recorder()
        q = TurnQueue(rec)
        for text in ("first", "second", "third"):
            await q.submit("alice", "1", text)
        await q.drain()
        await q.close()
        return rec

    rec = run(scenario())
    assert rec.finished == ["first", "second", "third"]


def test_a_message_arriving_mid_turn_is_queued_not_dropped():
    async def scenario():
        rec = Recorder()
        rec.gate = asyncio.get_running_loop().create_future()
        rec.gate_on = "slow one"
        q = TurnQueue(rec)

        first = await q.submit("alice", "1", "slow one")
        await asyncio.sleep(0)                     # let the worker pick it up
        second = await q.submit("alice", "1", "arrived during the first")

        assert first == ("started", 0)
        assert second[0] == "queued" and second[1] >= 1
        assert rec.finished == []                  # still blocked

        rec.gate.set_result(None)
        await q.drain()
        await q.close()
        return rec

    rec = run(scenario())
    assert rec.finished == ["slow one", "arrived during the first"]


def test_overflow_is_refused_loudly_rather_than_silently_discarded():
    async def scenario():
        rec = Recorder()
        rec.gate = asyncio.get_running_loop().create_future()
        rec.gate_on = "in flight"
        q = TurnQueue(rec, max_depth=2)

        await q.submit("alice", "1", "in flight")
        await asyncio.sleep(0)
        statuses = [(await q.submit("alice", "1", f"m{i}"))[0] for i in range(4)]

        rec.gate.set_result(None)
        await q.drain()
        await q.close()
        return statuses, rec

    statuses, rec = run(scenario())
    assert statuses[:2] == ["queued", "queued"]
    assert statuses[2:] == ["dropped", "dropped"]
    # the accepted ones all ran; nothing accepted was lost
    assert rec.finished == ["in flight", "m0", "m1"]


def test_one_learner_cannot_block_another():
    async def scenario():
        rec = Recorder()
        rec.gate = asyncio.get_running_loop().create_future()
        rec.gate_on = "alice slow"
        q = TurnQueue(rec)

        await q.submit("alice", "1", "alice slow")
        await asyncio.sleep(0)
        status, _ = await q.submit("bob", "2", "bob quick")
        await asyncio.sleep(0.05)

        assert status == "started"           # bob was not put behind alice
        assert "bob quick" in rec.finished
        assert "alice slow" not in rec.finished

        rec.gate.set_result(None)
        await q.drain()
        await q.close()

    run(scenario())


def test_a_failing_turn_does_not_stop_the_next_one():
    async def scenario():
        seen = []

        async def handler(user_id, chat_id, text):
            seen.append(text)
            if text == "boom":
                raise RuntimeError("turn exploded")

        q = TurnQueue(handler)
        await q.submit("alice", "1", "boom")
        await q.submit("alice", "1", "after")
        await q.drain()
        await q.close()
        return seen

    assert run(scenario()) == ["boom", "after"]


# ------------------------------------------------------------------- chunking

def test_long_replies_are_split_rather_than_truncated():
    text = "\n\n".join(f"Paragraph {i} " + "x" * 300 for i in range(40))
    chunks = split_message(text, limit=1000)

    assert len(chunks) > 1
    assert all(len(c) <= 1000 for c in chunks)
    # nothing vanished
    assert sum(len(c.replace(" ", "")) for c in chunks) >= len(text.replace(" ", "")) * 0.98


def test_a_short_reply_is_one_chunk_and_an_empty_one_is_none():
    assert split_message("hello") == ["hello"]
    assert split_message("   ") == []


def test_a_word_longer_than_the_limit_is_still_cut():
    assert all(len(c) <= 50 for c in split_message("z" * 500, limit=50))


# ----------------------------------------------------------------- bot dispatch

def _bot(monkeypatch, reply="ok"):
    """A bot whose turns are stubbed — routing and LLMs are not under test here."""
    channel = FakeChannel(name="telegram")
    b = botmod.Bot(channel)
    monkeypatch.setattr(botmod.Bot, "_sync_turn",
                        lambda self, sid, uid, chat, text: reply)
    return b, channel


def test_a_redelivered_update_is_answered_only_once(monkeypatch):
    b, channel = _bot(monkeypatch, reply="answered")

    async def scenario():
        channel.feed("1001", "hello", update_id="42")
        for update in await channel.updates():
            await b.handle_update(update)
            await b.handle_update(update)          # Telegram resends it
        await b.queue.drain()
        await b.close()

    run(scenario())
    assert channel.texts_to("1001").count("answered") == 1


def test_first_contact_links_the_chat_to_a_stable_learner_id(monkeypatch):
    b, channel = _bot(monkeypatch)

    async def scenario():
        channel.feed("777", "hi")
        for u in await channel.updates():
            await b.handle_update(u)
        await b.queue.drain()
        await b.close()

    run(scenario())
    link = memory.get_link("telegram", "777")
    assert link and link["user_id"] == "tg777"
    # renaming is cosmetic; the scope must not move
    memory.link_chat("telegram", "777", "tg777", display_name="Alice")
    assert memory.get_link("telegram", "777")["user_id"] == "tg777"


def test_a_learner_claiming_to_be_an_admin_is_not_one(monkeypatch):
    """The privileged path is chosen by an allow-list, never by what the message
    says about itself."""
    monkeypatch.setenv("CP_TUTOR_ADMIN_CHAT_IDS", "999")
    b, channel = _bot(monkeypatch, reply="ordinary tutor reply")
    called = []
    monkeypatch.setattr(admin, "handle", lambda chat, msg: called.append(msg) or "admin!")

    async def scenario():
        channel.feed("1001", "/admin mute nudges")   # not on the allow-list
        for u in await channel.updates():
            await b.handle_update(u)
        await b.queue.drain()
        await b.close()

    run(scenario())
    assert called == []                              # admin never invoked
    assert channel.texts_to("1001") == ["ordinary tutor reply"]


def test_an_allow_listed_chat_reaches_the_admin_subagent(monkeypatch):
    monkeypatch.setenv("CP_TUTOR_ADMIN_CHAT_IDS", "999")
    b, channel = _bot(monkeypatch)
    monkeypatch.setattr(admin, "handle", lambda chat, msg: f"admin did: {msg}")

    async def scenario():
        channel.feed("999", "/admin list the rules")
        for u in await channel.updates():
            await b.handle_update(u)
        await b.queue.drain()
        await b.close()

    run(scenario())
    assert channel.texts_to("999") == ["admin did: list the rules"]


# ------------------------------------------------- the channel path vs the web path
#
# The bot and the web API are two entry points onto one `_handle`. Anything the
# web path does *around* that call has to be done here too, or a feature quietly
# exists only in the browser. These two both regressed exactly that way.

class _Prob:
    def __init__(self, url, name):
        self.url, self.name, self.rating = url, name, None
        self.source, self.statement = "bank", "..."


def _stub_turn(monkeypatch, prob, intent="concept", reply="ok", meta=None,
               on_handle=None):
    """Run a real _sync_turn with the model calls and the writes stubbed out."""
    monkeypatch.setattr(botmod.state, "current", lambda sid: prob["p"])
    monkeypatch.setattr(botmod.router, "route",
                        lambda m: SimpleNamespace(intent=intent, rating_delta=0,
                                                  reason=""))

    def fake_handle(*a, **k):
        if on_handle:
            on_handle()
        return ChatResponse(intent=intent, reply=reply, meta=meta or {})

    monkeypatch.setattr(botmod.api, "_handle", fake_handle)
    monkeypatch.setattr(botmod.memory, "log_run", lambda **k: None)

    tagged, reflected = [], []
    monkeypatch.setattr(botmod.memory, "add_message",
                        lambda sid, role, text, i, key: tagged.append((role, key)))
    # patch the module slot, not the function on it, so each of these tests fails
    # for its own reason on code where bot.py doesn't import reflect at all
    monkeypatch.setattr(
        botmod, "reflect",
        SimpleNamespace(consider=lambda uid, msg, rep, i, key=None:
                        reflected.append((uid, msg, i))),
        raising=False)
    return tagged, reflected


def test_a_chat_turn_can_still_teach_the_agent_something(monkeypatch):
    """Reflection ran only in the web endpoint, so the agent stopped learning the
    moment a learner moved to the chat — with no error to notice."""
    prob = {"p": _Prob("p/x", "X")}
    _, reflected = _stub_turn(monkeypatch, prob, reply="A hash map is...")
    b = botmod.Bot(FakeChannel(name="telegram"))

    b._sync_turn("u:tg1", "tg1", "1", "keep answers to three sentences from now on")

    assert reflected == [("tg1", "keep answers to three sentences from now on",
                          "concept")]


def test_a_queued_run_is_not_reflected_on_its_own_acknowledgement(monkeypatch):
    prob = {"p": _Prob("p/x", "X")}
    _, reflected = _stub_turn(monkeypatch, prob, intent="solution",
                              reply="Queued to run.", meta={"verdict": "QUEUED"})
    b = botmod.Bot(FakeChannel(name="telegram"))

    b._sync_turn("u:tg1", "tg1", "1", "loop over every pair and count them")

    assert reflected == []          # the reply is an ack, not something learned


def test_a_new_problem_turn_is_tagged_to_the_problem_being_left(monkeypatch):
    """`_handle` switches the current problem, so the key has to be captured
    before the call — otherwise the last exchange on a problem lands in the next
    problem's recap instead of its own."""
    prob = {"p": _Prob("p/old", "Old")}
    tagged, _ = _stub_turn(
        monkeypatch, prob, intent="new_problem", reply="switched",
        on_handle=lambda: prob.__setitem__("p", _Prob("p/new", "New")))
    b = botmod.Bot(FakeChannel(name="telegram"))

    b._sync_turn("u:tg1", "tg1", "1", "give me another problem")

    assert [key for _, key in tagged] == ["p/old", "p/old"]


def test_the_queue_ack_tells_the_learner_where_they_stand(monkeypatch):
    """Head-of-line blocking is the cost of FIFO; the least we can do is say so."""
    channel = FakeChannel(name="telegram")
    b = botmod.Bot(channel)
    gate: asyncio.Future = None

    async def slow(self, sid, uid, chat, text):
        return "done"

    async def scenario():
        nonlocal gate
        gate = asyncio.get_running_loop().create_future()

        async def handler(user_id, chat_id, text):
            if text == "slow":
                await gate
            await channel.send(chat_id, f"reply to {text}")

        b.queue = TurnQueue(handler)
        channel.feed("1001", "slow", update_id="1")
        channel.feed("1001", "quick", update_id="2")
        for u in await channel.updates():
            await b.handle_update(u)
        acks = [t for t in channel.texts_to("1001") if "next in line" in t]
        gate.set_result(None)
        await b.queue.drain()
        await b.close()
        return acks

    acks = run(scenario())
    assert len(acks) == 1 and "next in line" in acks[0]
