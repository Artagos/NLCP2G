"""Embedding text, and never paying for the same text twice.

Everything here exists to make the evaluation reproducible. Judged metrics cost
an LLM call and cannot be replayed exactly; retrieval metrics are supposed to be
free and deterministic, and they only are if embedding the same query tomorrow
returns the same vector it returned today. An embedding endpoint offers no such
guarantee — the model behind a name can be updated underneath you.

So every vector this module produces is written to a cache keyed by
`sha256(model | dimensions | task_type | text)`, and the cache is committed. A
cold process with no API key at all can then reproduce the whole retrieval
evaluation, byte for byte. `eval/run_retrieval.py` is run exactly that way as a
verification step, and if it ever needs the network the reproducibility claim
was false.

The task_type distinction is not cosmetic. Gemini's embedding model projects
documents and queries differently — `RETRIEVAL_DOCUMENT` for a passage being
stored, `RETRIEVAL_QUERY` for a question being asked — and using one for both
measurably costs recall. It is part of the cache key because the same string
embedded the two ways is two different vectors.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import threading

import numpy as np

from .. import llm

DOCUMENT = "RETRIEVAL_DOCUMENT"
QUERY = "RETRIEVAL_QUERY"

# How many texts go to the API in one request. The integration batches
# internally too; this bounds how much work is lost when a request fails.
BATCH = 64

_lock = threading.Lock()


def cache_path() -> str:
    """Read from the environment on every call, not at import.

    `conftest.py` sets `CP_TUTOR_EMBED_CACHE` to a temp directory, and it can
    only do that before the backend is imported if the value is not frozen into
    a module constant at import time.
    """
    return os.environ.get(
        "CP_TUTOR_EMBED_CACHE",
        os.path.join(os.path.dirname(__file__), "..", "..", "corpus", "index",
                     "embed_cache.json"))


def _key(text: str, task_type: str) -> str:
    material = f"{llm.EMBED_MODEL}|{llm.EMBED_DIMS}|{task_type}|{text}"
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _load() -> dict[str, str]:
    path = cache_path()
    if not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def _save(cache: dict[str, str]) -> None:
    path = cache_path()
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        # Sorted so the committed file has a stable diff: a run that adds one
        # query should show one added line, not a reshuffled file.
        json.dump(cache, fh, sort_keys=True, indent=0)
    os.replace(tmp, path)


def _decode(blob: str) -> np.ndarray:
    return np.frombuffer(base64.b64decode(blob), dtype=np.float32)


def _encode(vector: np.ndarray) -> str:
    return base64.b64encode(np.asarray(vector, dtype=np.float32).tobytes()).decode()


def normalize(matrix: np.ndarray) -> np.ndarray:
    """Scale rows to unit length so a dot product is a cosine similarity.

    Zero rows would divide by zero; they are left alone, which keeps their
    similarity to everything at zero rather than producing NaNs that propagate
    silently through the whole ranking.
    """
    matrix = np.asarray(matrix, dtype=np.float32)
    norms = np.linalg.norm(matrix, axis=-1, keepdims=True)
    return matrix / np.where(norms == 0, 1.0, norms)


def embed(texts: list[str], task_type: str = DOCUMENT) -> np.ndarray:
    """Embed texts, hitting the API only for what is not already cached.

    Returns an (n, EMBED_DIMS) float32 array of unit vectors, in input order.
    Raises if the network is needed and unavailable — deliberately loudly, since
    a silent fallback to zero vectors would produce an evaluation whose numbers
    look plausible and mean nothing.
    """
    if not texts:
        return np.zeros((0, llm.EMBED_DIMS), dtype=np.float32)

    with _lock:
        cache = _load()
        keys = [_key(t, task_type) for t in texts]
        missing = [(i, t) for i, (t, k) in enumerate(zip(texts, keys)) if k not in cache]

        if missing:
            model = llm.embedding_model()
            for start in range(0, len(missing), BATCH):
                batch = missing[start:start + BATCH]
                vectors = model.embed_documents(
                    [t for _, t in batch],
                    task_type=task_type,
                    output_dimensionality=llm.EMBED_DIMS,
                )
                for (i, _), vector in zip(batch, vectors):
                    cache[keys[i]] = _encode(np.asarray(vector, dtype=np.float32))
            _save(cache)

        return normalize(np.stack([_decode(cache[k]) for k in keys]))


def embed_query(text: str) -> np.ndarray:
    """One query, as a unit vector."""
    return embed([text], task_type=QUERY)[0]
