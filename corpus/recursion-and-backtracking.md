# Recursion and backtracking

## What a recursive function needs

Two things, and a missing one of either is the usual bug.

**A base case** that returns without recursing. **Progress** — every recursive call
must be on a strictly smaller instance, so the base case is eventually reached.

"Smaller" has to be measurable. A recursion on "the remaining list" shrinks; one on
"the same graph, different vertex" does not, and needs a visited set to supply the
progress the structure does not.

## The call stack, and how it runs out

Each pending call keeps its parameters, locals and return address on the call
stack. Recursion depth d therefore costs O(d) memory, and the stack is small —
typically 1 MB to 8 MB, which runs out somewhere between 10⁴ and 10⁵ frames
depending on how much each frame holds.

Overflowing it is a crash, not a slowdown, and judges report it as a runtime error
rather than as a memory limit. A depth-first search over a path-shaped graph with
10⁵ vertices hits this reliably.

Two fixes. Convert to an explicit stack, which moves the frames to the heap:

```cpp
stack<int> st;
st.push(start);
while (!st.empty()) {
    int u = st.top(); st.pop();
    if (seen[u]) continue;
    seen[u] = true;
    for (int v : adj[u]) if (!seen[v]) st.push(v);
}
```

Or restructure so the recursion is shallow. Note that **tail-call elimination is not
guaranteed** in C++ — compilers often do it at `-O2`, but it is not something to
depend on, and it does not happen at all in a debug build.

## Backtracking

Backtracking is recursion that builds a partial solution, extends it, and undoes the
extension on the way back out. The undo is what distinguishes it.

```cpp
void search(int depth) {
    if (depth == n) { record(current); return; }
    for (int choice : options(depth)) {
        if (!valid(choice)) continue;
        current.push_back(choice);      // do
        used[choice] = true;
        search(depth + 1);
        used[choice] = false;           // undo
        current.pop_back();
    }
}
```

The do/undo pair must be exact. Any state modified on the way down and not restored
on the way up leaks into sibling branches, and the resulting bug appears only on the
second and later branches — which is why it survives testing on the first example.

An alternative that removes the risk is passing state by value, at the cost of a
copy per call. For small states that is often the better trade.

## Pruning is what makes it feasible

Unpruned backtracking explores the whole space, which is exponential. The pruning is
not an optimisation; it is usually the difference between running and not.

**Feasibility pruning.** Abandon a branch the moment it cannot lead to a valid
solution — placing a queen on an attacked square, exceeding the capacity.

**Bound pruning.** With an optimisation objective, abandon a branch whose best
conceivable completion is worse than the best solution already found. This requires
a bound that is cheap and never optimistic in the wrong direction.

**Symmetry breaking.** If two branches produce the same solutions up to relabelling,
explore one. Fixing the first element's choice often halves or better.

**Ordering.** Trying the most constrained choice first makes failures happen near the
root, where they cut the most.

## Recursion versus iteration versus DP

If the recursion revisits the same subproblem many times, memoise it — that is
top-down dynamic programming, and it turns exponential into polynomial. The test is
whether the parameters repeat.

If it does not revisit, memoising costs memory and buys nothing, and the question is
just whether recursion or an explicit loop reads better.

If the depth might be large, prefer iteration regardless.

## Generating combinatorial objects

**Subsets** of a set of size n ≤ 20 are most easily enumerated with a bitmask rather
than recursion:

```cpp
for (int mask = 0; mask < (1 << n); ++mask)
    for (int i = 0; i < n; ++i)
        if (mask >> i & 1) { /* element i is in this subset */ }
```

**Permutations** have a standard library answer, which is shorter and less
error-prone than writing the recursion:

```cpp
sort(v.begin(), v.end());
do { /* use v */ } while (next_permutation(v.begin(), v.end()));
```

`next_permutation` requires the range to start sorted, and returns false after
wrapping to the smallest arrangement. There are n! permutations, so this is only
viable to about n = 10.

## Mistakes worth knowing

**A missing or unreachable base case.** Infinite recursion, then a stack overflow.

**Forgetting to undo.** State leaks into sibling branches; the first branch is right
and the rest are wrong.

**Recursing on unchanged state.** No progress, no termination.

**Capturing by reference in a lambda that outlives the frame.** Dangling reference,
undefined behaviour.

**Passing large containers by value in a hot recursion.** Correct but slow, sometimes
dramatically so — the copy happens at every node of the search tree.

**Assuming recursion depth equals input size.** For a balanced tree it is log n; for
a degenerate one it is n. Size for the worst case.
