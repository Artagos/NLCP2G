# Trees and lowest common ancestor

## What makes a tree special

A tree is a connected graph with n vertices and exactly n − 1 edges, and therefore
no cycles. Between any two vertices there is exactly one simple path — which is the
property that makes so many problems tractable on trees and hard on general graphs.

Rooting a tree — picking one vertex as the root — gives every other vertex a unique
parent, and turns questions about paths into questions about ancestry.

## Rooting, depth and subtree sizes

One DFS computes the parent, the depth, and the size of each subtree.

```cpp
void dfs(int u, int p) {
    parent[u] = p;
    size[u] = 1;
    for (int v : adj[u]) {
        if (v == p) continue;              // do not walk back up
        depth[v] = depth[u] + 1;
        dfs(v, u);
        size[u] += size[v];                // accumulate after the child finishes
    }
}
```

The `if (v == p) continue;` is how an undirected adjacency list is traversed as a
rooted tree. Note it fails if the tree has multi-edges; a visited array is the
robust version.

Subtree size accumulates *after* the recursive call, because a child's size is only
known once its own recursion has finished. Anything computed bottom-up follows this
shape; anything top-down (like depth) is set before recursing.

## Euler tour: turning a subtree into a range

Recording the entry and exit time of each vertex during the DFS maps every subtree
onto a contiguous interval.

```cpp
int timer = 0;
void dfs(int u, int p) {
    tin[u] = timer++;
    for (int v : adj[u]) if (v != p) dfs(v, u);
    tout[u] = timer;                       // half-open: subtree is [tin[u], tout[u])
}
```

Vertex `v` is in the subtree of `u` exactly when `tin[u] <= tin[v] < tout[u]`, an
O(1) test. And a query over a subtree becomes a range query over the tour array,
which means a Fenwick tree or a segment tree can answer "sum over this subtree" and
"add to every vertex in this subtree" in O(log n).

This flattening is the single most useful trick for subtree problems.

## Lowest common ancestor

The LCA of two vertices is their deepest common ancestor. It is the meeting point of
the two paths to the root, and it is what makes path queries decomposable: the
distance between `u` and `v` is

```
depth[u] + depth[v] - 2 * depth[lca(u, v)]
```

because the shared part from the root down to the LCA is walked twice and has to be
removed twice.

## Binary lifting

The standard LCA method. Precompute, for each vertex, its 2^k-th ancestor for every
k — a table of size n log n built in O(n log n).

```cpp
const int LOG = 20;                        // 2^20 > 10^6
int up[LOG][MAXN];

// up[0][v] = parent[v], filled by the DFS
for (int k = 1; k < LOG; ++k)
    for (int v = 1; v <= n; ++v)
        up[k][v] = up[k-1][ up[k-1][v] ];  // 2^k = 2^(k-1) twice
```

Answering a query has two phases: lift the deeper vertex to the other's depth, then
lift both together as far as possible without their ancestors coinciding. What
remains is one step below the LCA.

```cpp
int lca(int u, int v) {
    if (depth[u] < depth[v]) swap(u, v);
    int diff = depth[u] - depth[v];
    for (int k = 0; k < LOG; ++k)
        if (diff >> k & 1) u = up[k][u];
    if (u == v) return u;                  // one was an ancestor of the other
    for (int k = LOG - 1; k >= 0; --k)
        if (up[k][u] != up[k][v]) { u = up[k][u]; v = up[k][v]; }
    return up[0][u];
}
```

The second loop counts **downwards**. Descending from the largest jump is what makes
the greedy correct: at each size, jump only if the ancestors still differ, which
keeps both vertices strictly below the LCA throughout.

The `u == v` early return handles the case where one vertex is an ancestor of the
other, which the second loop cannot detect on its own.

Query cost is O(log n), and the table costs O(n log n) memory — at n = 10⁵ and
LOG = 20 that is two million integers, which is fine, but at n = 10⁶ it is worth
checking against the memory limit.

## Other LCA methods

**Euler tour plus sparse table** answers in O(1) after O(n log n) preprocessing, by
reducing LCA to range minimum over the tour. Faster per query, more code.

**Tarjan's offline algorithm** answers all queries in nearly linear time using DSU,
but requires knowing every query in advance.

Binary lifting is usually the right default: it is online, it is short enough to
write from memory, and the same table answers "the k-th ancestor of v" directly,
which problems often want.

## Common tree patterns

**Tree DP.** Compute an answer for each vertex from its children's answers, bottom
up in the same DFS that computes sizes.

**Rerooting.** When the answer is wanted for every possible root, a second pass
propagates the parent's contribution downwards, giving all n answers in O(n) rather
than O(n²).

**Diameter.** The longest path in a tree is found by two BFS runs: from any vertex
to the farthest one, then from there to the farthest again. Note this argument
depends on the graph being a tree and does not hold in general.

## Mistakes worth knowing

**Recursing back into the parent.** Infinite recursion, or a stack overflow reported
as a runtime error.

**Recursive DFS on a path-shaped tree.** 10⁵ nested calls can exceed the stack. Use
an iterative traversal for large n.

**LOG too small.** If 2^LOG is under n, the lifting cannot reach the root and the
answers are quietly wrong for deep trees.

**Forgetting depth in the LCA.** Lifting both vertices by the same amount without
equalising depth first does not converge on the ancestor.
