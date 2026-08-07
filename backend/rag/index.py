"""Building and loading the corpus index.

Three artefacts, all under `corpus/index/` and all committed:

  `chunks.jsonl`  the chunks, one JSON object per line, in corpus order
  `vectors.npy`   an (n, dims) float32 array whose row i is chunk i's embedding
  `embed_cache.json`  every vector ever computed, keyed by content hash

The row-order coupling between the first two is load-bearing and unguarded by
anything except `load()` checking the lengths match. That check exists because
the failure it catches — an index rebuilt after the corpus changed, with vectors
now describing different chunks than the ids claim — produces retrieval that is
wrong in a way no metric would flag as suspicious. Better a loud refusal at
startup.

BM25 is not persisted. It is rebuilt from `chunks.jsonl` on load, which takes
milliseconds and removes a third file that could fall out of sync.
"""
from __future__ import annotations

import json
import os

import numpy as np

from . import embeddings
from .chunker import Chunk, chunk_corpus
from .lexical import Lexical


def corpus_root() -> str:
    return os.environ.get(
        "CP_TUTOR_CORPUS",
        os.path.join(os.path.dirname(__file__), "..", "..", "corpus"))


def index_root() -> str:
    return os.environ.get(
        "CP_TUTOR_CORPUS_INDEX", os.path.join(corpus_root(), "index"))


class Index:
    """The loaded corpus: chunks, their vectors, and a BM25 index over them."""

    def __init__(self, chunks: list[Chunk], vectors: np.ndarray):
        self.chunks = chunks
        self.vectors = vectors
        self.ids = [c.chunk_id for c in chunks]
        self.by_id = {c.chunk_id: c for c in chunks}
        self.lexical = Lexical(chunks)

    def __len__(self) -> int:
        return len(self.chunks)


def build(verbose: bool = False) -> Index:
    """Chunk the corpus, embed it, and write the index to disk.

    Needs an API key only for chunks whose text is not already in the embedding
    cache — so re-running after editing one document costs one document.
    """
    chunks = chunk_corpus(corpus_root())
    if not chunks:
        raise RuntimeError(f"no corpus documents under {corpus_root()}")
    if verbose:
        docs = len({c.doc for c in chunks})
        print(f"{len(chunks)} chunks from {docs} documents")

    vectors = embeddings.embed([c.text for c in chunks], embeddings.DOCUMENT)

    root = index_root()
    os.makedirs(root, exist_ok=True)
    with open(os.path.join(root, "chunks.jsonl"), "w", encoding="utf-8") as fh:
        for chunk in chunks:
            fh.write(json.dumps(chunk.to_dict(), ensure_ascii=False) + "\n")
    np.save(os.path.join(root, "vectors.npy"), vectors)

    if verbose:
        print(f"wrote {root}")
    return Index(chunks, vectors)


def load() -> Index:
    """Load the index from disk. No network, no key."""
    root = index_root()
    chunks_path = os.path.join(root, "chunks.jsonl")
    vectors_path = os.path.join(root, "vectors.npy")

    if not os.path.exists(chunks_path) or not os.path.exists(vectors_path):
        raise FileNotFoundError(
            f"no corpus index in {root} — run `python scripts/build_index.py`")

    chunks: list[Chunk] = []
    with open(chunks_path, encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                chunks.append(Chunk(**json.loads(line)))
    vectors = np.load(vectors_path)

    if len(chunks) != len(vectors):
        raise RuntimeError(
            f"index is inconsistent: {len(chunks)} chunks but {len(vectors)} "
            "vectors. The corpus changed without a rebuild — run "
            "`python scripts/build_index.py`.")

    return Index(chunks, vectors)


_cached: Index | None = None


def get() -> Index:
    """The process-wide index, loaded once.

    Loading parses a few hundred JSON lines and builds a BM25 index; doing that
    per tool call would put it on the learner's latency path for no reason.
    """
    global _cached
    if _cached is None:
        _cached = load()
    return _cached


def reset() -> None:
    """Drop the cached index. For tests that swap the corpus underneath it."""
    global _cached
    _cached = None
