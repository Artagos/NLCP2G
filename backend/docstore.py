"""Store 2 of 3: a non-relational (JSON document) store for agent-written memory.

SQLite holds the domain model — problems, attempts, notes — because those are
structured rows we filter and aggregate on. This store holds the things the agent
writes *in its own words*, where no fixed schema fits: a fact it learned about a
learner, a rule a learner asked it to follow. Documents are free-form; only a
handful of fields are load-bearing.

Two document types:

  fact — something learned that would otherwise have to be re-asked
         ("finds recursion confusing", "is doing this for a university course").
         Saved *with a cue* — keywords plus a note about when it should come
         back. The cue is how it gets pulled later. When a fact surfaces, the
         model decides what to do with it.

  rule — something that changes how the agent behaves ("always give me a tiny
         worked example"). Attached on every run for its owner, not retrieved.
         The model does not decide what to do — the rule says what. It decides
         only whether the rule applies here.

Two scopes:

  private — a file per user under memory_store/private/<user>.json. A fact saved
            for alice is never returned for bob. This is enforced here, at the
            store, not by asking a prompt nicely.
  shared  — memory_store/shared.json, visible to everyone's agent.

Layout on disk (deliberately readable — you can cat it during a demo):

  memory_store/
    shared.json
    private/
      alice.json
      bob.json
"""
from __future__ import annotations

import json
import os
import re
import time
import uuid
from threading import Lock

_ROOT = os.environ.get(
    "CP_TUTOR_DOCSTORE",
    os.path.join(os.path.dirname(__file__), "..", "memory_store"),
)

_lock = Lock()

# Words too common to be a useful retrieval cue.
_STOP = {
    "a", "an", "the", "is", "are", "was", "were", "be", "been", "being", "do",
    "does", "did", "of", "to", "in", "on", "at", "for", "with", "and", "or",
    "but", "if", "then", "this", "that", "these", "those", "it", "its", "i",
    "me", "my", "you", "your", "we", "us", "he", "she", "they", "them", "what",
    "how", "why", "when", "which", "who", "can", "could", "should", "would",
    "will", "about", "as", "so", "not", "no", "yes", "please", "thanks",
}


def _safe(user_id: str) -> str:
    """Filename-safe user id — the id also comes from a URL/cookie, so clamp it."""
    slug = re.sub(r"[^a-z0-9_.-]+", "-", (user_id or "guest").strip().lower())
    return slug.strip("-.") or "guest"


def safe_user(user_id: str) -> str:
    """Public alias — the app normalises the cookie value through this."""
    return _safe(user_id)


def _path(scope: str, user_id: str | None) -> str:
    if scope == "shared":
        return os.path.join(_ROOT, "shared.json")
    return os.path.join(_ROOT, "private", f"{_safe(user_id or 'guest')}.json")


def _load(path: str) -> list[dict]:
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError):
        return []
    return data if isinstance(data, list) else []


def _store(path: str, docs: list[dict]) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(docs, fh, indent=2, ensure_ascii=False)
    os.replace(tmp, path)   # atomic, so a crash mid-write can't truncate memory


def _tokens(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9']+", (text or "").lower())
            if len(w) > 2 and w not in _STOP}


# ---------------------------------------------------------------- writing

def save(
    *,
    doc_type: str,
    text: str,
    user_id: str,
    scope: str = "private",
    cue_keywords: list[str] | None = None,
    cue_note: str = "",
    source: str = "chat",
    problem_key: str | None = None,
) -> dict:
    """Write one document. Returns the stored document (with its generated id)."""
    if doc_type not in ("fact", "rule"):
        raise ValueError(f"doc_type must be 'fact' or 'rule', got {doc_type!r}")
    if scope not in ("private", "shared"):
        raise ValueError(f"scope must be 'private' or 'shared', got {scope!r}")

    doc = {
        "id": f"{doc_type[0]}_{uuid.uuid4().hex[:8]}",
        "type": doc_type,
        "scope": scope,
        "user_id": _safe(user_id),
        "text": text.strip(),
        # the cue is what makes a fact retrievable later; rules don't need one
        "cue": {
            "keywords": [k.strip().lower() for k in (cue_keywords or []) if k.strip()],
            "note": cue_note.strip(),
        },
        "source": source,
        "problem_key": problem_key,
        "created_at": time.time(),
        "hits": 0,
        "last_used": None,
    }
    path = _path(scope, user_id)
    with _lock:
        docs = _load(path)
        # don't accumulate near-duplicates of the same fact
        for existing in docs:
            if existing["type"] == doc_type and existing["text"].lower() == doc["text"].lower():
                return existing
        docs.append(doc)
        _store(path, docs)
    return doc


def forget(doc_id: str, user_id: str, scope: str = "private") -> bool:
    path = _path(scope, user_id)
    with _lock:
        docs = _load(path)
        kept = [d for d in docs if d["id"] != doc_id]
        if len(kept) == len(docs):
            return False
        _store(path, kept)
    return True


def clear(user_id: str) -> None:
    """Drop one user's private documents (used by the session reset)."""
    path = _path("private", user_id)
    with _lock:
        if os.path.exists(path):
            os.remove(path)


# ---------------------------------------------------------------- reading

def all_docs(user_id: str, include_shared: bool = True) -> list[dict]:
    """Everything visible to this user: their private docs plus shared ones.

    Another user's private documents are simply not on the read path — there is
    no filter to get wrong.
    """
    docs = list(_load(_path("private", user_id)))
    if include_shared:
        docs += _load(_path("shared", None))
    return docs


def rules_for(user_id: str) -> list[dict]:
    """Rule documents — *pushed* into every run for this user, never retrieved.

    Shared rules apply to everyone; private rules only to their owner.
    """
    return [d for d in all_docs(user_id) if d.get("type") == "rule"]


def retrieve(user_id: str, query: str, limit: int = 4) -> list[dict]:
    """*Pull*: fetch facts whose cue matches the request, mid-run.

    Scoring is deliberately transparent rather than clever — cue keywords are
    worth more than incidental overlap with the fact's own text — so a trace can
    show exactly why a fact surfaced.
    """
    q = _tokens(query)
    if not q:
        return []

    scored: list[tuple[float, dict]] = []
    for doc in all_docs(user_id):
        if doc.get("type") != "fact":
            continue
        cue = doc.get("cue") or {}
        keywords = {k.lower() for k in cue.get("keywords") or []}
        # a multi-word cue keyword matches on the whole phrase too
        kw_hits = sum(
            1 for k in keywords
            if k in q or (" " in k and k in query.lower())
        )
        note_hits = len(q & _tokens(cue.get("note", "")))
        text_hits = len(q & _tokens(doc.get("text", "")))
        score = kw_hits * 3.0 + note_hits * 1.0 + text_hits * 0.5
        if score > 0:
            scored.append((score, doc))

    scored.sort(key=lambda pair: (-pair[0], pair[1]["created_at"]))
    hits = [doc for _, doc in scored[:limit]]
    if hits:
        _mark_used({d["id"] for d in hits}, user_id)
    return hits


def _mark_used(doc_ids: set[str], user_id: str) -> None:
    """Bump usage counters so the monitor can see which memories actually fire."""
    for scope in ("private", "shared"):
        path = _path(scope, user_id)
        with _lock:
            docs = _load(path)
            touched = False
            for doc in docs:
                if doc["id"] in doc_ids:
                    doc["hits"] = doc.get("hits", 0) + 1
                    doc["last_used"] = time.time()
                    touched = True
            if touched:
                _store(path, docs)


# ---------------------------------------------------------------- rendering

def render_rules(docs: list[dict]) -> str:
    """Rule documents, formatted for the push into a system prompt."""
    if not docs:
        return ""
    lines = ["--- LEARNER-SPECIFIC RULES (always in force for this learner) ---"]
    for d in docs:
        scope = "everyone" if d["scope"] == "shared" else "this learner"
        lines.append(f"- [{d['id']}, applies to {scope}] {d['text']}")
    lines.append("--- end learner-specific rules ---")
    return "\n".join(lines)


def render_facts(docs: list[dict]) -> str:
    """Retrieved facts, formatted for injection after a pull."""
    if not docs:
        return ""
    lines = ["--- REMEMBERED ABOUT THIS LEARNER (act on these; do not announce them) ---"]
    for d in docs:
        when = (d.get("cue") or {}).get("note", "")
        lines.append(f"- [{d['id']}] {d['text']}" + (f"  (saved for: {when})" if when else ""))
    lines.append("--- end remembered facts ---")
    return "\n".join(lines)
