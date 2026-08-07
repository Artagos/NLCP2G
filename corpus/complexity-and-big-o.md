# Complexity and big-O notation

## What big-O actually measures

Big-O describes how the amount of work a program does grows as its input grows.
It is deliberately not a measure of seconds. Two programs that are both O(n) can
differ by a factor of fifty in wall-clock time; what O(n) promises is that if you
double the input, each of them roughly doubles its own running time.

The notation drops constants and lower-order terms, because those stop mattering
as n gets large. A routine doing `3n + 200` steps is O(n). One doing
`n² / 2 + 1000n` is O(n²) — the quadratic term wins eventually, no matter how
generous the constants attached to the linear one.

The usual reading is "worst case, as n grows without bound". When someone says an
algorithm "is O(n log n)", they mean there is some input size beyond which the
step count never exceeds a constant multiple of n log n.

## The growth rates you will meet

Ordered from cheapest to most expensive, with a feel for how each behaves when the
input gets ten times bigger:

| Notation | Name | n → 10n means |
|---|---|---|
| O(1) | constant | unchanged |
| O(log n) | logarithmic | about 3 more steps |
| O(n) | linear | 10× the work |
| O(n log n) | linearithmic | a bit over 10× |
| O(n²) | quadratic | 100× the work |
| O(n³) | cubic | 1000× the work |
| O(2ⁿ) | exponential | hopeless past small n |
| O(n!) | factorial | hopeless past tiny n |

The gap between O(n²) and O(n log n) is the one that decides most outcomes. At
n = 200 000 the quadratic version does forty billion steps and the linearithmic
version does about three and a half million.

## How many operations fit in a second

A useful rule of thumb: a modern judge machine runs on the order of 10⁸ simple
operations per second. "Simple" means an addition, a comparison, an array index —
not a hash lookup, not a memory allocation, not a division.

Working backwards from a two-second limit gives a rough sense of what complexity a
constraint is asking for:

| If n is up to | You probably need |
|---|---|
| 10 | anything, even O(n!) |
| 20 | O(2ⁿ) |
| 500 | O(n³) |
| 5 000 | O(n²) |
| 10⁶ | O(n log n) |
| 10⁸ | O(n) |

This is a rule of thumb and not a law. Constant factors matter: an O(n log n)
algorithm built out of `std::map` operations can lose to an O(n²) algorithm built
out of tight array scans, at moderate n, because each map operation costs a
pointer chase and a cache miss.

## Amortised, average, and worst case

Three different claims that are easy to confuse.

**Worst case** is a guarantee: no input does worse than this. Binary search is
worst-case O(log n).

**Average case** is a claim about a distribution of inputs. Quicksort is
average-case O(n log n) but worst-case O(n²), and which one you get depends on the
data and the pivot rule.

**Amortised** is a claim about a sequence of operations: any single one may be
expensive, but the total across many is bounded. Appending to a dynamic array is
amortised O(1) — most appends are a single write, and the occasional reallocation
that copies everything is paid for by all the cheap appends before it. Amortised
is a real guarantee, unlike average case; it does not depend on the input being
typical.

## Space complexity counts too

The same notation applies to memory. An algorithm that builds an n × n table is
O(n²) space, and at n = 10 000 that is 10⁸ cells — hundreds of megabytes, past
most memory limits, even if the time complexity is fine.

Recursion consumes space that is easy to forget: each pending call keeps its local
variables and return address on the call stack. A recursion n levels deep is O(n)
space, and a deep enough one overflows the stack and crashes rather than merely
running slowly.

## Common mistakes reading complexity

**Forgetting what n is.** With q queries over an array of n elements, "O(n) per
query" is O(nq) overall. Those are different numbers and the second one is the one
that decides whether it fits.

**Counting the loop and not the body.** A loop that runs n times, each iteration
doing a linear scan or a string copy, is O(n²), not O(n). Any call inside the loop
contributes its own cost.

**Assuming a library call is free.** Inserting into a balanced tree is O(log n);
sorting is O(n log n); building a string by repeated concatenation can be
quadratic. The cost is there whether or not you wrote the loop yourself.

**Treating O as exact.** O is an upper bound. Saying an algorithm is O(n²) is still
true if it is really O(n) — it is just uninformative. When people want to say a
bound is tight they say Θ (theta), though in practice most write O and mean Θ.
