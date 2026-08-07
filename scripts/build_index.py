"""Build the corpus index: chunk every document, embed it, write it to disk.

    python scripts/build_index.py            # build, reusing cached embeddings
    python scripts/build_index.py --stats    # build and describe what came out
    python scripts/build_index.py --dry-run  # chunk only, no API key needed

Needs GEMINI_API_KEY, but only for chunks whose text is not already in
`corpus/index/embed_cache.json`. Editing one document therefore costs that
document's embeddings and nothing else; a fresh clone with the committed cache
costs nothing at all.

The three artefacts it writes — `chunks.jsonl`, `vectors.npy`, `embed_cache.json`
— are committed on purpose. They are what lets the evaluation reproduce offline,
which is checked as a verification step rather than assumed.
"""
from __future__ import annotations

import argparse
import os
import sys
from collections import Counter

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from dotenv import load_dotenv  # noqa: E402

load_dotenv()

from backend.rag import chunker, index as index_module  # noqa: E402


def _stats(chunks) -> None:
    per_doc = Counter(c.doc for c in chunks)
    lengths = sorted(len(c.text) for c in chunks)
    split = sum(1 for c in chunks if c.ordinal > 0)

    print(f"\n{len(chunks)} chunks from {len(per_doc)} documents")
    print(f"  chars: min {lengths[0]}  median {lengths[len(lengths)//2]}  "
          f"max {lengths[-1]}  mean {sum(lengths)//len(lengths)}")
    print(f"  sections split into more than one chunk: {split}")
    print("\n  chunks per document:")
    for doc, count in sorted(per_doc.items()):
        print(f"    {count:3d}  {doc}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--stats", action="store_true",
                        help="describe the chunking that resulted")
    parser.add_argument("--dry-run", action="store_true",
                        help="chunk only; do not embed or write (no key needed)")
    args = parser.parse_args()

    root = index_module.corpus_root()
    if not os.path.isdir(root):
        print(f"no corpus at {root}", file=sys.stderr)
        return 1

    if args.dry_run:
        chunks = chunker.chunk_corpus(root)
        print(f"would index {len(chunks)} chunks from {root}")
        if args.stats:
            _stats(chunks)
        return 0

    if not (os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")):
        print("GEMINI_API_KEY is not set — needed for any uncached chunk.",
              file=sys.stderr)
        return 1

    idx = index_module.build(verbose=True)
    if args.stats:
        _stats(idx.chunks)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
