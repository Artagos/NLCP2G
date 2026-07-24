"""SQLite-backed memory, keyed by session id.

Stores three things per session:
  * attempts   — every solution attempt (problem, approach, verdict, time)
  * seen       — problems shown to the learner (+ whether solved) for adaptive
                 selection and repeat-avoidance
  * messages   — the full chat, so a refresh restores context and the tutor can
                 recall the earlier conversation

GUARDRAIL: nothing here is ever handed to the translator or screener. Only the
tutor (which already sees the statement) and the problem-selection logic read it.
"""
from __future__ import annotations

import os
import sqlite3
import time
from contextlib import contextmanager

_DB = os.environ.get("CP_TUTOR_DB", "cp_tutor.db")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS attempts(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  sid TEXT, problem_key TEXT, problem_name TEXT, rating INTEGER,
  approach TEXT, verdict TEXT, created_at REAL
);
CREATE TABLE IF NOT EXISTS seen(
  sid TEXT, problem_key TEXT, problem_name TEXT, rating INTEGER,
  solved INTEGER DEFAULT 0, created_at REAL,
  PRIMARY KEY (sid, problem_key)
);
CREATE TABLE IF NOT EXISTS messages(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  sid TEXT, role TEXT, content TEXT, intent TEXT, created_at REAL
);
CREATE TABLE IF NOT EXISTS session_current(
  sid TEXT PRIMARY KEY, problem_key TEXT, contest_id INTEGER,
  problem_index TEXT, updated_at REAL
);
"""


@contextmanager
def _conn():
    conn = sqlite3.connect(_DB, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init() -> None:
    with _conn() as c:
        c.executescript(_SCHEMA)


# ---- current problem per session ----

def set_current(sid: str, key: str, name: str, rating: int | None,
                contest_id: int | None, index: str | None) -> None:
    with _conn() as c:
        c.execute(
            "INSERT INTO session_current(sid, problem_key, contest_id, problem_index, updated_at) "
            "VALUES (?,?,?,?,?) ON CONFLICT(sid) DO UPDATE SET "
            "problem_key=excluded.problem_key, contest_id=excluded.contest_id, "
            "problem_index=excluded.problem_index, updated_at=excluded.updated_at",
            (sid, key, contest_id, index, time.time()),
        )
        c.execute(
            "INSERT INTO seen(sid, problem_key, problem_name, rating, solved, created_at) "
            "VALUES (?,?,?,?,0,?) ON CONFLICT(sid, problem_key) DO NOTHING",
            (sid, key, name, rating, time.time()),
        )


def get_current_ref(sid: str) -> tuple[str, int | None, str | None] | None:
    with _conn() as c:
        row = c.execute(
            "SELECT problem_key, contest_id, problem_index FROM session_current WHERE sid=?",
            (sid,),
        ).fetchone()
    return (row["problem_key"], row["contest_id"], row["problem_index"]) if row else None


# ---- attempts & progress ----

def record_attempt(sid: str, key: str, name: str, rating: int | None,
                   approach: str, verdict: str) -> int:
    with _conn() as c:
        c.execute(
            "INSERT INTO attempts(sid, problem_key, problem_name, rating, approach, verdict, created_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (sid, key, name, rating, approach, verdict, time.time()),
        )
        if verdict == "AC":
            c.execute("UPDATE seen SET solved=1 WHERE sid=? AND problem_key=?", (sid, key))
        n = c.execute(
            "SELECT COUNT(*) n FROM attempts WHERE sid=? AND problem_key=?", (sid, key)
        ).fetchone()["n"]
    return n


def attempt_count(sid: str, key: str) -> int:
    with _conn() as c:
        row = c.execute(
            "SELECT COUNT(*) n FROM attempts WHERE sid=? AND problem_key=?", (sid, key)
        ).fetchone()
    return row["n"]


def seen_keys(sid: str) -> set[str]:
    with _conn() as c:
        rows = c.execute("SELECT problem_key FROM seen WHERE sid=?", (sid,)).fetchall()
    return {r["problem_key"] for r in rows}


def solved_ratings(sid: str) -> list[int]:
    with _conn() as c:
        rows = c.execute(
            "SELECT rating FROM seen WHERE sid=? AND solved=1 AND rating IS NOT NULL",
            (sid,),
        ).fetchall()
    return [r["rating"] for r in rows]


def progress(sid: str) -> dict:
    with _conn() as c:
        seen = c.execute("SELECT COUNT(*) n FROM seen WHERE sid=?", (sid,)).fetchone()["n"]
        solved = c.execute("SELECT COUNT(*) n FROM seen WHERE sid=? AND solved=1", (sid,)).fetchone()["n"]
        attempts = c.execute("SELECT COUNT(*) n FROM attempts WHERE sid=?", (sid,)).fetchone()["n"]
        by_verdict = {
            r["verdict"]: r["n"]
            for r in c.execute(
                "SELECT verdict, COUNT(*) n FROM attempts WHERE sid=? GROUP BY verdict", (sid,)
            ).fetchall()
        }
        recent = [
            {"name": r["problem_name"], "verdict": r["verdict"], "at": r["created_at"]}
            for r in c.execute(
                "SELECT problem_name, verdict, created_at FROM attempts WHERE sid=? "
                "ORDER BY id DESC LIMIT 10", (sid,)
            ).fetchall()
        ]
    return {"seen": seen, "solved": solved, "attempts": attempts,
            "by_verdict": by_verdict, "recent": recent}


# ---- conversation ----

def add_message(sid: str, role: str, content: str, intent: str | None) -> None:
    with _conn() as c:
        c.execute(
            "INSERT INTO messages(sid, role, content, intent, created_at) VALUES (?,?,?,?,?)",
            (sid, role, content, intent, time.time()),
        )


def get_messages(sid: str) -> list[dict]:
    with _conn() as c:
        rows = c.execute(
            "SELECT role, content, intent FROM messages WHERE sid=? ORDER BY id", (sid,)
        ).fetchall()
    return [{"role": r["role"], "content": r["content"], "intent": r["intent"]} for r in rows]


def tutor_history(sid: str) -> list[dict]:
    """Prior concept-turn exchanges, for the tutor's context."""
    with _conn() as c:
        rows = c.execute(
            "SELECT role, content FROM messages WHERE sid=? AND intent='concept' ORDER BY id",
            (sid,),
        ).fetchall()
    return [{"role": r["role"], "content": r["content"]} for r in rows]
