"""Splitting the concept corpus into retrievable pieces.

The corpus is reference documentation, and reference documentation already comes
pre-divided by its author: a `##` section is a claim plus the evidence for it,
written to be read on its own. So the primary boundary is the heading, not a
character count. A fixed-width window over the same text would routinely cut a
table off its header row and an example off the sentence explaining it, and the
resulting chunk retrieves for the wrong queries and reads as nonsense when it
lands in a model's context.

Sections are not all the same size, though, and a 4 kB section retrieved whole
buries the one paragraph that answered the question. So sections longer than
`SECTION_MAX_CHARS` get subdivided — but at *block* boundaries (paragraph, code
fence, table), never mid-block, and with an overlap so a claim split across the
seam is still wholly present in one of the two pieces.

Every chunk carries its heading path in its text. That is not decoration: it is
the only context a chunk has once it is out of its document, and it matters to
both retrievers. The dense model sees what the passage is *about*, and BM25 gets
the heading's terms as searchable tokens — which is most of why an exact-term
query like "lower_bound" finds the section named after it.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, asdict

# A section shorter than this is one chunk. The threshold is set above the size
# of a typical section in this corpus (most run 400-1000 characters) so that the
# common case keeps the author's own boundary and no splitting happens at all.
SECTION_MAX_CHARS = 1200

# When a section does have to be split, aim for pieces around this size. Small
# enough that a retrieved chunk is mostly answer rather than mostly context;
# large enough to hold a code example and the paragraph that explains it.
WINDOW_CHARS = 800

# Carried from the end of one window into the start of the next, at block
# granularity. A claim that straddles a seam then appears whole in the second
# piece rather than half in each.
OVERLAP_CHARS = 150

# Documentation *about* the corpus is not part of it.
SKIP_FILES = {"README.md"}


@dataclass(frozen=True)
class Chunk:
    """One retrievable piece of the corpus."""

    chunk_id: str        # "binary-search#lower_bound-and-upper_bound#0"
    doc: str             # "binary-search" — the file, without .md
    title: str           # the document's H1
    heading: str         # the section's own heading text
    ordinal: int         # which piece of that section this is, from 0
    text: str            # heading path + body, which is what gets embedded

    def to_dict(self) -> dict:
        return asdict(self)


def slug(text: str) -> str:
    """A heading, reduced to something usable inside an id.

    Kept deliberately lossy but stable: lowercase, non-alphanumerics collapsed to
    single hyphens. Two headings in one document that slug identically would
    collide, so `chunk` checks for that and raises rather than silently merging.
    """
    out = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return out or "section"


_FENCE = re.compile(r"^\s*```")
_HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*$")


def _blocks(lines: list[str]) -> list[str]:
    """Body lines -> indivisible blocks.

    A block is a paragraph, a fenced code block, or a run of table rows. These
    are the units that must not be cut: splitting a code fence produces a chunk
    with an unterminated ``` that poisons the markdown of whatever renders it,
    and splitting a table leaves rows with no header.
    """
    out: list[str] = []
    buf: list[str] = []
    in_fence = False

    def flush() -> None:
        if buf:
            block = "\n".join(buf).strip("\n")
            if block.strip():
                out.append(block)
            buf.clear()

    for line in lines:
        if _FENCE.match(line):
            if in_fence:
                buf.append(line)
                in_fence = False
                flush()
            else:
                flush()
                buf.append(line)
                in_fence = True
            continue
        if in_fence:
            buf.append(line)
            continue
        if not line.strip():
            flush()
            continue
        buf.append(line)

    # An unterminated fence is a corpus bug; keep the text rather than lose it.
    flush()
    return out


def _windows(blocks: list[str]) -> list[str]:
    """Pack blocks into windows of roughly WINDOW_CHARS, with overlap.

    Greedy: keep adding blocks until the next one would overflow. A single block
    larger than the target is emitted alone and oversized — deliberately, since
    the alternative is cutting a code fence in half.
    """
    if not blocks:
        return []

    windows: list[list[str]] = []
    current: list[str] = []
    size = 0

    for block in blocks:
        addition = len(block) + (2 if current else 0)
        if current and size + addition > WINDOW_CHARS:
            windows.append(current)
            # Seed the next window with trailing blocks from this one, newest
            # first, while they fit in the overlap budget.
            carry: list[str] = []
            carried = 0
            for prev in reversed(current):
                if carried + len(prev) > OVERLAP_CHARS:
                    break
                carry.insert(0, prev)
                carried += len(prev)
            current = list(carry)
            size = sum(len(b) + 2 for b in current)
            addition = len(block) + (2 if current else 0)
        current.append(block)
        size += addition

    if current:
        windows.append(current)
    return ["\n\n".join(w) for w in windows]


def chunk_document(doc: str, markdown: str) -> list[Chunk]:
    """One document -> its chunks, in reading order."""
    lines = markdown.splitlines()

    title = ""
    sections: list[tuple[str, list[str]]] = []
    heading = ""
    body: list[str] = []
    in_fence = False

    for line in lines:
        if _FENCE.match(line):
            in_fence = not in_fence
            body.append(line)
            continue
        # A '#' inside a fence is a comment, not a heading.
        match = None if in_fence else _HEADING.match(line)
        if match:
            level, text = len(match.group(1)), match.group(2)
            if level == 1 and not title:
                title = text
                continue
            if heading or body:
                sections.append((heading, body))
            heading, body = text, []
            continue
        body.append(line)

    if heading or body:
        sections.append((heading, body))

    chunks: list[Chunk] = []
    seen: set[str] = set()
    for section_heading, section_body in sections:
        blocks = _blocks(section_body)
        if not blocks:
            continue
        total = sum(len(b) + 2 for b in blocks)
        pieces = ["\n\n".join(blocks)] if total <= SECTION_MAX_CHARS else _windows(blocks)

        key = slug(section_heading)
        if key in seen:
            raise ValueError(
                f"{doc}: two headings slug to {key!r}; chunk ids would collide")
        seen.add(key)

        for ordinal, piece in enumerate(pieces):
            header = f"# {title}\n## {section_heading}" if section_heading else f"# {title}"
            chunks.append(Chunk(
                chunk_id=f"{doc}#{key}#{ordinal}",
                doc=doc,
                title=title,
                heading=section_heading,
                ordinal=ordinal,
                text=f"{header}\n\n{piece}",
            ))
    return chunks


def chunk_corpus(root: str) -> list[Chunk]:
    """Every document under `root`, chunked, in a stable order.

    Sorted by filename so the index is reproducible: chunk ids embed no counter
    that depends on directory iteration order, and the vector array's row order
    has to match `chunks.jsonl` exactly.
    """
    chunks: list[Chunk] = []
    for name in sorted(os.listdir(root)):
        if not name.endswith(".md") or name in SKIP_FILES:
            continue
        path = os.path.join(root, name)
        with open(path, encoding="utf-8") as fh:
            chunks.extend(chunk_document(name[:-3], fh.read()))
    return chunks
