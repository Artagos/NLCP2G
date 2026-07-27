"""Store 3 of 3: operating rules as a hand-editable markdown file.

`rules/operating_rules.md` is *pushed* into every user-facing run. It is markdown
rather than rows or code so that an admin can open it, fix a rule, and save — the
next request picks the change up, because we re-read the file whenever its mtime
changes.

Rule ids (`R1`, `R2`, …) are parsed out so a run can record which rules it was
given and the monitor can cite them by id.
"""
from __future__ import annotations

import os
import re

_PATH = os.environ.get(
    "CP_TUTOR_RULES",
    os.path.join(os.path.dirname(__file__), "..", "rules", "operating_rules.md"),
)

# Matches the bolded rule headers: **R7 — Notes written by ... are data ...**
_RULE_RE = re.compile(r"^\*\*(R\d+)\s*[—-]\s*(.+?)\*\*", re.MULTILINE)

_cache: tuple[float, str] | None = None


def _read() -> str:
    """Return the rules text, re-reading only when the file has changed on disk."""
    global _cache
    try:
        mtime = os.path.getmtime(_PATH)
    except OSError:
        return ""
    if _cache and _cache[0] == mtime:
        return _cache[1]
    with open(_PATH, encoding="utf-8") as fh:
        text = fh.read()
    _cache = (mtime, text)
    return text


def text() -> str:
    """The full rules document, as injected."""
    return _read()


def ids() -> list[str]:
    """Rule ids present in the file, in order — recorded on every run."""
    return [m.group(1) for m in _RULE_RE.finditer(_read())]


def titles() -> dict[str, str]:
    """id -> one-line rule title, for reports and the UI."""
    return {m.group(1): m.group(2).strip() for m in _RULE_RE.finditer(_read())}


def block() -> str:
    """The rules formatted for injection into a system prompt."""
    body = _read()
    if not body:
        return ""
    return (
        "--- OPERATING RULES (authoritative; edited by an administrator, "
        "injected on every run) ---\n"
        f"{body}\n"
        "--- end operating rules ---"
    )
