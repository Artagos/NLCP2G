"""Durable graph state: a conversation survives a restart.

The turn graph is compiled with a `SqliteSaver` writing into the same file the
rest of the system already uses (`CP_TUTOR_DB`). LangGraph creates its own
tables alongside ours, so there is one state file, one Docker volume, and
nothing extra to mount or back up.

The thread id is the session id — `u:<learner>` — which is already how memory is
scoped everywhere else. So the identity cookie that decides whose facts you can
see also decides which conversation you resume, and switching learner in one
browser switches both together. Getting a second, independent notion of "which
conversation is this" would have been the easy mistake here.

Two memories, deliberately distinct, and it is worth being clear about which is
which:

  * **this checkpointer** holds what the *model* sees — the running conversation
    the tutor is given as context, trimmed to a bounded window.
  * **the `messages` table** in memory.py holds what the *human* sees — the UI's
    restore on reload, and the audit record the monitor grades. It is never
    trimmed, because an audit trail with the inconvenient parts dropped is not
    an audit trail.

They are written from the same turn and can be compared, which is the point: if
the agent ever answers from something that is not in the record, the record is
where you find out.
"""
from __future__ import annotations

import os
import sqlite3
from threading import Lock

from langgraph.checkpoint.sqlite import SqliteSaver

# How many messages of conversation the tutor is given. Checkpoints are written
# on every superstep, so an untrimmed thread would grow the write cost of every
# turn for the whole life of a learner's account.
HISTORY_WINDOW = 20

_saver: SqliteSaver | None = None
_lock = Lock()


def _db_path() -> str:
    return os.environ.get(
        "CP_TUTOR_DB",
        os.path.join(os.path.dirname(__file__), "..", "..", "cp_tutor.db"))


def saver() -> SqliteSaver:
    """The process-wide checkpointer.

    `check_same_thread=False` because this connection is genuinely used from
    several threads: FastAPI runs sync endpoints in a threadpool, and the bot
    hands each turn to `asyncio.to_thread`. SQLite serialises the writes itself.
    """
    global _saver
    with _lock:
        if _saver is None:
            path = _db_path()
            parent = os.path.dirname(os.path.abspath(path))
            if parent:
                os.makedirs(parent, exist_ok=True)
            conn = sqlite3.connect(path, check_same_thread=False)
            _saver = SqliteSaver(conn)
            _saver.setup()
        return _saver


def reset() -> None:
    """Drop the cached saver so the next call reopens the current CP_TUTOR_DB.

    Only tests need this — they point the env at a fresh file per run, and a
    connection held open against a deleted database is a confusing way to fail.
    """
    global _saver
    with _lock:
        if _saver is not None:
            try:
                _saver.conn.close()
            except Exception:               # pragma: no cover — already closed
                pass
        _saver = None


def thread(sid: str) -> dict:
    """The config that selects one learner's conversation."""
    return {"configurable": {"thread_id": sid}}
