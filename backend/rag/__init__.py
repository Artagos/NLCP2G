"""Retrieval over the concept corpus.

    corpus/*.md ─► chunker ─► embeddings ─► index ─┬─► dense  ─┐
                                                   │           ├─► RRF ─► rerank
                                                   └─► lexical ┘

The layer is reached two ways, and the difference matters. The tutor reaches it
through `tools/tutor_tools.search_corpus`, a LangChain tool bound to the model —
so retrieval happens when the model decides it should, not on every turn before
the model has seen the question. The eval harness reaches `retriever.search`
directly, with `rerank` set both ways, so it measures the same code the agent
runs rather than a copy of it.

What the corpus contains is constrained by the operating rules, not by
convenience: it is general-concept reference material only, which is exactly
what R2 permits the tutor to explain. There are no problem editorials in it,
because R1 forbids the tutor to use them and the most reliable enforcement of
that is for the material not to exist. See `corpus/README.md`.

Nothing in this package imports FastAPI, the graphs, or the stores, so the
harness can use it without dragging the application in.
"""
