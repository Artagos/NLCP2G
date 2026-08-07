"""BM25 over the corpus, and the tokenizer that decides what it can find.

The tokenizer is the interesting half. A generic word splitter destroys exactly
the terms this corpus is searched by: it turns `lower_bound` into two words,
`std::sort` into two more, and `1e9+7` into `1e9` and `7`. Those are the
*highest-signal* tokens available — a learner typing `lower_bound` means that
identifier and nothing else — so preserving them is most of why the lexical arm
earns its place next to the dense one.

The rule here is emit-both: a compound token is indexed whole *and* split into
its parts. `lower_bound` produces `lower_bound`, `lower`, `bound`. That way the
exact-term query matches on the rare compound (high IDF, strong signal) and the
natural-language query "how do I find the lower bound" still matches on the
parts. Choosing one or the other would give up one of those two queries.

BM25 parameters are the standard k1=1.5, b=0.75. They are left alone
deliberately: b controls how hard length normalisation bites, and these chunks
are already length-normalised by construction (the chunker targets a size), so
there is nothing here that the defaults get wrong. Tuning them against the eval
set would be fitting the retriever to the test.
"""
from __future__ import annotations

import re

from rank_bm25 import BM25Okapi

from .chunker import Chunk

# Order matters — the specific patterns must win before the general one gets a
# chance to bite a compound in half.
#   1e9+7, 2^31         numeric literals written the way statements write them
#   std::sort           namespace-qualified identifiers
#   O(n log n)          complexity expressions, normalised to o-n-log-n
_NUMERIC = re.compile(r"\d+e\d+\s*\+\s*\d+|\d+\s*\^\s*\d+")
_QUALIFIED = re.compile(r"[a-z_][a-z0-9_]*(?:::[a-z_][a-z0-9_]*)+")
_BIG_O = re.compile(r"\bo\s*\(\s*([a-z0-9\s^*/+.-]+?)\s*\)")
_WORD = re.compile(r"[a-z_][a-z0-9_]*|\d+")


def tokenize(text: str) -> list[str]:
    """Text -> BM25 terms, compounds preserved and also split."""
    lowered = text.lower()
    tokens: list[str] = []

    def add_compound(token: str, parts: list[str]) -> None:
        tokens.append(token)
        tokens.extend(p for p in parts if p)

    for match in _NUMERIC.finditer(lowered):
        tokens.append(re.sub(r"\s+", "", match.group(0)))

    for match in _BIG_O.finditer(lowered):
        inner = re.sub(r"\s+", "-", match.group(1).strip())
        tokens.append(f"o({inner})")

    for match in _QUALIFIED.finditer(lowered):
        token = match.group(0)
        add_compound(token, token.split("::"))

    # The general pass runs over text with the qualified names already blanked,
    # so `std::sort` is not counted a second time as two bare words.
    remaining = _QUALIFIED.sub(" ", lowered)
    for match in _WORD.finditer(remaining):
        token = match.group(0)
        if "_" in token:
            add_compound(token, token.split("_"))
        else:
            tokens.append(token)

    return tokens


class Lexical:
    """A BM25 index over chunk texts, returning (chunk_id, score) in rank order."""

    def __init__(self, chunks: list[Chunk]):
        self._ids = [c.chunk_id for c in chunks]
        self._bm25 = BM25Okapi([tokenize(c.text) for c in chunks])

    def search(self, query: str, n: int) -> list[tuple[str, float]]:
        terms = tokenize(query)
        if not terms:
            return []
        scores = self._bm25.get_scores(terms)
        order = sorted(range(len(scores)), key=lambda i: -scores[i])[:n]
        # A zero score means no query term appears at all. Returning those pads
        # the candidate list with documents the lexical arm has no opinion about,
        # and RRF would then award them rank credit they did not earn.
        return [(self._ids[i], float(scores[i])) for i in order if scores[i] > 0]
