# Segment tree

## What problem it solves

A segment tree maintains an array under interleaved updates and range queries,
where the query is any **associative** combining operation — sum, minimum, maximum,
greatest common divisor, bitwise or.

Both update and query cost O(log n). The structure is more general than a Fenwick
tree, which is limited to operations that can be undone by subtraction, and that
generality is the reason to accept the extra code and memory.

## The structure

A binary tree over intervals. The root covers the whole array, each internal node
splits its interval in half, and the leaves are single elements. A node's value is
its two children's values combined.

The standard flat layout puts the root at index 1, and node `v`'s children at `2v`
and `2v + 1`. Allocating `4n` entries is the usual safe size — the tree is not
perfectly balanced unless n is a power of two, and `2n` is not always enough.

```cpp
struct SegTree {
    int n;
    vector<long long> t;
    SegTree(int n) : n(n), t(4 * n, 0) {}

    void build(const vector<long long>& a, int v, int lo, int hi) {
        if (lo == hi) { t[v] = a[lo]; return; }
        int mid = (lo + hi) / 2;
        build(a, 2*v, lo, mid);
        build(a, 2*v+1, mid+1, hi);
        t[v] = t[2*v] + t[2*v+1];              // the combine step
    }

    void update(int v, int lo, int hi, int pos, long long val) {
        if (lo == hi) { t[v] = val; return; }
        int mid = (lo + hi) / 2;
        if (pos <= mid) update(2*v, lo, mid, pos, val);
        else            update(2*v+1, mid+1, hi, pos, val);
        t[v] = t[2*v] + t[2*v+1];
    }

    long long query(int v, int lo, int hi, int l, int r) {
        if (r < lo || hi < l) return 0;                    // no overlap: identity
        if (l <= lo && hi <= r) return t[v];               // fully covered
        int mid = (lo + hi) / 2;
        return query(2*v, lo, mid, l, r)
             + query(2*v+1, mid+1, hi, l, r);
    }
};
```

Changing the operation means changing the combine step and the identity returned
for a non-overlapping node. For minimum, combine with `min` and return `LLONG_MAX`;
for maximum, `max` and `LLONG_MIN`. **The identity must be a true identity for the
operation** — returning 0 from a minimum query makes every answer at most zero, and
the bug looks like a logic error somewhere else entirely.

## Why the query is logarithmic

At each level the query interval intersects at most two nodes partially; everything
else is either fully inside (answered immediately) or fully outside (discarded).
With O(log n) levels and O(1) partial nodes per level, the recursion visits O(log n)
nodes.

## Lazy propagation

Updating a whole range one element at a time is O(n log n) per operation and
defeats the point. Lazy propagation defers the work: mark a node as "this whole
interval has a pending change" and only push it down when a query needs to look
inside.

```cpp
void push(int v, int lo, int hi) {
    if (!lazy[v]) return;
    t[v] += lazy[v] * (hi - lo + 1);          // apply to this node
    if (lo != hi) {                            // hand it to the children
        lazy[2*v]   += lazy[v];
        lazy[2*v+1] += lazy[v];
    }
    lazy[v] = 0;
}
```

Every entry point — update and query alike — must `push` before reading a node's
value or descending through it. A single path that reads `t[v]` without pushing
returns a stale answer, and it will do so only on the inputs that happen to exercise
that path.

Range assignment (set every element to a value) and range addition compose
differently, and a structure supporting both needs a defined order for combining
pending operations. Getting that composition right is the hard part of lazy
propagation.

## The iterative form

For point update and range query without lazy propagation, a bottom-up iterative
version is shorter and faster:

```cpp
void update(int i, long long val) {           // 0-based
    for (t[i += n] = val; i > 1; i >>= 1)
        t[i>>1] = t[i] + t[i^1];
}
long long query(int l, int r) {               // half-open [l, r)
    long long res = 0;
    for (l += n, r += n; l < r; l >>= 1, r >>= 1) {
        if (l & 1) res += t[l++];
        if (r & 1) res += t[--r];
    }
    return res;
}
```

This uses exactly `2n` memory and no recursion. It takes a half-open range, unlike
the recursive version above, and mixing the two conventions in one solution is a
reliable way to produce off-by-one errors.

## Segment tree versus Fenwick tree

A Fenwick tree is about ten lines, uses n + 1 memory, and has a smaller constant. It
answers prefix sums by subtraction, which restricts it to invertible operations —
sums, counts, XOR.

A segment tree costs more code and memory but handles minimum, maximum, gcd,
assignment over ranges, and anything else associative. If the query is a sum with
point updates, prefer the Fenwick tree; the moment it is a minimum or a range
update, the segment tree is the structure that can express it at all.

## Mistakes worth knowing

**Allocating `2n` for the recursive version.** Out-of-bounds writes for n that is
not a power of two. Use `4n`.

**A wrong identity for the non-overlapping case.** Silent, and looks like anything
but what it is.

**Forgetting to push before descending, with lazy propagation.** Stale reads on some
paths only.

**Recomputing the parent before recursing rather than after.** The child's new value
has to exist before the parent combines it.

**Mixing inclusive and half-open conventions** between the recursive and iterative
forms, or between build and query.
