# Graph traversal: BFS and DFS

## Representing a graph

The adjacency list is the default: for each vertex, the list of its neighbours.

```cpp
vector<vector<int>> adj(n + 1);          // 1-based vertices
for (int i = 0; i < m; ++i) {
    int u, v; cin >> u >> v;
    adj[u].push_back(v);
    adj[v].push_back(u);                 // omit this line for a directed graph
}
```

Space is O(n + m) and listing a vertex's neighbours is proportional to how many it
has. An adjacency matrix costs O(n²) space and only makes sense for dense graphs or
when you need O(1) edge existence tests; at n = 10⁵ a matrix is 10¹⁰ cells and is
simply not an option.

## Breadth-first search

BFS explores in rings: all vertices at distance 1, then all at distance 2, and so
on. That ordering is its defining property, and it is what makes BFS compute
shortest paths **when every edge has the same weight**.

```cpp
vector<int> dist(n + 1, -1);
queue<int> q;
dist[start] = 0;
q.push(start);
while (!q.empty()) {
    int u = q.front(); q.pop();
    for (int v : adj[u]) {
        if (dist[v] == -1) {             // not yet reached
            dist[v] = dist[u] + 1;
            q.push(v);
        }
    }
}
```

The critical detail is *when* a vertex is marked. `dist[v]` is set at the moment `v`
is pushed, not when it is popped. Marking at pop time lets a vertex be pushed many
times before it is first processed, and the queue blows up.

Using `dist[v] == -1` as the visited test does double duty and removes the risk of
the two falling out of sync.

Total cost is O(n + m): every vertex enters the queue at most once, and every edge
is examined at most twice.

## Depth-first search

DFS follows one path as far as it goes before backtracking. It does not produce
shortest paths, and it is not trying to.

```cpp
vector<bool> seen(n + 1, false);
void dfs(int u) {
    seen[u] = true;
    for (int v : adj[u])
        if (!seen[v]) dfs(v);
}
```

What DFS gives you instead is structure: the order in which vertices are entered
and left. Entry and exit times are what cycle detection, topological ordering,
bridge and articulation point algorithms, and strongly connected component
algorithms are all built on.

**Recursion depth is a real constraint.** A path graph with 10⁵ vertices means 10⁵
nested calls, and the default stack on many judges cannot take it. The failure mode
is a crash, reported as a runtime error rather than as a wrong answer. The fix is an
explicit stack:

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

Note this visits in a different order than the recursive version, which matters if
you depend on entry and exit times.

## Choosing between them

| You need | Use |
|---|---|
| Shortest path, all edges weight 1 | BFS |
| Shortest path, edge weights 0 or 1 | 0-1 BFS (deque) |
| Shortest path, general positive weights | Dijkstra — not BFS |
| Connected components | either |
| Cycle detection | DFS |
| Topological order | DFS, or Kahn's algorithm with a queue |
| Anything about entry / exit times | DFS |

The row that catches people is the third. BFS finds the path with the fewest
*edges*, which is only the cheapest path when all edges cost the same.

## Multi-source BFS

To find each vertex's distance to the *nearest* member of a set, push every member
of that set at distance 0 before starting the loop. The rest of the algorithm is
unchanged, and it still costs O(n + m) — not one BFS per source.

This is the standard answer to "distance from every cell to the closest X" on a
grid, where running a separate search from each X would be far too slow.

## Grids as graphs

A grid is a graph whose vertices are cells and whose edges join neighbours. There
is no need to build an adjacency list; generate the neighbours arithmetically.

```cpp
const int dr[] = {-1, 1, 0, 0};
const int dc[] = {0, 0, -1, 1};
for (int k = 0; k < 4; ++k) {
    int nr = r + dr[k], nc = c + dc[k];
    if (nr < 0 || nr >= rows || nc < 0 || nc >= cols) continue;
    // ...
}
```

Bounds-check before indexing, not after. Reading `grid[-1][0]` is undefined
behaviour, and on a flattened array it silently reads the wrong cell rather than
crashing.

## Mistakes worth knowing

**Marking visited at pop rather than push in BFS.** Correct answers, but the queue
can hold O(m) copies and the memory limit fails.

**A visited array that is not reset between test cases.** With multiple test cases
in one run, stale marks make later cases wrong. Reset only the touched vertices, or
use a timestamp array, rather than clearing an array of size n each time.

**Forgetting the reverse edge on an undirected graph.** The traversal quietly
explores less than it should.

**Using BFS on weighted edges.** It returns fewest-edges, which is not
lowest-total-weight, and the answer is wrong without ever looking wrong.
