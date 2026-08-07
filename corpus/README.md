# The concept corpus

Reference notes on general computer science and C++, written for this project and
used by the tutor's `search_corpus` tool.

**This file is not indexed.** The indexer skips `README.md`, so nothing here can be
retrieved or cited — it is documentation about the corpus, not part of it.

## What belongs in here, and what does not

The scope is set by the operating rules in `rules/operating_rules.md`, not by taste:

> **R1 — Never reveal how to solve the current problem.** No hints, no nudges, no
> naming the technique that would work.
>
> **R2 — General concepts are always fair game.** Explain data structures,
> complexity notation, how an operation behaves, what a language construct does —
> in the abstract, with small generic examples that are not the current problem.

So the corpus is **exactly the R2 material**. Every document explains a concept in
the abstract. None of them is about a specific problem, and none contains an
editorial, a solution write-up, or a worked answer to a task a learner might be
given.

That is a deliberate limit rather than an accident of what was convenient to write.
A corpus of editorials would put the tutor one retrieval away from the material R1
forbids it to use, and no amount of prompt instruction is as reliable as the
material simply not being there.

The small code examples that do appear are generic — a five-line binary search, a
Fenwick tree's `add` — and are the kind of illustration R2 explicitly permits.

## The documents

21 files, one topic each.

| Area | Documents |
|---|---|
| Foundations | `complexity-and-big-o`, `io-and-runtime-errors`, `debugging-tle-wa` |
| Arrays and scanning | `arrays-and-prefix-sums`, `binary-search`, `two-pointers-and-sliding-window`, `sorting-and-comparators` |
| Containers | `stl-containers`, `hashing-and-maps`, `stacks-queues-deques` |
| Graphs | `graph-traversal-bfs-dfs`, `shortest-paths`, `dsu-union-find`, `trees-and-lca` |
| Range structures | `fenwick-tree`, `segment-tree` |
| Techniques | `dynamic-programming-patterns`, `greedy-and-exchange-arguments`, `recursion-and-backtracking` |
| Other | `modular-arithmetic`, `strings-and-pattern-matching` |

Some pairs overlap on purpose. `fenwick-tree` and `segment-tree` both cover range
sums and both contain a comparison table naming the other; `hashing-and-maps` and
`stl-containers` both discuss `unordered_map` against `map`; `graph-traversal-bfs-dfs`
and `shortest-paths` both explain when BFS is and is not a shortest-path algorithm.

That overlap is the point. Near-duplicate content is one of the retrieval failure
modes the evaluation measures, and a corpus with no genuinely confusable documents
would make the reranker look useful or useless for the wrong reasons.

## Editing it

Chunk ids are derived from the file name and the heading text, so **renaming a file
or reworded heading changes the ids**. The eval set does not store ids for that
reason; it stores `{doc, heading}` anchors and resolves them at load time
(`eval/anchors.py`). If you rename a heading, update the anchors that point at it —
the harness fails loudly on an anchor that resolves to nothing rather than quietly
scoring zero.

After any edit, rebuild the index:

```bash
python scripts/build_index.py
```

Unchanged chunks are served from the embedding cache, so a one-document edit costs
only that document's embeddings.
