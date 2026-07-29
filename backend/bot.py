"""The bot process: the agent living on a chat channel instead of in a notebook.

    python -m backend.bot                    # real Telegram, long polling
    python -m backend.bot --fake             # a fake channel, for a scripted demo

What happens to an inbound message, in order:

  1. **De-duplicated.** Telegram redelivers updates it didn't get an ack for, so
     an update id already seen is dropped. Without this, a crash mid-turn means
     the learner's message is answered twice.
  2. **Attributed.** The chat is linked to a learner id, created on first
     contact. That id is the same memory scope the web UI uses.
  3. **Routed by privilege.** An allow-listed chat reaches the admin subagent.
     Everyone else reaches the tutor, whatever they claim about themselves.
  4. **Queued.** Per-learner FIFO (see turnqueue.py). If a turn is already in
     flight the learner is told where they are in line rather than left guessing.

The sandbox run does not happen here. A turn that reaches the sandbox ends by
handing a job to the worker and acknowledging; the verdict arrives later through
the run-complete webhook. That is what keeps a 60-second run from blocking the
channel, and it is the interactive-mode trigger.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import uuid

from . import admin, main as api, memory, router, state
from .channels import Channel, FakeChannel, TelegramChannel
from .outbound import set_channel
from .turnqueue import TurnQueue

log = logging.getLogger("cp_tutor.bot")

WELCOME = (
    "Hi — I'm a competitive-programming tutor for people who don't write code.\n\n"
    "Read the problem, then tell me in plain words how you'd solve it. I'll turn "
    "exactly that into a program, run it against real tests, and tell you what "
    "happened. I won't tell you how to solve it — that part is yours.\n\n"
    "You can also ask me general questions (\"what is a hash map?\").\n\n"
    "/problem — show the current problem\n"
    "/new — a different problem (say \"something easier\" to steer it)\n"
    "/summarize — recap what you've tried\n"
    "/help — this message"
)


def user_id_for(chat_id: str) -> str:
    """Stable per chat. Display names are cosmetic and never change the scope —
    renaming yourself must not silently orphan your memory."""
    return f"tg{chat_id}"


def ensure_link(channel: str, chat_id: str, sender_name: str = "") -> str:
    link = memory.get_link(channel, chat_id)
    if link:
        return link["user_id"]
    uid = user_id_for(chat_id)
    memory.link_chat(channel, chat_id, uid, sender_name or None)
    log.info("linked %s chat %s as %s", channel, chat_id, uid)
    return uid


def problem_text(sid: str) -> str:
    p = state.current(sid)
    bits = [p.name]
    if p.rating:
        bits.append(f"rating {p.rating}")
    if p.source:
        bits.append(p.source)
    head = " · ".join(bits)
    tail = f"\n\nFull problem: {p.url}" if p.url and p.url.startswith("http") else ""
    return f"{head}\n\n{p.statement.strip()}{tail}"


class Bot:
    def __init__(self, channel: Channel, poll_timeout: int = 25):
        self.channel = channel
        self.poll_timeout = poll_timeout
        self.queue = TurnQueue(self._turn)
        set_channel(channel)          # so the run-complete webhook can reply

    # ------------------------------------------------------------------ a turn

    async def _turn(self, user_id: str, chat_id: str, text: str) -> None:
        """One learner message, start to finish. Runs in the queue's worker."""
        sid = f"u:{user_id}"
        try:
            reply = await asyncio.to_thread(self._sync_turn, sid, user_id, chat_id, text)
        except Exception:
            log.exception("turn blew up for %s", user_id)
            reply = ("Sorry — something went wrong on my side handling that. "
                     "Please try again.")
        if reply:
            await self.channel.send(chat_id, reply)

    def _sync_turn(self, sid: str, user_id: str, chat_id: str, text: str) -> str:
        """The blocking part: routing, LLM calls, job hand-off."""
        lowered = text.strip().lower()

        if lowered in ("/start", "/help"):
            return WELCOME
        if lowered == "/problem":
            return problem_text(sid)
        if lowered in ("/new", "/next"):
            problem = state.load_new(sid)
            return f"New problem.\n\n{problem_text(sid)}"
        if lowered == "/reset":
            state.reset(sid)
            memory.reset(sid)
            state.load_new(sid)
            return "Wiped your progress and loaded a fresh problem. /problem to see it."
        if lowered == "/summarize":
            text = "summarize"

        run_id = uuid.uuid4().hex[:12]
        routed = router.route(text)
        resp = api._handle(sid, user_id, text, run_id, routed=routed,
                           defer={"channel": self.channel.name, "chat_id": str(chat_id)})

        memory.add_message(sid, "user", text, resp.intent,
                           state.current(sid).url or "fallback")
        # a deferred run's assistant message is written when the verdict lands
        if resp.meta.get("verdict") != "QUEUED":
            memory.add_message(sid, "assistant", resp.reply, resp.intent,
                               state.current(sid).url or "fallback")
            memory.log_run(run_id=run_id, user_id=user_id, sid=sid,
                           problem_key=state.current(sid).url or "fallback",
                           problem_name=state.current(sid).name, intent=resp.intent,
                           user_message=text, reply=resp.reply,
                           verdict=resp.meta.get("verdict"),
                           rules_applied=resp.meta.get("rules_applied") or [],
                           facts_used=resp.meta.get("facts_used") or [],
                           notes_seen=resp.meta.get("notes_seen") or [],
                           tools_called=resp.meta.get("tools_called") or [])

        if resp.intent == "new_problem":
            return f"{resp.reply}\n\n{problem_text(sid)}"
        return resp.reply

    # -------------------------------------------------------------- admin path

    async def _admin(self, chat_id: str, text: str) -> bool:
        """Handle a privileged command. Returns True if it was one."""
        lowered = text.strip().lower()
        if lowered.startswith("/tick"):
            dry = "--send" not in lowered
            report = await admin.force_tick(str(chat_id), dry_run=dry)
            suffix = "" if dry else "\n(sent for real)"
            await self.channel.send(chat_id, f"nudge pass{suffix}\n{report}")
            return True
        if lowered.startswith("/admin"):
            body = text.strip()[len("/admin"):].strip()
            if not body:
                await self.channel.send(chat_id, "Usage: /admin <what you want>")
                return True
            reply = await asyncio.to_thread(admin.handle, str(chat_id), body)
            await self.channel.send(chat_id, reply)
            return True
        if lowered.startswith("/queue"):
            snap = self.queue.snapshot()
            await self.channel.send(
                chat_id, "queue depths: " + (str(snap) if snap else "all idle"))
            return True
        return False

    # ------------------------------------------------------------------- loop

    async def handle_update(self, update) -> None:
        if not memory.claim_update(self.channel.name, update.update_id):
            log.info("skipping redelivered update %s", update.update_id)
            return

        chat_id = str(update.chat_id)
        user_id = ensure_link(self.channel.name, chat_id, update.sender_name)

        if admin.is_admin(chat_id) and await self._admin(chat_id, update.text):
            return

        status, ahead = await self.queue.submit(user_id, chat_id, update.text)
        if status == "dropped":
            await self.channel.send(
                chat_id,
                "I've still got a few of your messages waiting and can't hold more "
                "— give me a moment to catch up, then send that again. (Nothing "
                "you already sent has been lost.)")
        elif status == "queued":
            await self.channel.send(
                chat_id,
                f"Got it — I'm still working on your previous message, so this one "
                f"is next in line ({ahead} ahead of it).")

    async def run(self) -> None:
        log.info("bot up on %s", self.channel.name)
        while True:
            for update in await self.channel.updates(self.poll_timeout):
                await self.handle_update(update)
            if isinstance(self.channel, FakeChannel):
                return                     # scripted demo: one drain and out
            await asyncio.sleep(0.2)

    async def close(self) -> None:
        await self.queue.close()
        await self.channel.close()
        set_channel(None)


async def _main(fake: bool) -> None:
    memory.init()
    channel: Channel = FakeChannel(name="telegram") if fake else TelegramChannel()
    bot = Bot(channel)
    if not fake:
        me = await channel.me()          # prove which identity we're acting as
        log.info("acting as @%s (id %s)", me.get("username"), me.get("id"))
    try:
        await bot.run()
        await bot.queue.drain(timeout=300)
    finally:
        await bot.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="NLCP2G chat bot.")
    parser.add_argument("--fake", action="store_true",
                        help="use a fake channel (no token needed)")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    asyncio.run(_main(args.fake))


if __name__ == "__main__":
    main()
