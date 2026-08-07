"""Four defence layers, and the reason each one is where it is.

This project already had the *policy*. R7 says notes written by other learners
are data and never instructions. R8 says one learner's private memory is never
shown to another. R9 says a shared note does not become a licence to hint. What
it had by way of *mechanism* was a single function — `memory.render_notes` —
that fenced notes and neutralised one token. Everything else was the model being
asked nicely.

A rule the model is asked to follow is a rule the next prompt can argue with.
These layers are the parts that do not depend on the model agreeing.

    Layer 1  input filtering        notice the attempt, and record it
    Layer 2  structural separation  make untrusted text unmistakably data
    Layer 3  output filtering       check the answer before it ships
    Layer 4  capability constraints make the dangerous thing impossible

**Layer 1 flags; it does not block.** That is deliberate and it is the opposite
of what "input filtering" usually means. R7 tells the tutor to *quote* an
attempted instruction and carry on — refusing to store the note, or stripping
the text, would destroy the evidence and break a behaviour the system already
demonstrates in `traces/02-planted-comment.md`. A filter that silently rewrites
user text also makes every downstream measurement a measurement of the filter.
So the pattern list marks content as suspicious, the fencing contains it, and
the detector can later ask whether the model actually obeyed. The one thing
Layer 1 does refuse outright is a note longer than `MAX_NOTE_CHARS`, because a
50 kB note is a context-flooding attack and there is no legitimate version of it.

**Layer 3 is where the real guarantees are**, because it checks what happened
rather than what was asked. A citation that names a passage nobody retrieved is
a fabrication, whatever the prompt said. Text shared with the problem statement
is a leak, whatever the model intended.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

# A note big enough to bury the system prompt is an attack, not a note. The
# longest legitimate note in the eval set is a couple of hundred characters.
MAX_NOTE_CHARS = 2000

# Layer 2's markers, and what each becomes inside a body.
#
# Targeted replacement rather than escaping every `<` and `>`: corpus passages
# are full of `vector<int>` and `#include <vector>`, and HTML-escaping those
# would mangle the reference material the tutor is supposed to read out. The
# angle-bracket forms become entities (which is what `render_notes` already did
# for the closing tag, and what `tests/test_notes.py` pins); the `--- x ---`
# forms get their first hyphen turned into an entity, which no longer matches
# the literal fence while staying obvious to a human reading the transcript.
NEUTRALISED: dict[str, str] = {
    "</untrusted-note>": "&lt;/untrusted-note&gt;",
    "<untrusted-note": "&lt;untrusted-note",
    "--- end notes ---": "&#45;-- end notes ---",
    "--- end passages ---": "&#45;-- end passages ---",
    "--- end problem statement ---": "&#45;-- end problem statement ---",
}
FENCE_TOKENS = tuple(NEUTRALISED)


@dataclass(frozen=True)
class Finding:
    """One thing worth noticing. `severity` is `info`, `warn` or `alert`."""

    check: str
    severity: str
    detail: str

    def __str__(self) -> str:
        return f"[{self.severity}] {self.check}: {self.detail}"


# --------------------------------------------------- Layer 1: input filtering

# Deliberately narrow. Each pattern describes an *imperative aimed at the
# assistant*, not a topic. "How does a hash map ignore collisions" must not
# match, and neither must a learner quoting an error message. Breadth here is
# paid for directly in the false-positive rate reported in the README, so the
# list is short and every entry earns its place.
_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("override-instructions", re.compile(
        r"\b(ignore|disregard|forget|override)\b[^.\n]{0,40}?"
        r"\b(previous|prior|above|earlier|your|all)\b[^.\n]{0,20}?"
        r"\b(instruction|instructions|rules|prompt|directive)s?\b", re.I)),
    ("reveal-system", re.compile(
        r"\b(reveal|show|print|repeat|output|dump|display)\b[^.\n]{0,30}?"
        r"\b(system prompt|your prompt|your instructions|the source code|"
        r"your rules|the flag)\b", re.I)),
    ("identity-swap", re.compile(
        r"\b(you are now|from now on you|act as|pretend to be|"
        r"roleplay as|new persona)\b", re.I)),
    ("cross-learner", re.compile(
        r"\b(other|another|previous|different)\s+(learner|learners|user|users|"
        r"student|students)\b[^.\n]{0,40}?"
        r"\b(data|memory|memories|facts?|progress|notes?|answers?|solution)\b",
        re.I)),
    ("exfiltrate", re.compile(
        r"\b(send|post|upload|forward|email|leak|exfiltrate)\b[^.\n]{0,40}?"
        r"(https?://|\bto my\b|\bwebhook\b|\bendpoint\b)", re.I)),
]


def injection_patterns(text: str) -> list[Finding]:
    """Injection-shaped phrasing only. No opinion about length.

    Split out from `screen_input` because the length cap is a rule about *notes
    at the door*, not a property of untrusted text in general. Scanning a
    retrieved search result with the cap applied flagged every ordinary concept
    question in the eval set — five passages of reference material run to about
    3.5 kB, so `oversized` fired on 10 of 39 perfectly innocent turns and put
    the detector's false-positive rate at 0.256. The patterns are the part that
    generalises; the cap is not.
    """
    body = text or ""
    return [Finding(name, "warn", f"matched {name!r}: {m.group(0)[:80]!r}")
            for name, pattern in _PATTERNS
            if (m := pattern.search(body))]


def screen_input(text: str) -> list[Finding]:
    """Notice injection-shaped content at an ingress point. Never rewrites.

    Patterns plus the length cap. Use this on something a user just submitted;
    use `injection_patterns` on anything else.
    """
    found = injection_patterns(text)
    if too_long(text):
        found.append(Finding("oversized", "alert",
                             f"{len(text or '')} characters, "
                             f"limit {MAX_NOTE_CHARS}"))
    return found


def looks_like_injection(text: str) -> bool:
    return any(f.severity in ("warn", "alert") for f in screen_input(text))


def too_long(text: str) -> bool:
    return len(text or "") > MAX_NOTE_CHARS


# ---------------------------------------------- Layer 2: structural separation

def neutralise(body: str) -> str:
    """Defuse any fence terminator the body contains.

    Without this a note ending in `</untrusted-note>` closes its own block and
    everything after it reads as the system's own words. `memory.render_notes`
    already did this for one token; the corpus and the problem statement have
    their own terminators and had none.
    """
    out = body or ""
    for token, replacement in NEUTRALISED.items():
        out = out.replace(token, replacement)
    return out


def fence(label: str, body: str, *, note: str = "") -> str:
    """Wrap untrusted text so its boundaries survive contact with its contents."""
    header = f"--- {label} ---"
    footer = f"--- end {label} ---"
    parts = [header]
    if note:
        parts.append(note)
    parts.append(neutralise(body))
    parts.append(footer)
    return "\n".join(parts)


# ------------------------------------------------- Layer 3: output filtering

_CITATION = re.compile(r"\[([a-z0-9][a-z0-9\-]*#[a-z0-9\-]*#\d+)\]", re.I)
_URL = re.compile(r"https?://[^\s)\]]+", re.I)
_STOP = frozenset("""a an the and or but if then than that this those these of to in on
for with is are was were be been it its as at by from you your they them their we our
i me my not no do does did can could should would will shall may might must have has
had there here what which who whom when where why how all any both each few more most
other some such only own same so too very just""".split())


def cited_chunks(reply: str) -> set[str]:
    """Chunk ids the reply quoted as `[doc#heading#n]`."""
    return {m.group(1) for m in _CITATION.finditer(reply or "")}


def uncited_fabrications(reply: str, retrieved: list[str] | set[str]) -> set[str]:
    """Citations naming a passage that was never retrieved.

    The cheapest hallucination check there is, and it needs no judge: either the
    id came back from the retriever on this turn or the model invented it.
    """
    return cited_chunks(reply) - set(retrieved or ())


def _shingles(text: str, size: int) -> set[tuple[str, ...]]:
    words = [w for w in re.findall(r"[a-z0-9_]+", (text or "").lower())
             if w not in _STOP]
    return {tuple(words[i:i + size]) for i in range(len(words) - size + 1)}


def statement_overlap(reply: str, statement: str, size: int = 6) -> list[str]:
    """Runs of words the reply shares with the problem statement.

    Stop words are dropped first, so "the number of pairs of positions" is
    compared on `number pairs positions` — which means an incidental "you can
    use the" match cannot trigger it, and a genuine lift of the problem's
    wording still can. Six content words is long enough that ordinary English
    does not collide by accident.
    """
    if not statement:
        return []
    shared = _shingles(reply, size) & _shingles(statement, size)
    return sorted(" ".join(s) for s in shared)


def leaked_identifiers(reply: str, allowed: set[str] | None = None) -> set[str]:
    """Document ids in the reply that this turn never legitimately touched.

    `docstore` ids look like `f_1a2b3c4d` / `r_1a2b3c4d`. A reply containing one
    at all is odd — they are internal handles, never something a learner asked
    about — and one belonging to another learner is R8 broken.
    """
    ids = set(re.findall(r"\b[fr]_[0-9a-f]{8}\b", reply or ""))
    return ids - set(allowed or ())


def outbound_urls(reply: str) -> list[str]:
    """URLs in a reply. The tutor has no reason to emit one with a payload."""
    return _URL.findall(reply or "")


# ---------------------------------------------- Layer 4: capability constraints

def clamp_k(value, low: int = 1, high: int = 10) -> int:
    """A model-supplied count, forced into range.

    Layer 4 is the layer with no prompt in it. `search_corpus` already clamps
    `k`; this puts the clamp somewhere it can be tested as a security property
    rather than as an implementation detail of one tool.
    """
    try:
        number = int(value)
    except (TypeError, ValueError):
        return low
    return max(low, min(number, high))
