"""The evaluation harness. Deliberately outside `backend/`.

Nothing in the application imports this package, and nothing here reimplements
retrieval. The harness calls `backend.rag.retriever.search` — the same function
`search_corpus` calls — with `rerank` set both ways, so what it measures is the
code the agent actually runs rather than a copy that could drift from it.

Two families of metric, run in that order:

  `metrics/rank.py`   hit rate@k, precision@k, recall@k, MRR, nDCG@k. No model
                      call, no judge, exactly reproducible. These run first
                      because they are free, and a retrieval bug found here
                      costs nothing while the same bug found downstream has
                      already been paid for in judged metrics.

  `metrics/judge.py`  faithfulness, answer relevance, context precision, context
                      recall. One model call each, cached to disk so a re-run is
                      free and identical.

Agent-level evaluation is not here. Measuring whether the tutor as a whole
behaves well is a different question with different instruments, and it belongs
to the next increment.
"""
