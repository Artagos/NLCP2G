# Dynamic programming patterns

## When dynamic programming applies

Two conditions. **Optimal substructure**: the best answer to the whole is built from
best answers to smaller pieces. **Overlapping subproblems**: those smaller pieces
recur, so caching them is worth something.

Without overlap you have divide and conquer, not DP. Without optimal substructure
neither applies and you are looking at search.

The practical recipe is always the same four steps, in order:

1. Define the state — what a subproblem is, precisely, in words.
2. Write the transition — how a state's answer follows from smaller states.
3. Fix the base cases.
4. Choose an evaluation order in which every state's dependencies come first.

Skipping step 1 and going straight to a recurrence is the usual reason a DP does
not come out. If you cannot say in one sentence what `dp[i][j]` *means*, the
transition will be guesswork.

## Top-down versus bottom-up

**Top-down** (memoised recursion) follows the recurrence directly and only computes
states it actually needs.

```cpp
long long solve(int i) {
    if (i < 0) return 0;
    if (memo[i] != -1) return memo[i];
    return memo[i] = max(solve(i - 1), a[i] + solve(i - 2));
}
```

**Bottom-up** fills a table in dependency order. No recursion, so no stack limit,
and it allows the memory optimisations below.

```cpp
dp[0] = a[0];
dp[1] = max(a[0], a[1]);
for (int i = 2; i < n; ++i)
    dp[i] = max(dp[i - 1], a[i] + dp[i - 2]);
```

Use top-down when the state space is large but sparsely visited, or the order is
awkward to write. Use bottom-up when you need the speed, the memory reduction, or a
guarantee against stack overflow.

A memo array must be initialised to a value that cannot be a real answer. Using 0 as
"not computed" when 0 is a legitimate answer produces a DP that recomputes forever
or returns wrong values.

## Knapsack

**0/1 knapsack** — each item used at most once, capacity `W`:

```cpp
vector<int> dp(W + 1, 0);
for (int i = 0; i < n; ++i)
    for (int w = W; w >= weight[i]; --w)          // DOWNWARDS
        dp[w] = max(dp[w], dp[w - weight[i]] + value[i]);
```

**Unbounded knapsack** — items reusable — is the same loop ascending:

```cpp
for (int w = weight[i]; w <= W; ++w)              // UPWARDS
    dp[w] = max(dp[w], dp[w - weight[i]] + value[i]);
```

The direction of the inner loop is the entire difference between the two problems.
Descending, `dp[w - weight[i]]` still holds the previous item's row, so the item is
used once. Ascending, it holds the current row, so the item can be used again. This
is worth understanding rather than memorising, because it is exactly the kind of
one-character difference that is invisible in review.

## Common state shapes

| Problem shape | State | Transition |
|---|---|---|
| Longest increasing subsequence | `dp[i]` = best ending at `i` | over all `j < i` with `a[j] < a[i]` |
| Edit distance | `dp[i][j]` = cost for prefixes | insert / delete / replace |
| Longest common subsequence | `dp[i][j]` = LCS of prefixes | match, or drop from either |
| Coin change | `dp[v]` = ways or fewest coins for `v` | over each coin |
| Grid paths | `dp[r][c]` | from above and from the left |
| Interval DP | `dp[l][r]` | split at every `m` in between |
| Bitmask DP | `dp[mask]` = best over a used-set | add one unused element |

Longest increasing subsequence has an O(n log n) form that is worth knowing
separately: maintain the smallest possible tail for each length, and binary search
it with `lower_bound` for each new element. The array of tails is not itself a valid
subsequence, only its length is meaningful.

Bitmask DP is bounded by memory and time at roughly n = 20, since 2²⁰ states is a
million and 2²⁵ is not reachable.

## Reducing the memory

When `dp[i]` depends only on `dp[i - 1]`, two rows suffice, and often one row
updated in place — the knapsack above is exactly this, having collapsed an
`n × W` table to a single array of size `W + 1`.

For a grid DP that reads only the row above and the cell to the left, a single row
updated left to right works, because by the time you reach a cell its left neighbour
is already this row and everything ahead is still the previous one.

This turns O(nW) memory into O(W), which is frequently the difference between
fitting in the memory limit and not. It costs you the ability to reconstruct the
choices afterwards, so keep the full table when the problem asks *which* items and
not just the value.

## Reconstructing the answer

Store, alongside each state, the decision that produced it — or recompute it by
checking which transition attains the stored optimum. Then walk backwards from the
final state.

```cpp
int w = W;
for (int i = n - 1; i >= 0; --i)
    if (dp[i + 1][w] != dp[i][w]) { chosen.push_back(i); w -= weight[i]; }
```

Recomputing rather than storing costs nothing extra in time and saves the memory of
a parallel choice table.

## Mistakes worth knowing

**A state that does not capture everything the future depends on.** If two different
histories reach the same state but lead to different futures, the state is
underspecified and the DP is wrong. This is the most common and hardest-to-see DP
bug.

**Iterating in an order where a dependency is not ready.** Bottom-up requires that
every state a transition reads has already been finalised.

**Off-by-one between 0-based data and a 1-based DP table.** Prefix-indexed DP
(`dp[i]` meaning "first i elements") is usually cleaner than element-indexed, and
makes the empty prefix a natural base case.

**Overflow in a counting DP.** Counting problems grow fast; take the modulus at
every addition, not just at the end.

**Memoising on an incomplete key.** If the recursion takes three parameters and the
memo table is indexed by two, it returns another subproblem's answer.
