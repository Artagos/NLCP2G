"""Where the API process finds a channel to write to.

The run-complete webhook has to push a verdict into a chat, but it is an HTTP
handler — it has no conversation to reply to. So the bot registers its channel
here on startup, and the webhook looks it up.

A settable module-level slot rather than a global constructed at import: tests
install a `FakeChannel` and assert on what the webhook sent, which is the only
way to test the interactive trigger without a live bot.
"""
from __future__ import annotations

from .channels import Channel

_channel: Channel | None = None


def set_channel(channel: Channel | None) -> None:
    global _channel
    _channel = channel


def get_channel() -> Channel | None:
    return _channel
