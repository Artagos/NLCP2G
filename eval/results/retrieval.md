### Retrieval metrics

152 chunks from 21 documents. Retrieve 30 dense + 30 lexical, fuse with RRF (k=60), rerank the top 20.

Averaged over the 21 answerable cases. The 3 out-of-corpus cases have an empty golden set, so all five metrics are undefined on them; they are excluded here and measured in the generation table instead.

**Reading precision@k.** Each case has one or two golden chunks, so precision@k cannot exceed |golden|/k. The ceiling is 0.476 at k=3, 0.286 at k=5, 0.143 at k=10 — compare the precision column against those, not against 1.0.


**no rerank**

| k  | hit rate@k | precision@k | recall@k | MRR   | nDCG@k |
|----|------------|-------------|----------|-------|--------|
| 3  | 0.810      | 0.349       | 0.762    | 0.762 | 0.730  |
| 5  | 0.810      | 0.219       | 0.786    | 0.762 | 0.741  |
| 10 | 0.857      | 0.119       | 0.857    | 0.767 | 0.764  |

**reranked**

| k  | hit rate@k | precision@k | recall@k | MRR   | nDCG@k |
|----|------------|-------------|----------|-------|--------|
| 3  | 0.952      | 0.429       | 0.905    | 0.873 | 0.865  |
| 5  | 1.000      | 0.267       | 0.952    | 0.883 | 0.883  |
| 10 | 1.000      | 0.138       | 0.976    | 0.883 | 0.893  |

**What reranking changed**

| k  | hit rate@k | precision@k | recall@k | MRR    | nDCG@k |
|----|------------|-------------|----------|--------|--------|
| 3  | +0.143     | +0.079      | +0.143   | +0.111 | +0.135 |
| 5  | +0.190     | +0.048      | +0.167   | +0.121 | +0.142 |
| 10 | +0.143     | +0.019      | +0.119   | +0.116 | +0.129 |

**By category** (k=5, reranked)

| category       | cases         | hit rate@k | precision@k | recall@k | MRR   | nDCG@k |
|----------------|---------------|------------|-------------|----------|-------|--------|
| exact-term     | 3             | 1.000      | 0.267       | 1.000    | 1.000 | 1.000  |
| acronym        | 3             | 1.000      | 0.200       | 1.000    | 1.000 | 1.000  |
| paraphrase     | 3             | 1.000      | 0.267       | 1.000    | 0.667 | 0.775  |
| near-duplicate | 3             | 1.000      | 0.400       | 1.000    | 1.000 | 0.973  |
| multi-hop      | 3             | 1.000      | 0.267       | 0.667    | 0.778 | 0.640  |
| negation       | 3             | 1.000      | 0.200       | 1.000    | 1.000 | 1.000  |
| ambiguous      | 3             | 1.000      | 0.267       | 1.000    | 0.733 | 0.796  |
| out-of-corpus  | 3 (undefined) | —          | —           | —        | —     | —      |

**Per case** (k=5, reranked)

| case                               | category       | hit rate@k | precision@k | recall@k | MRR   | nDCG@k |
|------------------------------------|----------------|------------|-------------|----------|-------|--------|
| exact-lower-upper-bound            | exact-term     | 1.000      | 0.400       | 1.000    | 1.000 | 1.000  |
| exact-modulus-1e9-7                | exact-term     | 1.000      | 0.200       | 1.000    | 1.000 | 1.000  |
| exact-sync-with-stdio              | exact-term     | 1.000      | 0.200       | 1.000    | 1.000 | 1.000  |
| acronym-dsu                        | acronym        | 1.000      | 0.200       | 1.000    | 1.000 | 1.000  |
| acronym-bit                        | acronym        | 1.000      | 0.200       | 1.000    | 1.000 | 1.000  |
| acronym-tle-vs-mle                 | acronym        | 1.000      | 0.200       | 1.000    | 1.000 | 1.000  |
| paraphrase-too-slow                | paraphrase     | 1.000      | 0.400       | 1.000    | 0.500 | 0.693  |
| paraphrase-estimate-speed          | paraphrase     | 1.000      | 0.200       | 1.000    | 0.500 | 0.631  |
| paraphrase-negative-result         | paraphrase     | 1.000      | 0.200       | 1.000    | 1.000 | 1.000  |
| dup-fenwick-or-segment             | near-duplicate | 1.000      | 0.400       | 1.000    | 1.000 | 1.000  |
| dup-map-or-unordered-map           | near-duplicate | 1.000      | 0.400       | 1.000    | 1.000 | 1.000  |
| dup-bfs-for-cheapest               | near-duplicate | 1.000      | 0.400       | 1.000    | 1.000 | 0.920  |
| multihop-recursive-crash           | multi-hop      | 1.000      | 0.400       | 1.000    | 1.000 | 1.000  |
| multihop-changing-range-sums       | multi-hop      | 1.000      | 0.200       | 0.500    | 1.000 | 0.613  |
| multihop-passes-sample-fails-later | multi-hop      | 1.000      | 0.200       | 0.500    | 0.333 | 0.307  |
| negation-sliding-window            | negation       | 1.000      | 0.200       | 1.000    | 1.000 | 1.000  |
| negation-greedy-fails              | negation       | 1.000      | 0.200       | 1.000    | 1.000 | 1.000  |
| negation-dijkstra                  | negation       | 1.000      | 0.200       | 1.000    | 1.000 | 1.000  |
| ambiguous-how-trees-work           | ambiguous      | 1.000      | 0.200       | 1.000    | 1.000 | 1.000  |
| ambiguous-fastest-sort             | ambiguous      | 1.000      | 0.400       | 1.000    | 1.000 | 1.000  |
| ambiguous-memory-usage             | ambiguous      | 1.000      | 0.200       | 1.000    | 0.200 | 0.387  |
| ooc-solve-my-problem               | out-of-corpus  | —          | —           | —        | —     | —      |
| ooc-specific-contest-problem       | out-of-corpus  | —          | —           | —        | —     | —      |
| ooc-language-not-covered           | out-of-corpus  | —          | —           | —        | —     | —      |
