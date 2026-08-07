# Greedy algorithms and exchange arguments

## What makes a greedy algorithm

A greedy algorithm builds a solution by repeatedly taking whatever looks best right
now, and never reconsidering. It is the simplest strategy there is, it is usually
fast, and it is wrong far more often than it looks.

The whole difficulty is not writing one. It is knowing whether the one you wrote is
correct — because a greedy algorithm that is wrong tends to be right on every small
example you try by hand.

## The two properties you need

**Greedy choice property.** There is an optimal solution that agrees with the first
greedy choice. Taking that choice does not cost you the ability to finish optimally.

**Optimal substructure.** After making that choice, what remains is a smaller
instance of the same problem, and solving it optimally completes an optimal whole.

Both must hold. Optimal substructure alone gives you dynamic programming; it is the
greedy choice property that lets you skip the search.

## Exchange arguments

The standard way to prove the greedy choice property. Take any optimal solution,
show that it can be transformed step by step into the greedy one without ever
getting worse, and conclude that the greedy solution is itself optimal.

The shape of the argument for an ordering problem:

1. Suppose an optimal solution puts `b` before `a`, where the greedy rule would have
   put `a` first.
2. Swap them.
3. Show the objective does not get worse.
4. Repeat until no such pair remains — which is the greedy order.

Step 3 is the real content. For scheduling to minimise total completion time, taking
the shortest job first survives the exchange because swapping an adjacent
longer-then-shorter pair strictly reduces the total. That single inequality *is* the
proof.

Doing this on paper for two adjacent elements is quick and it is what separates a
greedy algorithm you can trust from one you are hoping about.

## Greedy algorithms that are provably correct

**Activity selection.** To fit the most non-overlapping intervals, sort by *end*
time and take each interval that starts after the last one taken. Sorting by start
time or by duration both fail — the exchange argument only works for end time,
because finishing earliest leaves the most room for everything after.

**Kruskal's and Prim's minimum spanning trees.** Justified by the cut property: the
lightest edge crossing any partition of the vertices belongs to some MST.

**Huffman coding.** Repeatedly merging the two least frequent symbols is optimal, by
an exchange argument on the two deepest leaves.

**Fractional knapsack.** Sort by value per unit weight and fill greedily. Note this
is the *fractional* version; the 0/1 version is not greedy-solvable and needs DP.

**Coin change with a canonical system.** Taking the largest coin that fits works for
the coin systems in ordinary use, but not for arbitrary ones. With coins {1, 3, 4}
and a target of 6, greedy gives 4 + 1 + 1 = three coins; the optimum is 3 + 3 = two.
That example is worth remembering as the standard illustration of greedy failing.

## When greedy fails

The pattern is a choice that looks locally best but forecloses something better
later. 0/1 knapsack is the classic: the item with the best value-per-weight can
occupy space that two other items would have used more profitably, and once it is in
there is no way back.

If you cannot construct an exchange argument, look for a counterexample instead —
and look at small, lopsided cases: two elements, one enormous value, an exact-fit
capacity. Greedy failures usually show up at n = 3 or below if they show up at all.

When both attempts fail, the fallback is dynamic programming, which considers the
alternatives greedy discards.

## Sorting is usually the first step

Almost every greedy algorithm begins by sorting on the key the greedy rule uses, so
the cost is typically O(n log n) dominated by the sort. Choosing the sort key *is*
choosing the algorithm — and it is exactly what the exchange argument decides.

When the same "best remaining" question has to be asked repeatedly against a set
that changes, a priority queue replaces the sort and the cost stays O(n log n).

## Mistakes worth knowing

**Testing on small random cases and concluding correctness.** Greedy failures often
need a specific structure that random tests do not produce. Write a brute force and
compare on exhaustive small inputs instead — that is the reliable check.

**Sorting on the wrong key.** Start time versus end time, weight versus ratio. The
program runs and the answers are plausible.

**Assuming a greedy that works forwards works backwards.** The exchange argument is
directional.

**Ties.** When several choices look equally good, the tie-break can matter, and it is
rarely mentioned in the statement. If two orderings of equal keys give different
answers, the greedy rule is underspecified and probably wrong.
