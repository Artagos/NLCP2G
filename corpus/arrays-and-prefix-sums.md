# Arrays and prefix sums

## What a prefix sum is

A prefix sum array stores, at each position, the total of everything up to that
position. Given `a = [3, 1, 4, 1, 5]`, the prefix sums are
`p = [0, 3, 4, 8, 9, 14]` — one entry longer than the original, with `p[i]` holding
the sum of the first `i` elements.

That extra leading zero is not decoration. It is what makes the query formula work
without a special case for ranges that start at the beginning.

The point of the structure is a trade: you spend O(n) time and O(n) memory once,
and afterwards any range total costs O(1) instead of O(length). If you answer q
range queries, you go from O(nq) to O(n + q).

## Building and querying

Building is a single pass, each entry the previous one plus the next element:

```cpp
vector<long long> p(n + 1, 0);
for (int i = 0; i < n; ++i)
    p[i + 1] = p[i] + a[i];
```

The sum of the half-open range `[l, r)` is then `p[r] - p[l]`. Everything up to
`r` minus everything up to `l` leaves exactly the middle.

Note the accumulator type. If the values are up to 10⁶ and there are up to 2 × 10⁵
of them, the total reaches 2 × 10¹¹, which overflows a 32-bit `int` and silently
produces nonsense. Prefix sums are one of the most common places for that bug,
because each individual value looks small.

## Off-by-one and the 1-based convention

Most range-query problems state their ranges as inclusive and 1-based: "positions
l to r, counting from 1". The prefix array above is 0-based and half-open. Mixing
the two conventions is the single most common source of wrong answers here.

Two safe translations, given inclusive 1-based `l` and `r`:

- With the array above, the answer is `p[r] - p[l - 1]`.
- Or convert first: the half-open 0-based range is `[l - 1, r)`.

Pick one convention, write it down at the top of the function, and do not switch
midway. Checking the formula against a two-element example by hand costs ten
seconds and catches almost every version of this mistake.

## Difference arrays: range update, point query

A prefix sum answers range queries over a fixed array. Its mirror image, the
difference array, handles the opposite job: many range *updates*, then reading the
final values.

To add `v` to every element of `[l, r]`, record only the two endpoints:

```cpp
d[l]     += v;
d[r + 1] -= v;
```

After all updates, one prefix-sum pass over `d` produces the final array. Each
update is O(1) instead of O(length), so m updates cost O(n + m) instead of O(nm).

Sizing `d` to `n + 1` rather than `n` matters, so that `d[r + 1]` is a legal write
when `r` is the last index.

## Two dimensions

The same idea extends to a grid. `P[i][j]` holds the sum of the rectangle from the
origin to `(i, j)`, built with:

```cpp
P[i][j] = a[i-1][j-1] + P[i-1][j] + P[i][j-1] - P[i-1][j-1];
```

The subtraction is inclusion-exclusion: the two overlapping rectangles both contain
the top-left corner region, so it gets counted twice and has to come off once.

Querying a rectangle uses the same correction in reverse, with four lookups and
three arithmetic operations, independent of the rectangle's size.

## When prefix sums do not apply

Prefix sums work because addition has an inverse: knowing the total up to `r` and
the total up to `l` is enough to recover the middle, by subtracting. Operations
without an inverse do not admit this trick.

Range minimum is the standard example. Knowing the minimum of the first `r`
elements and the minimum of the first `l` tells you nothing about the minimum
between them — the small value might be in the prefix you removed. Range minimum
needs a sparse table, a segment tree, or a different approach entirely.

The other limitation is mutability. A prefix sum array is built once and is only
correct for the array as it was at that moment. A single update to the underlying
data invalidates every entry after it, costing O(n) to repair. If updates and
queries are interleaved, you want a Fenwick tree or a segment tree instead.
