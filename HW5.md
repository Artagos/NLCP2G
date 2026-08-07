# HW5 — Retrieval, and the harness that measures it

Evidence: [`traces/09`](traces/09-retrieval-stages.md) ·
numbers: [`eval/results/`](eval/results/) ·
design: [README § Retrieval](README.md#retrieval), [§ Evaluation](README.md#evaluation).

237 tests, up from 188. Two of the new files exist only to test the scorers,
because the numbers in this writeup are only worth as much as the code that
produced them.

## What I built

**A concept corpus, scoped by the operating rules rather than by convenience.**
21 markdown documents on general CS and C++ — 152 chunks. The scope was not a
judgement call: R1 says never reveal how to solve the current problem, R2 says
general concepts are always fair game, so the corpus is exactly the R2 material
and contains no problem editorials. Material that does not exist is a stronger
guarantee than an instruction not to read it. Some documents overlap on purpose
(`fenwick-tree` and `segment-tree` each name the other) because near-duplicate
noise is one of the things being measured.

**Hybrid retrieval** (`backend/rag/`): 30 dense + 30 BM25, fused with RRF at
k=60, top 20 reranked by a batched model call. Dense is an exact numpy dot
product rather than a vector database — at 152 chunks brute force is exact and
sub-millisecond, and an approximate index underneath an evaluation *of retrieval
quality* would measure the wrong thing. The BM25 tokenizer emits `lower_bound`
whole **and** as `lower`+`bound`, so the exact-term query matches the rare
compound while "the lower bound" still matches the parts.

**A tool the model chooses to call.** `search_corpus` is bound to the tutor's
model; nothing in the routing invokes it. `MAX_TOOL_ROUNDS` went 4 → 6 because
it is the first tool here expected to be called *twice*: when the first query
comes back off-target the right move is a sharper second search, and the old cap
would have forbidden exactly the behaviour the tool exists for.

**An eval harness** (`eval/`, outside `backend/`, imported by nothing in the
app). 24 cases over 8 categories with golden context stored as `{doc, heading}`
anchors resolved at load time — ids encode the chunker's parameters, so storing
them would invalidate the set on every re-chunk. Five rank metrics written by
hand, four judged metrics on the project's own `llm` seam.

## Ideas from the session

**Rank-aware metrics first, judged metrics second**, because the first family is
free and reproduces exactly. That ordering paid immediately: every retrieval
question was settled before a single judged call was made.

**Undefined is not zero.** The three out-of-corpus cases have an empty golden
set on purpose. Scoring them zero would drag every average down in proportion to
how many honest unanswerable cases the set contains — punishing the eval set for
being realistic.

**The `pack_for_lim` trap, made into a test.** Packing reorders results so the
strongest sit at the edges of the context; that destroys rank information which
MRR and nDCG read. `search` returns retriever order and `pack_for_llm` is a
separate function, and a test shows packing degrading MRR from 0.5 to 0.2 while
hit rate, precision and recall do not move at all — which is what makes the
mistake look like a retrieval regression instead of a bug.

## What the harness caught that I would have got wrong

**RRF made retrieval worse, and only reranking hid it.** On every paraphrase
case, dense search puts the right chunk at rank 1, BM25 does not return it at
all, and fusion pushes it down to rank 10–12. The arithmetic is exact: a
single-arm hit earns 1/(60+1) = 0.0164, while eleven chunks *both* arms returned
score 0.028–0.032 and bury it. RRF prefers agreement, and when one retriever is
blind to the query there is no agreement to be had.

I had written "fuse the two rankings" as an obviously-good step. On this eval
set, hybrid+RRF without a reranker is **worse than dense alone**, and the
reranker's large deltas (+0.190 hit rate@5) are mostly it repairing damage the
fusion did. That is the opposite of the story I expected to write.

**The before/after comparison was nearly worthless the first time.** I picked
eight cases by hand, one per category, and seven of them read "both arms found
it at rank 1, nothing changed". The selection is now computed from the data, and
the trace says plainly that 7 of the 21 answerable cases have all four stages
agreeing. A comparison that only shows the cases that improved is not a
comparison.

**Faithfulness at 0.977 does not mean the answers are right.** Retrieval
improved substantially (+0.190 hit rate@5, +0.142 nDCG) and faithfulness moved
+0.001. It was already 0.976 and it measures grounding, not correctness:
`ambiguous-how-trees-work` scores faithfulness 1.000 with context precision
0.000 — perfectly grounded in entirely useless passages. Context precision
(+0.100) and context recall (+0.120) are the columns that noticed the win.

**Reranking made the R1 case worse.** On "how should I solve the problem I am
working on right now", the un-reranked answerer declined and the reranked one
answered anyway. Better passages looked more confidently relevant and were more
tempting. One case, so not over-read — but it is a retrieval win becoming a
behavioural regression on the question where the cost is highest, and it is
where the next increment starts.

**Judge self-preference, measured rather than acknowledged.** `--judge-swap`
re-judges a stratified subset with the generator's own model: +0.032
faithfulness, +0.042 answer relevance, +0.075 context precision. That is roughly
the same size as the reranking gain on answer relevance, which is a reason to
trust the context-precision and context-recall deltas more than that one.

## How I tested it

```bash
python -m pytest tests -q                            # 237 tests, no API key
python -m eval.run_retrieval --check-reproducible    # byte-identical or fail
python -m eval.run_generation --judge-swap 1
python scripts/draw_graph.py --check
```

Every embedding, rerank verdict, generated answer and judge verdict is cached by
content hash and committed, so the whole evaluation reproduces offline. That is
checked rather than claimed: `--check-reproducible` re-runs and fails on a
single character of difference, and it was run with `GEMINI_API_KEY` unset to
confirm the network is genuinely not needed.

`tests/test_eval_metrics.py` checks the five rank metrics against values
computed by hand rather than recorded from the implementation — a fixture taken
from the code asserts only that it still does what it did, which is exactly what
a wrong scorer also does. `tests/test_judge_scorers.py` drives the judged
scorers offline with a scripted model, covering the places bugs would actually
be: a short judge reply cannot inflate faithfulness, an empty claim set is
undefined rather than zero, and the cache key separates judge models so the
swap cannot be served stale scores.

The 188 pre-existing tests pass unmodified.
