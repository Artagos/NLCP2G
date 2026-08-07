# Sorting and comparators

## What the standard sort gives you

`std::sort` sorts a range in O(n log n) time. Implementations use introsort — a
quicksort that switches to heapsort when recursion gets too deep — so the O(n²)
worst case of plain quicksort does not occur.

```cpp
sort(a.begin(), a.end());              // ascending, using operator<
sort(a.begin(), a.end(), greater<>()); // descending
```

`std::sort` is **not stable**: equal elements may come out in any relative order.
When the original order of equal elements matters, `std::stable_sort` preserves it,
at the cost of extra memory and a somewhat larger constant.

For the common case of "sort and then look at the k largest", `std::nth_element`
and `std::partial_sort` do less work — O(n) and O(n log k) respectively — because
they do not fully order everything.

## Writing a comparator

A comparator answers one question: does `x` come strictly before `y`?

```cpp
sort(v.begin(), v.end(), [](const Item& x, const Item& y) {
    if (x.score != y.score) return x.score > y.score;  // higher score first
    return x.name < y.name;                            // ties: name ascending
});
```

The pattern of "if the primary keys differ, decide on them; otherwise fall through
to the next key" is the readable way to write multi-key ordering, and it extends to
any number of keys.

For simple lexicographic ordering by several fields, `std::tuple` already does the
right thing and is less error-prone than writing it out:

```cpp
sort(v.begin(), v.end(), [](const Item& x, const Item& y) {
    return tie(x.a, x.b, x.c) < tie(y.a, y.b, y.c);
});
```

## Strict weak ordering, and why violating it crashes

A comparator must define a **strict weak ordering**. The requirement that catches
people is irreflexivity: `cmp(x, x)` must be `false`. It follows that you must use
`<` and not `<=`.

```cpp
// WRONG — returns true for equal elements
sort(v.begin(), v.end(), [](int x, int y) { return x <= y; });
```

This is not a stylistic point. With `<=`, `std::sort` can read past the end of the
array. Its inner loop advances a pointer while the comparator keeps returning true,
and normally an equal element stops it; with `<=` nothing does. The result is
undefined behaviour, which in practice shows up as a segmentation fault on the
judge and often not on your machine.

The comparator must also be transitive and consistent. A comparator built on a
mutable field, or one that consults something that changes during the sort, breaks
both and produces the same class of failure.

## Sorting indices instead of data

When you need the sorted order but must keep the original positions — to report
which item won, or to permute a parallel array — sort an index array:

```cpp
vector<int> idx(n);
iota(idx.begin(), idx.end(), 0);
sort(idx.begin(), idx.end(), [&](int i, int j) { return a[i] < a[j]; });
```

`idx[0]` is now the position of the smallest element. This is also cheaper than
sorting large structs, since only integers move.

## Counting sort and when O(n log n) is not the floor

The n log n bound applies to comparison sorts. When the values are integers in a
small known range, you can beat it by counting occurrences and reading them back
out:

```cpp
vector<int> cnt(MAXV + 1, 0);
for (int x : a) ++cnt[x];
int pos = 0;
for (int v = 0; v <= MAXV; ++v)
    while (cnt[v]--) a[pos++] = v;
```

That is O(n + MAXV). It wins when MAXV is comparable to n and loses badly when the
value range is huge — counting to 10⁹ is not a plan. Radix sort generalises the
idea to larger ranges by sorting digit by digit.

## Common mistakes

**Sorting when the order was the point.** If the problem asks about contiguous
subarrays or about positions, sorting destroys the information the question was
about.

**Sorting inside a loop.** An O(n log n) sort inside an O(n) loop is O(n² log n).
Sorting once outside is nearly always possible and is the fix.

**Assuming stability from `std::sort`.** If your solution depends on equal elements
keeping their input order, use `stable_sort` — the ordinary sort happening to
preserve it on your test data is luck, not a guarantee.

**Comparing floating-point values for equality inside a comparator.** Exact `==`
on doubles that came from arithmetic is unreliable, and a comparator that is
inconsistent because of rounding violates strict weak ordering. Compare with a
tolerance, or sort on an exact integer key.

**Overflow in a subtraction-style comparator.** `return x - y < 0;` looks clever and
breaks when the difference overflows. Write the comparison out as `return x < y;`.
