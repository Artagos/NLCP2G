# Binary search

## The invariant that makes it work

Binary search needs one property and one only: the thing you are searching must be
**monotonic**. Somewhere there is a dividing line, everything on one side of it
answers "no" and everything on the other answers "yes", and the job is to find the
line.

For a sorted array the monotonic property is "is this element at least x?" — false
for a while, then true forever after. Sortedness is a common way to get
monotonicity, but it is not the requirement itself, which is why binary search
applies to plenty of things that are not arrays.

Each step halves the interval still under consideration, so the work is O(log n).
On a million elements that is twenty comparisons.

## Searching a sorted array

The half-open form, searching for the first index whose value is at least `x`:

```cpp
int lo = 0, hi = n;              // answer lies in [lo, hi]
while (lo < hi) {
    int mid = lo + (hi - lo) / 2;
    if (a[mid] >= x) hi = mid;   // mid might be the answer, keep it
    else             lo = mid + 1;
}
// lo == hi == first index with a[lo] >= x, or n if none
```

`lo + (hi - lo) / 2` rather than `(lo + hi) / 2` avoids overflow when both bounds
are large. It costs nothing and removes a whole class of bug.

The loop maintains the invariant that the answer is always inside `[lo, hi]`, and
narrows it until only one position remains. Writing the invariant in a comment
before writing the loop is the most reliable way to get the boundaries right.

## lower_bound and upper_bound

The C++ standard library provides both, and the difference between them catches
people constantly.

- `std::lower_bound(first, last, x)` returns an iterator to the **first element
  not less than x** — that is, the first `>= x`.
- `std::upper_bound(first, last, x)` returns an iterator to the **first element
  strictly greater than x** — the first `> x`.

So for `a = [1, 2, 2, 2, 5]` and `x = 2`, `lower_bound` points at index 1 and
`upper_bound` points at index 4. The distance between them is the number of copies
of `x`, which is the usual way to count occurrences.

Both return `last` when no such element exists, and neither tells you directly
whether `x` is present. The membership test is:

```cpp
auto it = lower_bound(a.begin(), a.end(), x);
bool found = (it != a.end() && *it == x);
```

Both require the range to be sorted with respect to the same comparator you pass.
On an unsorted range they do not report an error; they return a meaningless
position.

One performance trap: `lower_bound` on a `std::set` or `std::map` should be called
as the member function `s.lower_bound(x)`, not the free function. The free function
works on any forward iterator but degrades to O(n) on a node-based container
because it cannot jump; the member version uses the tree and stays O(log n).

## Binary searching the answer

The more powerful use is not searching a container at all. When a problem asks for
the smallest (or largest) value satisfying some condition, and the condition is
monotonic in that value, you can binary search over the answer space directly.

The shape is always the same: write a predicate `feasible(x)` that returns whether
`x` works, confirm it is monotonic — once it becomes true it stays true — then
binary search the range of candidate answers.

```cpp
long long lo = 0, hi = LIMIT;
while (lo < hi) {
    long long mid = lo + (hi - lo) / 2;
    if (feasible(mid)) hi = mid;
    else               lo = mid + 1;
}
```

The total cost is O(log(range) × cost of one feasibility check). The check is
usually a simple linear pass, which is why this turns many hard-looking
optimisation questions into easy ones.

The part that requires care is proving monotonicity. If `feasible` can go true,
then false, then true again, binary search will land on one of the boundaries
arbitrarily and the result is meaningless.

## Floating-point binary search

When the answer is a real number, the loop cannot terminate by exhausting integers.
Two options: iterate a fixed number of times, or loop until the interval is small
enough.

A fixed iteration count is the safer of the two. A hundred iterations halves the
interval by a factor of 2¹⁰⁰, which is far beyond double precision, and it cannot
loop forever:

```cpp
double lo = 0, hi = 1e9;
for (int iter = 0; iter < 100; ++iter) {
    double mid = (lo + hi) / 2;
    if (feasible(mid)) hi = mid;
    else               lo = mid;
}
```

Terminating on `hi - lo > 1e-9` looks tidier but can spin forever when the values
are large enough that adding the epsilon does not change the floating-point
representation.

## Mistakes that cause infinite loops

**Updating a bound to `mid` on both branches.** In the integer form, if
`feasible(mid)` is false and you set `lo = mid` rather than `lo = mid + 1`, then
when `hi == lo + 1` the midpoint equals `lo` and nothing changes. The loop spins.

**Mixing half-open and closed intervals.** With `hi = n - 1` and `while (lo < hi)`
the last element is never examined. With `hi = n` and `a[hi]` accessed anywhere,
you read out of bounds. Choose one and keep it.

**Searching unsorted data.** Binary search will happily return a wrong answer
rather than complain. If the input is not guaranteed sorted, sort it first, and
remember that costs O(n log n) — which may make a single linear scan the better
choice if you only need one query.

**A predicate with side effects.** `feasible` gets called O(log range) times with
values that are not the final answer. If it mutates shared state, the state is
wrong by the end.
