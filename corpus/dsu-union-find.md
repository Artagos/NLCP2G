# Disjoint set union (DSU / union-find)

## What it is for

A disjoint set union structure — DSU, also called union-find — maintains a
collection of elements partitioned into groups, and supports two operations:

- `find(x)`: which group is `x` in?
- `unite(x, y)`: merge the groups containing `x` and `y`.

Both run in effectively constant time once the two standard optimisations are in
place. The structure answers connectivity questions under a stream of merges, which
is exactly the shape of problems about components, grouping, and "are these two
things related yet".

What it cannot do is split a group. DSU is a one-way structure: things merge and
never come apart. Problems involving deletions usually need the edges processed in
reverse, or a different structure entirely.

## The representation

Each group is a tree, and the group is identified by its root. The whole structure
is one array holding each element's parent, with roots pointing at themselves.

```cpp
vector<int> parent(n), rank_(n, 0);
iota(parent.begin(), parent.end(), 0);   // everyone is their own root

int find(int x) {
    while (parent[x] != x) x = parent[x];
    return x;
}
```

Two elements are in the same group exactly when `find` returns the same root for
both. Without optimisations these trees can degenerate into chains and `find`
becomes O(n), so both optimisations below are effectively mandatory.

## Path compression

After `find` walks to the root, it points every node it passed at the root
directly, so the next query on any of them is immediate.

```cpp
int find(int x) {
    if (parent[x] != x) parent[x] = find(parent[x]);   // recursive, compressing
    return parent[x];
}
```

The iterative version avoids deep recursion on adversarial input:

```cpp
int find(int x) {
    int root = x;
    while (parent[root] != root) root = parent[root];
    while (parent[x] != root) { int next = parent[x]; parent[x] = root; x = next; }
    return root;
}
```

Path compression mutates the structure during a read, which is why `find` cannot be
marked const and why it must not be called concurrently.

## Union by rank or by size

When merging two trees, attach the shorter one under the taller. Left to itself,
merging arbitrarily can build a chain; attaching the smaller tree keeps depth
logarithmic even before compression.

```cpp
bool unite(int a, int b) {
    a = find(a); b = find(b);
    if (a == b) return false;              // already together
    if (rank_[a] < rank_[b]) swap(a, b);
    parent[b] = a;
    if (rank_[a] == rank_[b]) ++rank_[a];
    return true;
}
```

Union by size — tracking the element count and attaching the smaller — works
equally well and has the advantage that the size of each component is available for
free, which problems often ask for.

Returning whether the merge did anything is a small convenience that turns out to
matter: it is how Kruskal's algorithm decides whether an edge joins two components
or closes a cycle.

## The complexity, honestly stated

With both optimisations, m operations on n elements cost O(m · α(n)), where α is
the inverse Ackermann function. α(n) is at most 4 for any n that fits in the
universe, so the practical claim is "effectively constant per operation" — but it is
not literally O(1), and the amortised bound is over a sequence, not per call.

With only one of the two optimisations you get O(log n) per operation, which is
usually still fine. With neither, O(n).

## What DSU is good for

**Counting connected components.** Start with n components and decrement each time
a `unite` returns true.

**Kruskal's minimum spanning tree.** Sort edges by weight, add an edge whenever it
joins two different components. DSU is what makes the cycle test cheap.

**Grouping by equivalence.** Any problem where "a is equivalent to b" arrives as a
stream of pairs and you need the resulting classes.

**Offline connectivity with deletions.** Process the queries in reverse, so
deletions become insertions.

## Mistakes worth knowing

**Comparing elements instead of roots.** `if (parent[a] == parent[b])` is not the
same as `if (find(a) == find(b))` and is wrong whenever the trees are more than one
level deep.

**Uniting without finding first.** `parent[b] = a` with raw arguments attaches a
node rather than a tree, and silently loses everything under `b`'s real root.

**Caching a root across merges.** A root stops being a root as soon as its tree is
attached under another. Call `find` again after any `unite`.

**Forgetting to size the arrays for 1-based input.** If the vertices are numbered 1
to n, the arrays need n + 1 entries.
