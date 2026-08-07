# Shortest paths in weighted graphs

## Picking the right algorithm

The weights decide the algorithm, and getting this wrong is the usual cause of a
shortest-path solution being both elegant and incorrect.

| Situation | Algorithm | Cost |
|---|---|---|
| All edges weight 1 | BFS | O(n + m) |
| All edges weight 0 or 1 | 0-1 BFS with a deque | O(n + m) |
| Non-negative weights, one source | Dijkstra with a heap | O(m log n) |
| Negative weights allowed, one source | Bellman-Ford | O(nm) |
| All pairs, small n | Floyd-Warshall | O(n³) |
| Weights from a tiny set of values | Dial's algorithm / bucket queue | O(n + mC) |

## Dijkstra's algorithm

Dijkstra grows a set of vertices whose shortest distance is finalised, always
finalising the closest unfinalised vertex next. The priority queue supplies that
"closest next" efficiently.

```cpp
vector<long long> dist(n + 1, LLONG_MAX);
priority_queue<pair<long long,int>,
               vector<pair<long long,int>>,
               greater<>> pq;             // MIN-heap: smallest distance on top
dist[s] = 0;
pq.push({0, s});
while (!pq.empty()) {
    auto [d, u] = pq.top(); pq.pop();
    if (d > dist[u]) continue;            // stale entry, already improved
    for (auto [v, w] : adj[u]) {
        if (dist[u] + w < dist[v]) {
            dist[v] = dist[u] + w;
            pq.push({dist[v], v});
        }
    }
}
```

Three details that are all load-bearing:

**The heap must be a min-heap.** `std::priority_queue` defaults to a max-heap, so
the `greater<>` comparator is required. Omitting it produces a program that runs and
returns wrong numbers.

**The staleness check.** There is no way to decrease a key in a `priority_queue`, so
improved distances are pushed as new entries and the old ones linger. `if (d >
dist[u]) continue;` discards them. Without it the algorithm still terminates and
still gets the right answer, but reprocesses vertices and can be much slower.

**The distance type.** Summing up to 10⁵ edges of weight up to 10⁹ reaches 10¹⁴,
well past a 32-bit `int`. Use `long long`, and be careful that `LLONG_MAX + w`
overflows — hence testing `dist[u] + w < dist[v]` only after `dist[u]` is known
finite, or using a large sentinel like `4e18` rather than the true maximum.

## Why Dijkstra needs non-negative weights

The algorithm finalises a vertex the moment it is the closest unfinalised one, and
never revisits it. That is only sound if no later-discovered path can be shorter —
which requires that extending a path never decreases its length.

A single negative edge breaks the argument: a longer-looking route might drop below
the finalised distance afterwards. Dijkstra will not notice, and returns a wrong
answer without any sign of trouble.

## 0-1 BFS

When every weight is 0 or 1, a deque replaces the heap and the log factor
disappears. A zero-weight edge goes to the **front** of the deque, a weight-one edge
to the back — which keeps the deque sorted by distance with at most two distinct
values in it at any time.

```cpp
deque<int> dq;
dq.push_front(s);
while (!dq.empty()) {
    int u = dq.front(); dq.pop_front();
    if (done[u]) continue;
    done[u] = true;
    for (auto [v, w] : adj[u]) {
        if (dist[u] + w < dist[v]) {
            dist[v] = dist[u] + w;
            if (w == 0) dq.push_front(v);
            else        dq.push_back(v);
        }
    }
}
```

This is the standard technique for grid problems where some moves are free and
others cost one.

## Bellman-Ford and negative cycles

Bellman-Ford relaxes every edge, n − 1 times over. After k rounds, every shortest
path using at most k edges is correct; since a simple path has at most n − 1 edges,
n − 1 rounds suffice.

```cpp
for (int round = 0; round < n - 1; ++round)
    for (auto [u, v, w] : edges)
        if (dist[u] < INF && dist[u] + w < dist[v])
            dist[v] = dist[u] + w;
```

Running one extra round is the negative-cycle test: if anything still improves, no
shortest path exists, because going round the cycle again always helps.

At O(nm) it is much slower than Dijkstra and is only worth it when negative weights
are genuinely present.

## Floyd-Warshall

For all-pairs distances on a small graph, three nested loops over an n × n matrix:

```cpp
for (int k = 1; k <= n; ++k)
    for (int i = 1; i <= n; ++i)
        for (int j = 1; j <= n; ++j)
            if (d[i][k] + d[k][j] < d[i][j])
                d[i][j] = d[i][k] + d[k][j];
```

**The `k` loop must be outermost.** The invariant is "after iteration k, `d[i][j]`
is the best path using only intermediate vertices from the first k". Reordering the
loops breaks the invariant and gives wrong answers on some graphs and right ones on
others, which makes it a miserable bug to find.

O(n³) means n up to roughly 400–500 within a typical limit. It handles negative
edges, and a negative value on the diagonal afterwards indicates a negative cycle.

## Reconstructing the path

Distances alone rarely satisfy the question. Record, for each vertex, the
predecessor that gave it its best distance:

```cpp
if (dist[u] + w < dist[v]) { dist[v] = dist[u] + w; parent[v] = u; }
```

Then walk `parent` backwards from the target and reverse. An unreachable target is
one whose distance is still the sentinel — check that before walking, or the loop
never terminates.
