"""A JSON file keyed by content hash, for calls that must not be paid for twice.

Two things in this system make a model call whose answer should be stable: the
reranker, and the eval harness's judge. Both are asked the same question every
time the evaluation is re-run, and both would otherwise return slightly
different answers each time — which would show up in the report as metrics that
drift for no reason anyone could point at.

Caching them turns "re-run the evaluation" from an expensive, noisy operation
into a free, exact one. The cache files are committed for the same reason the
embedding cache is: a cold process with no API key must be able to reproduce
every published number.

Deliberately unbounded and never evicted. The keys are content hashes, so a
stale entry is impossible — change the prompt, the model, or the candidates and
you get a different key. The file grows only when a genuinely new question is
asked.
"""
from __future__ import annotations

import hashlib
import json
import os
import threading
from typing import Any


class JsonCache:
    """A dict persisted to one JSON file, loaded lazily and written atomically."""

    def __init__(self, path: str):
        self._path = path
        self._data: dict[str, Any] | None = None
        self._lock = threading.Lock()

    @staticmethod
    def key(*parts: Any) -> str:
        """A stable key from anything JSON-serialisable."""
        material = "␟".join(json.dumps(p, sort_keys=True, default=str)
                                 for p in parts)
        return hashlib.sha256(material.encode("utf-8")).hexdigest()

    def _load(self) -> dict[str, Any]:
        if self._data is None:
            if os.path.exists(self._path):
                with open(self._path, encoding="utf-8") as fh:
                    self._data = json.load(fh)
            else:
                self._data = {}
        return self._data

    def get(self, key: str) -> Any | None:
        with self._lock:
            return self._load().get(key)

    def put(self, key: str, value: Any) -> None:
        with self._lock:
            data = self._load()
            data[key] = value
            os.makedirs(os.path.dirname(self._path) or ".", exist_ok=True)
            tmp = self._path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                # Sorted keys and an indent so the committed file diffs one
                # entry at a time instead of reflowing wholesale.
                json.dump(data, fh, sort_keys=True, indent=1, ensure_ascii=False)
            os.replace(tmp, self._path)

    def __len__(self) -> int:
        with self._lock:
            return len(self._load())
