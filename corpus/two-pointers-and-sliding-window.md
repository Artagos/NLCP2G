# Two pointers and sliding windows

## The shape of a two-pointer scan

A two-pointer scan keeps two indices into a sequence and moves each of them forward
only. Because neither ever goes backwards, the total number of moves is at most
2n however the two interleave, and the whole scan is O(n) — even though it looks,
from the nesting of the loops, as if it might be quadratic.

That is the entire trick: replacing a scan that restarts (O(n²)) with one where
both ends only advance (O(n)).

The two common arrangements are opposite ends moving towards each other, and both
pointers moving left to right with one trailing the other. The second one is what
people usually mean by a sliding window.

## Opposite ends

With a sorted array, looking for a pair summing to a target:

```cpp
int l = 0, r = n - 1;
while (l < r) {
    long long s = a[l] + a[r];
    if (s == target) { /* found */ break; }
    if (s < target) ++l;   // need more, smallest can only grow
    else            --r;   // need less, largest can only shrink
}
```

The correctness argument is worth internalising, because it generalises. When the
sum is too small, no pair using `a[l]` can work — `a[r]` is already the largest
partner available, so `a[l]` is eliminated entirely and moving `l` loses nothing.
The symmetric argument covers the other branch.

Notice this requires sorted input. If the array is not sorted, sorting first costs
O(n log n) and dominates, which may still beat the O(n²) alternative.

## Fixed-size windows

When the window has a known length k, the update is mechanical: add the element
entering on the right, remove the one leaving on the left.

```cpp
long long sum = 0;
for (int i = 0; i < n; ++i) {
    sum += a[i];
    if (i >= k) sum -= a[i - k];
    if (i >= k - 1) { /* window [i-k+1, i] is complete, sum is its total */ }
}
```

The two guards are different on purpose. `i >= k` controls the removal, `i >= k-1`
controls when a full window first exists. Conflating them is the standard off-by-one
here.

## Variable-size windows

The more general form grows the window on the right and shrinks it from the left
whenever some condition is violated. The canonical example is the longest run
containing no repeated element:

```cpp
int best = 0, l = 0;
unordered_map<int,int> count;
for (int r = 0; r < n; ++r) {
    ++count[a[r]];
    while (count[a[r]] > 1) {      // shrink until the invariant holds again
        --count[a[l]];
        ++l;
    }
    best = max(best, r - l + 1);
}
```

The inner `while` looks like it makes this quadratic. It does not: `l` only ever
increases, and it can increase at most n times across the whole run, so the inner
loop's total work over all iterations is O(n).

This is the pattern to reach for when a problem asks for the longest or shortest
contiguous stretch satisfying a property — provided the property is monotone in the
window, meaning that if a window fails, every larger window containing it also
fails.

## Why it is linear

The formal argument is a potential or accounting argument. Assign each pointer a
counter that only increases and is bounded by n. Every iteration of every loop
advances at least one counter. Therefore the total number of iterations across the
entire algorithm is at most 2n, regardless of how the loops nest in the source.

This is why you cannot judge the complexity of a two-pointer scan by counting
nested loops. The nesting is real, but the inner loop's iteration count is shared
across the whole run rather than restarting each time.

## When two pointers is wrong

**The property is not monotone.** If shrinking the window can turn a failing window
into another failing window and then a passing one, the "shrink until valid" step
can skip the answer. Sliding windows require that once a window is invalid, every
extension of it stays invalid.

**Elements can be negative and you are summing.** "Shortest subarray with sum at
least S" is a clean sliding window with non-negative values, because extending the
window can only increase the sum. With negatives, extending can decrease it, the
monotonicity is gone, and the technique breaks. Prefix sums plus a monotonic deque
or a balanced structure is the usual replacement.

**The order matters and you sorted.** Sorting is what makes the opposite-ends form
work, but it destroys positions. If the problem asks about contiguous ranges of the
original array, you cannot sort first.

**Both pointers need to move backwards.** If the algorithm ever needs to reconsider
a position it has passed, the linear bound is void and the approach is not
applicable.
