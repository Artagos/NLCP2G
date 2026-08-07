# Fenwick tree (binary indexed tree, BIT)

## What problem it solves

A Fenwick tree — also called a binary indexed tree, or BIT — maintains an array
under two interleaved operations:

- **point update**: add a value to one position
- **prefix query**: total of everything up to a position

Both cost O(log n). A plain array does the update in O(1) and the query in O(n); a
prefix-sum array reverses that. The Fenwick tree is the compromise that makes both
cheap, which is what you need when updates and queries arrive mixed together.

Range sums follow immediately: the sum over `[l, r]` is `query(r) - query(l - 1)`.

## The structure

One array, same length as the data plus one. Position `i` is responsible for a
block of entries ending at `i`, whose length is the lowest set bit of `i`.

That expression, `i & -i`, is the whole of the cleverness. It isolates the lowest
set bit, using two's complement: `-i` is `~i + 1`, so every bit below the lowest set
bit is zero in both, the lowest set bit is one in both, and everything above
differs.

```cpp
struct Fenwick {
    int n;
    vector<long long> t;
    Fenwick(int n) : n(n), t(n + 1, 0) {}

    void add(int i, long long v) {          // 1-based index
        for (; i <= n; i += i & -i) t[i] += v;
    }

    long long query(int i) {                // prefix sum of [1, i]
        long long s = 0;
        for (; i > 0; i -= i & -i) s += t[i];
        return s;
    }

    long long range(int l, int r) {         // inclusive
        return query(r) - query(l - 1);
    }
};
```

**A Fenwick tree is 1-based and not optionally so.** Index 0 would make `i & -i`
zero and the update loop would never advance. Convert 0-based input on the way in.

## Why the loops are logarithmic

Each step of `add` clears nothing and adds the lowest set bit, which carries and
strictly increases the value; each step of `query` removes the lowest set bit,
strictly decreasing the number of set bits. Either way you touch at most as many
positions as there are bits in n — about 20 for n = 10⁶.

## Building it

Adding n elements one at a time is O(n log n). When the initial values are known up
front, there is an O(n) construction: write the values in directly, then push each
position's total into its parent.

```cpp
for (int i = 1; i <= n; ++i) t[i] += a[i];
for (int i = 1; i <= n; ++i) {
    int j = i + (i & -i);
    if (j <= n) t[j] += t[i];
}
```

Rarely the bottleneck, but free.

## Range update, point query

Flip the roles by storing a difference array in the Fenwick tree. To add `v` across
`[l, r]`, do `add(l, v)` and `add(r + 1, -v)`; the value at position `i` is then
`query(i)`.

Supporting range update *and* range query needs two Fenwick trees and a bit of
algebra. At that point a segment tree with lazy propagation is usually the clearer
choice.

## Fenwick versus segment tree

They overlap heavily, and the honest summary is that a segment tree does everything
a Fenwick tree does and more, while a Fenwick tree is smaller, faster by a constant
factor, and much shorter to write.

| | Fenwick | Segment tree |
|---|---|---|
| Lines of code | ~10 | ~40, more with lazy |
| Memory | n + 1 | 2n to 4n |
| Constant factor | smaller | larger |
| Prefix / range sum | yes | yes |
| Range min or max | no | yes |
| Range assignment, range add | awkward | yes, with lazy propagation |
| Descend to find a position | yes, in O(log n) | yes |

The decisive question is the operation. Fenwick trees rely on being able to
*subtract*: a range is a prefix minus a prefix. Minimum has no inverse, so a Fenwick
tree cannot do range minimum, and that is the boundary between the two.

Choose a Fenwick tree for sums, counts and XOR when the updates are point updates.
Reach for a segment tree the moment you need min, max, assignment over a range, or
anything else that will not subtract.

## What it is used for

**Counting inversions.** Sweep left to right, and for each element count how many
already-seen values exceed it, with a Fenwick tree over value space. O(n log n).

**Order statistics over a value range.** With a tree over compressed values,
`query` counts how many elements are at most x, and a binary descent finds the k-th
smallest in O(log n).

**Any running count keyed by an integer** where the totals are asked for as you go.

## Mistakes worth knowing

**Passing a 0-based index.** `add(0, v)` loops forever or does nothing depending on
the loop form. Add one at the boundary.

**Sizing the tree to n rather than n + 1.** The last update writes out of bounds.

**Overflowing the accumulator.** The tree stores partial sums, which are as large
as the total. Use `long long` unless you have checked otherwise.

**Reusing the tree across test cases without clearing.** Old counts silently add to
the new case's answers.
