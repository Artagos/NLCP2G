"""The retrieval layer: chunking, tokenizing, fusion, and the tool it is behind.

Nothing here touches the network. Dense search needs an embedding, so these
build a tiny corpus in a temp directory and give it deterministic fake vectors —
which is enough to test the wiring, the fusion and the ranking, all of which are
ours. The quality of the real embeddings is not a unit-test question; that is
what `eval/run_retrieval.py` measures.

One test does read the committed index, to check it is consistent with the
corpus on disk. That one is about the artefact, not the algorithm.
"""
from __future__ import annotations

import json
import os
import textwrap

import numpy as np
import pytest
from langchain_core.messages import AIMessage, ToolMessage

from backend import llm
from backend.graphs import tutor as tutor_graph
from backend.rag import chunker, dense, embeddings, index as index_module, retriever
from backend.rag.fusion import RRF_K, reciprocal_rank_fusion
from backend.rag.lexical import Lexical, tokenize
from backend.tools.tutor_tools import TUTOR_TOOLS

# --------------------------------------------------------------- the chunker

DOC = textwrap.dedent("""\
    # Binary search

    ## The invariant

    Binary search needs monotonicity.

    ## lower_bound and upper_bound

    `lower_bound` returns the first element not less than x.

    ```cpp
    // a fence with a ## inside it
    int mid = lo + (hi - lo) / 2;
    ```

    Trailing paragraph.
    """)


def test_chunking_splits_on_headings_and_keeps_the_title():
    chunks = chunker.chunk_document("binary-search", DOC)

    assert [c.heading for c in chunks] == ["The invariant",
                                           "lower_bound and upper_bound"]
    assert all(c.title == "Binary search" for c in chunks)
    # The heading path rides in the text: it is the only context a chunk has
    # once it is out of its document, and both retrievers read it.
    assert chunks[0].text.startswith("# Binary search\n## The invariant")


def test_a_hash_inside_a_code_fence_is_not_a_heading():
    chunks = chunker.chunk_document("binary-search", DOC)
    assert len(chunks) == 2, "the ## inside the cpp fence started a new section"
    assert "int mid" in chunks[1].text


def test_chunk_ids_are_stable_and_slugged_from_the_heading():
    chunks = chunker.chunk_document("binary-search", DOC)
    # The slug is deliberately lossy — every non-alphanumeric run becomes one
    # hyphen, so the underscores in the heading do not survive into the id. That
    # is fine because ids are opaque handles; the eval set anchors on the
    # heading text itself precisely so it never has to know this rule.
    assert chunks[1].chunk_id == "binary-search#lower-bound-and-upper-bound#0"
    assert chunker.chunk_document("binary-search", DOC)[1].chunk_id == \
        chunks[1].chunk_id


def test_a_long_section_splits_but_never_inside_a_code_fence():
    body = "\n\n".join(f"Paragraph number {i} with enough text to take up room."
                       for i in range(40))
    doc = f"# T\n\n## Long\n\n{body}\n\n```cpp\n" + \
          "\n".join(f"int x{i} = {i};" for i in range(40)) + "\n```\n"
    chunks = chunker.chunk_document("t", doc)

    assert len(chunks) > 1, "a 2 kB section should have been split"
    for chunk in chunks:
        assert chunk.text.count("```") % 2 == 0, "a code fence was cut in half"


def test_headings_that_slug_alike_are_refused_rather_than_merged():
    doc = "# T\n\n## Fast IO\n\nbody\n\n## fast io\n\nother body\n"
    with pytest.raises(ValueError, match="collide"):
        chunker.chunk_document("t", doc)


# -------------------------------------------------------------- the tokenizer

def test_the_tokenizer_keeps_the_terms_the_corpus_is_searched_by():
    assert "lower_bound" in tokenize("use lower_bound here")
    assert "std::sort" in tokenize("call std::sort on it")
    assert "1e9+7" in tokenize("modulo 1e9+7 please")


def test_compounds_are_also_split_so_natural_phrasing_still_matches():
    """Emit-both. The exact-term query matches the rare compound; "the lower
    bound" matches the parts. Indexing only one of the two gives up a query."""
    tokens = tokenize("lower_bound")
    assert "lower_bound" in tokens and "lower" in tokens and "bound" in tokens

    qualified = tokenize("std::sort")
    assert "std::sort" in qualified and "sort" in qualified
    # ...but the bare words must not ALSO be emitted a second time by the
    # general pass, which would double their term frequency.
    assert qualified.count("sort") == 1


def _corpus(*texts) -> list[chunker.Chunk]:
    """Chunks from raw texts.

    Deliberately more than a handful. rank_bm25 computes IDF as
    log(N - df + 0.5) - log(df + 0.5), which is exactly zero when a term appears
    in half the corpus — so on a two-document corpus every term scores nothing
    and BM25 has no opinion about anything. That is an artefact of a tiny N, not
    a bug, but it makes two-document fixtures useless for testing ranking.
    """
    return [chunker.Chunk(chr(ord("a") + i), "d", "T", "h", 0, text)
            for i, text in enumerate(texts)]


def test_bm25_finds_a_rare_exact_term_over_a_topical_near_miss():
    chunks = _corpus(
        "binary search needs the array to be sorted before it can work",
        "lower_bound returns the first element not less than x in a sorted range",
        "sorting arranges the elements of an array into order",
        "a sorted array can be searched quickly by halving the range",
        "hashing maps keys to buckets and loses all ordering information",
        "a queue hands back the oldest element that is still waiting",
    )
    hits = Lexical(chunks).search("lower_bound", 5)
    assert hits, "the rare identifier should have matched something"
    assert hits[0][0] == "b"


def test_bm25_drops_documents_no_query_term_appears_in():
    """A zero score is no opinion, not a weak one. Passing those to RRF would
    award rank credit the lexical arm never earned."""
    chunks = _corpus(
        "hashing maps keys to buckets in constant time on average",
        "sorting arranges elements into order using comparisons",
        "graphs are made of vertices joined by edges",
        "a stack hands back the most recently added element first",
        "recursion needs a base case and measurable progress each call",
        "modular arithmetic keeps intermediate values small",
    )
    hits = Lexical(chunks).search("hashing", 5)
    assert all(score > 0 for _, score in hits)
    assert hits[0][0] == "a"


# ------------------------------------------------------------------ the fusion

def test_rrf_rewards_agreement_between_the_two_retrievers():
    """`b` is second on both lists and `a` is first on one and absent from the
    other. Broad agreement should win, which is the whole reason for RRF."""
    fused = reciprocal_rank_fusion([["a", "b", "c"], ["d", "b", "e"]])
    assert fused[0][0] == "b"

    expected = 1 / (RRF_K + 2) + 1 / (RRF_K + 2)
    assert fused[0][1] == pytest.approx(expected)


def test_rrf_uses_ranks_and_ignores_scores_entirely():
    """It takes id lists, not (id, score) pairs — so a retriever with a wildly
    different score scale cannot dominate by magnitude."""
    assert reciprocal_rank_fusion([["x"]])[0][1] == pytest.approx(1 / (RRF_K + 1))


def test_rrf_ties_break_deterministically():
    """A nondeterministic tie-break would show up in the eval as a metric that
    moves between identical runs."""
    once = reciprocal_rank_fusion([["a", "b"], ["c", "d"]])
    twice = reciprocal_rank_fusion([["a", "b"], ["c", "d"]])
    assert once == twice


# ------------------------------------------------------- dense, with fake vectors

def test_dense_search_ranks_by_cosine():
    matrix = embeddings.normalize(np.array(
        [[1.0, 0.0], [0.7, 0.7], [0.0, 1.0]], dtype=np.float32))
    query = embeddings.normalize(np.array([1.0, 0.0], dtype=np.float32))

    assert [cid for cid, _ in dense.search(query, matrix, ["a", "b", "c"], 3)] == \
        ["a", "b", "c"]


def test_normalize_leaves_a_zero_row_alone_rather_than_producing_nans():
    out = embeddings.normalize(np.array([[0.0, 0.0], [3.0, 4.0]], dtype=np.float32))
    assert not np.isnan(out).any()
    assert out[1].tolist() == pytest.approx([0.6, 0.8])


# ------------------------------------------------------ the layer, end to end

@pytest.fixture
def tiny_index(tmp_path, monkeypatch):
    """A three-document corpus with deterministic fake embeddings."""
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    (corpus / "alpha.md").write_text(
        "# Alpha\n\n## Sorting\n\nSorting arranges elements in order.\n",
        encoding="utf-8")
    (corpus / "beta.md").write_text(
        "# Beta\n\n## Hashing\n\nHashing maps keys to buckets.\n",
        encoding="utf-8")
    (corpus / "README.md").write_text(
        "# Not indexed\n\n## Skip\n\nThis file must not be indexed.\n",
        encoding="utf-8")

    chunks = chunker.chunk_corpus(str(corpus))

    def fake_embed(texts, task_type=embeddings.DOCUMENT):
        # One dimension per chunk: chunk i is the unit vector e_i. A query
        # embeds as whichever chunk shares the most words with it.
        out = np.zeros((len(texts), max(len(chunks), 1)), dtype=np.float32)
        for row, text in enumerate(texts):
            words = set(text.lower().split())
            best = max(range(len(chunks)),
                       key=lambda i: len(words & set(chunks[i].text.lower().split())))
            out[row][best] = 1.0
        return out

    monkeypatch.setattr(embeddings, "embed", fake_embed)
    monkeypatch.setattr(embeddings, "embed_query",
                        lambda text: fake_embed([text])[0])
    monkeypatch.setenv("CP_TUTOR_CORPUS", str(corpus))
    monkeypatch.setenv("CP_TUTOR_CORPUS_INDEX", str(tmp_path / "index"))
    index_module.reset()

    idx = index_module.build()
    yield idx
    index_module.reset()


def test_the_corpus_readme_is_not_indexed(tiny_index):
    assert {c.doc for c in tiny_index.chunks} == {"alpha", "beta"}


def test_search_returns_retriever_order_with_ranks_from_one(tiny_index):
    results = retriever.search("hashing maps keys", k=2, rerank=False,
                               idx=tiny_index)
    assert [r.rank for r in results] == [1, 2]
    assert results[0].doc == "beta"


def test_the_rerank_flag_is_a_real_seam(monkeypatch, tiny_index):
    """The eval runs the identical pipeline both ways, so `rerank=False` has to
    mean the reranker is not reached at all — not that it ran and was ignored."""
    from backend.rag import rerank as rerank_module

    called: list[str] = []
    monkeypatch.setattr(rerank_module, "rerank",
                        lambda q, c: called.append(q) or [cid for cid, _ in c])

    retriever.search("sorting", k=2, rerank=False, idx=tiny_index)
    assert called == []

    retriever.search("sorting", k=2, rerank=True, idx=tiny_index)
    assert called == ["sorting"]


def test_a_reranker_failure_costs_the_improvement_and_not_the_answer(
        monkeypatch, tiny_index):
    from backend.rag import rerank as rerank_module

    monkeypatch.setattr(llm, "generate_structured",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("503")))
    monkeypatch.setattr(rerank_module, "_cache", lambda: _NullCache())

    results = retriever.search("sorting", k=2, rerank=True, idx=tiny_index)
    assert results, "a failed rerank should fall back to fused order, not empty"


class _NullCache:
    def get(self, key):
        return None

    def put(self, key, value):
        pass


# ------------------------------------------------------- the committed index

def test_the_committed_index_matches_the_corpus_on_disk():
    """Guards the one failure mode nothing else would catch: an index rebuilt
    from a different corpus than the one in the repo. The ids would still look
    plausible and every retrieval would be subtly wrong."""
    idx = index_module.load()
    fresh = chunker.chunk_corpus(index_module.corpus_root())

    assert [c.chunk_id for c in idx.chunks] == [c.chunk_id for c in fresh], \
        "corpus/ has changed since the index was built — run scripts/build_index.py"
    assert len(idx.vectors) == len(idx.chunks)


def test_every_index_artefact_is_present_and_aligned():
    root = index_module.index_root()
    with open(os.path.join(root, "chunks.jsonl"), encoding="utf-8") as fh:
        lines = [json.loads(line) for line in fh if line.strip()]
    vectors = np.load(os.path.join(root, "vectors.npy"))

    assert len(lines) == len(vectors)
    assert vectors.shape[1] == llm.EMBED_DIMS
    assert len({row["chunk_id"] for row in lines}) == len(lines), "duplicate ids"


# ------------------------------------------------------------------- the tool

def test_search_corpus_is_registered_with_the_framework():
    names = [t.name for t in TUTOR_TOOLS]
    assert "search_corpus" in names
    assert len(TUTOR_TOOLS) >= 2

    tool = next(t for t in TUTOR_TOOLS if t.name == "search_corpus")
    assert tool.description.strip()
    # The model chooses the query. If it were not in the schema, retrieval would
    # be a pipeline step wearing a tool's clothes.
    assert "query" in tool.args_schema.model_json_schema()["properties"]


def test_the_model_decides_to_search_and_can_search_again(monkeypatch, problem):
    """Driven through the real compiled graph, the way a model drives it.

    Two searches in one turn is the behaviour `MAX_TOOL_ROUNDS = 6` exists to
    allow: the first query comes back off-target and the right move is a sharper
    second one, not an answer built on the wrong passages.
    """
    seen: list[str] = []

    def fake_search(query, k=5, rerank=True, **kw):
        seen.append(query)
        return [retriever.Retrieved(chunk_id=f"doc#{len(seen)}#0", doc="doc",
                                    heading="h", text=f"passage for {query}",
                                    score=1.0, rank=1)]

    monkeypatch.setattr(retriever, "search", fake_search)

    replies = [
        AIMessage(content="", tool_calls=[{"name": "search_corpus", "id": "a",
                                           "args": {"query": "bounds"},
                                           "type": "tool_call"}]),
        AIMessage(content="", tool_calls=[{"name": "search_corpus", "id": "b",
                                           "args": {"query": "lower_bound"},
                                           "type": "tool_call"}]),
        AIMessage(content="Here is the answer."),
    ]

    class _Scripted:
        def bind_tools(self, tools):
            return self

        def invoke(self, messages, *a, **kw):
            return replies.pop(0) if replies else AIMessage(content="ok")

    model = _Scripted()
    monkeypatch.setattr(llm, "chat_model", lambda *a, **k: model)

    final = tutor_graph.run(problem, "what are the bounds functions", user_id="u")

    assert seen == ["bounds", "lower_bound"], "the model could not re-query"
    delivered = [m for m in final["messages"] if isinstance(m, ToolMessage)]
    assert len(delivered) == 2
    assert final["tools_called"] == ["search_corpus"]
    # Deduplicated provenance, in retrieval order, for the run log.
    assert final["chunks_used"] == ["doc#1#0", "doc#2#0"]


def test_a_missing_index_degrades_instead_of_losing_the_turn(monkeypatch, problem):
    def boom(*a, **k):
        raise FileNotFoundError("no index")

    monkeypatch.setattr(retriever, "search", boom)

    replies = [
        AIMessage(content="", tool_calls=[{"name": "search_corpus", "id": "a",
                                           "args": {"query": "x"},
                                           "type": "tool_call"}]),
        AIMessage(content="Answered without notes."),
    ]

    class _Scripted:
        def bind_tools(self, tools):
            return self

        def invoke(self, messages, *a, **kw):
            return replies.pop(0) if replies else AIMessage(content="ok")

    monkeypatch.setattr(llm, "chat_model", lambda *a, **k: _Scripted())

    final = tutor_graph.run(problem, "explain hashing", user_id="u")
    assert "Answered without notes." in final["messages"][-1].content
    assert final.get("chunks_used", []) == []
