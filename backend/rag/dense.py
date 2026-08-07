"""Dense vector search: exact cosine similarity, in numpy, over the whole corpus.

There is no vector database here and that is a decision rather than an omission.
This corpus produces a few hundred chunks. A brute-force dot product against 300
unit vectors of 768 dimensions is about 230 000 multiply-adds — under a
millisecond, and dwarfed by the network round trip that produced the query
embedding in the first place.

What that buys is exactness. An approximate nearest-neighbour index (FAISS,
HNSW, anything a vector store gives you) trades recall for speed at a scale this
corpus never reaches, and it would put an approximation *underneath* an
evaluation whose whole purpose is measuring retrieval quality. When recall@10
comes out at 0.8, it should be because the embeddings ranked something poorly,
not because an ANN index skipped a cell.

At a hundred thousand chunks this would be the wrong call. At three hundred it
is the right one, and the honest version of "we chose a vector store" is that
there was nothing for it to do.
"""
from __future__ import annotations

import numpy as np


def search(
    query_vector: np.ndarray, matrix: np.ndarray, ids: list[str], n: int
) -> list[tuple[str, float]]:
    """Top-n by cosine similarity, best first.

    Both the query and the rows of `matrix` are expected to be unit vectors
    already (`embeddings.normalize` does it), so the dot product *is* the cosine
    and no per-query normalisation is needed.
    """
    if matrix.size == 0 or n <= 0:
        return []

    scores = matrix @ query_vector
    n = min(n, len(ids))

    # argpartition finds the top n without sorting the other few hundred; the
    # small slice is then sorted properly. Worth nothing at this size, but it is
    # the shape that stays correct if the corpus grows.
    top = np.argpartition(-scores, n - 1)[:n]
    top = top[np.argsort(-scores[top], kind="stable")]
    return [(ids[i], float(scores[i])) for i in top]
