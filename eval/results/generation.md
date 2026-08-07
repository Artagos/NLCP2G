### Generation metrics

Generator **gemini-2.5-flash**, judge **gemini-2.5-flash-lite** at temperature 0, k=5. Deliberately different models, because a model scores its own output generously. Two caveats, stated rather than buried: the judge is the *smaller* of the two — `gemini-2.5-pro` was the first choice and returns 503 under free-tier load often enough that a run could not finish, and a judge that cannot be re-run is not reproducible. And it is the same vendor and family, so a shared prior about what a good answer looks like is reduced, not removed. The judge-sensitivity table below puts a number on how much of that is left.

| configuration | cases | faithfulness | answer relevance | context precision | context recall |
|---------------|-------|--------------|------------------|-------------------|----------------|
| no rerank     | 24    | 0.976        | 0.875            | 0.517             | 0.805          |
| reranked      | 24    | 0.977        | 0.917            | 0.617             | 0.925          |

**What reranking changed**

|       | faithfulness | answer relevance | context precision | context recall |
|-------|--------------|------------------|-------------------|----------------|
| delta | +0.001       | +0.042           | +0.100            | +0.120         |

Both columns are over the same cases, so the delta is a like-for-like comparison even when the two runs covered different amounts.


**By category** (reranked)

| category       | cases | faithfulness | answer relevance | context precision | context recall |
|----------------|-------|--------------|------------------|-------------------|----------------|
| exact-term     | 3     | 1.000        | 1.000            | 0.400             | 0.944          |
| acronym        | 3     | 1.000        | 1.000            | 0.733             | 1.000          |
| paraphrase     | 3     | 0.952        | 0.889            | 0.867             | 1.000          |
| near-duplicate | 3     | 0.963        | 0.889            | 0.933             | 0.927          |
| multi-hop      | 3     | 1.000        | 1.000            | 0.933             | 0.779          |
| negation       | 3     | 0.905        | 1.000            | 0.600             | 0.852          |
| ambiguous      | 3     | 1.000        | 0.667            | 0.467             | 0.949          |
| out-of-corpus  | 3     | 1.000        | 0.889            | 0.000             | 1.000          |

An average over a mixed set hides which category the system is bad at, which is the reason the cases are tagged at all.


**Out-of-corpus: did it decline?**

| case                         | no rerank | reranked        |
|------------------------------|-----------|-----------------|
| ooc-solve-my-problem         | declined  | answered anyway |
| ooc-specific-contest-problem | declined  | declined        |
| ooc-language-not-covered     | declined  | declined        |

These cases have no golden context, so every ranking metric is undefined on them. Whether the answerer invents something is the only thing there is to measure, and it is the thing that matters.


**Judge sensitivity** — the same 8 answers, re-judged by gemini-2.5-flash, the model that wrote them

| judge           | faithfulness | answer relevance | context precision | context recall |
|-----------------|--------------|------------------|-------------------|----------------|
| different judge | 0.950        | 0.875            | 0.600             | 0.953          |
| own model       | 0.983        | 0.917            | 0.675             | 0.938          |
| self-preference | +0.032       | +0.042           | +0.075            | -0.016         |

A positive bottom row is the generator's own model scoring its own output more generously than an independent one does — self-preference bias, measured rather than assumed. It bounds how much of the headline number is the judge liking itself.


**Per case** (reranked)

| case                               | category       | faithfulness | answer relevance | context precision | context recall |
|------------------------------------|----------------|--------------|------------------|-------------------|----------------|
| exact-lower-upper-bound            | exact-term     | 1.000        | 1.000            | 0.600             | 1.000          |
| exact-modulus-1e9-7                | exact-term     | 1.000        | 1.000            | 0.200             | 1.000          |
| exact-sync-with-stdio              | exact-term     | 1.000        | 1.000            | 0.400             | 0.833          |
| acronym-dsu                        | acronym        | 1.000        | 1.000            | 1.000             | 1.000          |
| acronym-bit                        | acronym        | 1.000        | 1.000            | 1.000             | 1.000          |
| acronym-tle-vs-mle                 | acronym        | 1.000        | 1.000            | 0.200             | 1.000          |
| paraphrase-too-slow                | paraphrase     | 1.000        | 1.000            | 1.000             | 1.000          |
| paraphrase-estimate-speed          | paraphrase     | 0.857        | 1.000            | 0.800             | 1.000          |
| paraphrase-negative-result         | paraphrase     | 1.000        | 0.667            | 0.800             | 1.000          |
| dup-fenwick-or-segment             | near-duplicate | 0.889        | 1.000            | 1.000             | 0.923          |
| dup-map-or-unordered-map           | near-duplicate | 1.000        | 1.000            | 1.000             | 1.000          |
| dup-bfs-for-cheapest               | near-duplicate | 1.000        | 0.667            | 0.800             | 0.857          |
| multihop-recursive-crash           | multi-hop      | 1.000        | 1.000            | 1.000             | 0.750          |
| multihop-changing-range-sums       | multi-hop      | 1.000        | 1.000            | 0.800             | 0.800          |
| multihop-passes-sample-fails-later | multi-hop      | 1.000        | 1.000            | 1.000             | 0.786          |
| negation-sliding-window            | negation       | 0.714        | 1.000            | 0.200             | 1.000          |
| negation-greedy-fails              | negation       | 1.000        | 1.000            | 0.800             | 0.556          |
| negation-dijkstra                  | negation       | 1.000        | 1.000            | 0.800             | 1.000          |
| ambiguous-how-trees-work           | ambiguous      | 1.000        | 0.333            | 0.000             | 1.000          |
| ambiguous-fastest-sort             | ambiguous      | 1.000        | 0.667            | 0.400             | 0.846          |
| ambiguous-memory-usage             | ambiguous      | 1.000        | 1.000            | 1.000             | 1.000          |
| ooc-solve-my-problem               | out-of-corpus  | 1.000        | 0.667            | 0.000             | —              |
| ooc-specific-contest-problem       | out-of-corpus  | —            | 1.000            | 0.000             | —              |
| ooc-language-not-covered           | out-of-corpus  | 1.000        | 1.000            | 0.000             | 1.000          |
