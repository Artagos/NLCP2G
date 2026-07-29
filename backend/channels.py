"""The chat surface, behind one interface.

Everything above this file — the queue, the triggers, the silence branch, the
admin path — talks to `Channel` and never to Telegram. That is not architectural
tidiness for its own sake: a trigger that only fires on a real schedule, into a
real chat, over a real network, cannot be tested. `FakeChannel` records what
would have been sent, which is what lets the test suite assert that the agent
said a particular thing — or, more importantly, that it deliberately said
nothing.

Telegram is reached by **long polling**, not a webhook, so the bot needs no
public URL and runs behind any home NAT. (The one webhook in this system is the
sandbox run-complete callback, which is internal.)
"""
from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass, field

import httpx

log = logging.getLogger("cp_tutor.channels")

# Telegram rejects anything over 4096 characters; leave room for a suffix.
MAX_CHARS = 3900


@dataclass
class Update:
    """One inbound message, normalised across channels."""

    update_id: str
    chat_id: str
    text: str
    sender_name: str = ""


def split_message(text: str, limit: int = MAX_CHARS) -> list[str]:
    """Chunk a long reply on paragraph, then line, then hard boundaries.

    Problem statements and verdict explanations routinely exceed the limit, and a
    silently truncated reply is worse than two messages.
    """
    text = text.strip()
    if len(text) <= limit:
        return [text] if text else []

    chunks: list[str] = []
    remaining = text
    while len(remaining) > limit:
        window = remaining[:limit]
        cut = window.rfind("\n\n")
        if cut < limit // 2:
            cut = window.rfind("\n")
        if cut < limit // 2:
            cut = window.rfind(" ")
        if cut <= 0:
            cut = limit
        chunks.append(remaining[:cut].rstrip())
        remaining = remaining[cut:].lstrip()
    if remaining:
        chunks.append(remaining)
    return chunks


class Channel:
    """Minimal surface: read messages, write messages."""

    name = "channel"

    async def send(self, chat_id: str, text: str) -> None:
        raise NotImplementedError

    async def updates(self, timeout: int = 25) -> list[Update]:
        raise NotImplementedError

    async def close(self) -> None:
        return None


# --------------------------------------------------------------------- testing

@dataclass
class FakeChannel(Channel):
    """Records outbound messages; tests feed it inbound ones.

    `sent` is the assertion surface. An empty `sent` is how a test proves the
    silence branch actually stayed silent.
    """

    name: str = "fake"
    sent: list[tuple[str, str]] = field(default_factory=list)
    inbox: list[Update] = field(default_factory=list)
    fail_on: set[str] = field(default_factory=set)   # chat_ids that raise on send

    async def send(self, chat_id: str, text: str) -> None:
        if chat_id in self.fail_on:
            raise RuntimeError(f"fake channel: delivery to {chat_id} refused")
        for chunk in split_message(text):
            self.sent.append((str(chat_id), chunk))

    async def updates(self, timeout: int = 25) -> list[Update]:
        batch, self.inbox = self.inbox, []
        return batch

    # convenience for tests
    def feed(self, chat_id: str, text: str, update_id: str | None = None) -> None:
        self.inbox.append(Update(
            update_id=update_id or str(len(self.inbox) + 1),
            chat_id=str(chat_id), text=text))

    def texts_to(self, chat_id: str) -> list[str]:
        return [t for c, t in self.sent if c == str(chat_id)]

    @property
    def said_nothing(self) -> bool:
        return not self.sent


# -------------------------------------------------------------------- telegram

def _silence_http_client_logging() -> None:
    """Stop httpx logging the token.

    The Telegram Bot API puts the credential in the *path*
    (`api.telegram.org/bot<TOKEN>/getMe`), and httpx logs every request at INFO
    as "HTTP Request: POST <full url>". Every entrypoint here calls
    `logging.basicConfig(level=INFO)`, so the bot token was printed on the first
    API call and on every call after it, straight into whatever collects stdout.

    Nothing in this module logged the token. The HTTP client underneath it did,
    which is the same thing from the outside. The guard belongs here rather than
    in each CLI because constructing a TelegramChannel is the moment the risk
    starts, whoever constructed it.
    """
    for name in ("httpx", "httpcore"):
        logging.getLogger(name).setLevel(logging.WARNING)


class TelegramChannel(Channel):
    """Telegram Bot API over long polling.

    The token belongs to a bot created with @BotFather — a disposable identity,
    never a personal account. It is read from the environment, and neither this
    class nor the HTTP client beneath it ever writes it to a log.
    """

    name = "telegram"

    def __init__(self, token: str | None = None, timeout: int = 30):
        _silence_http_client_logging()
        self.token = token or os.environ.get("TELEGRAM_BOT_TOKEN", "")
        if not self.token:
            raise RuntimeError(
                "TELEGRAM_BOT_TOKEN is not set. Create a bot with @BotFather and "
                "put the token in .env (which is gitignored)."
            )
        self._base = f"https://api.telegram.org/bot{self.token}"
        self._offset: int | None = None
        self._client = httpx.AsyncClient(timeout=timeout + 10)

    async def _call(self, method: str, **params):
        resp = await self._client.post(f"{self._base}/{method}", json=params)
        if resp.status_code == 429:      # flood control; honour the retry hint
            wait = (resp.json().get("parameters") or {}).get("retry_after", 3)
            log.warning("telegram rate-limited, sleeping %ss", wait)
            await asyncio.sleep(wait)
            resp = await self._client.post(f"{self._base}/{method}", json=params)
        payload = resp.json()
        if not payload.get("ok"):
            raise RuntimeError(f"telegram {method} failed: {payload.get('description')}")
        return payload["result"]

    async def send(self, chat_id: str, text: str) -> None:
        # Plain text on purpose: statements and compiler output are full of
        # characters that MarkdownV2 would reject, and a rejected message is a
        # dropped reply.
        for chunk in split_message(text):
            try:
                await self._call("sendMessage", chat_id=chat_id, text=chunk,
                                 disable_web_page_preview=True)
            except Exception as exc:
                # a learner blocking the bot must not take the process down
                log.warning("send to %s failed: %s", chat_id, exc)
                return

    async def updates(self, timeout: int = 25) -> list[Update]:
        params = {"timeout": timeout, "allowed_updates": ["message"]}
        if self._offset is not None:
            params["offset"] = self._offset
        try:
            raw = await self._call("getUpdates", **params)
        except Exception as exc:
            log.warning("getUpdates failed: %s", exc)
            await asyncio.sleep(3)
            return []

        out: list[Update] = []
        for item in raw:
            self._offset = item["update_id"] + 1
            message = item.get("message") or {}
            text = (message.get("text") or "").strip()
            chat = message.get("chat") or {}
            if not text or not chat.get("id"):
                continue                 # stickers, photos, joins — nothing to answer
            sender = message.get("from") or {}
            out.append(Update(
                update_id=str(item["update_id"]),
                chat_id=str(chat["id"]),
                text=text,
                sender_name=(sender.get("username") or sender.get("first_name") or ""),
            ))
        return out

    async def me(self) -> dict:
        """Identity check — used by the smoke test to prove which bot we are."""
        return await self._call("getMe")

    async def close(self) -> None:
        await self._client.aclose()
