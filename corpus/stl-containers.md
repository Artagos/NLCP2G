# Choosing an STL container

## vector: the default

`std::vector` is a contiguous, growable array. It should be the first thing you
reach for, and it is the right answer far more often than its competitors.

- Indexing: O(1)
- `push_back` / `pop_back`: amortised O(1)
- Insert or erase in the middle: O(n)
- Memory: contiguous, so iteration is cache-friendly

The contiguity is why it wins in practice. Walking a vector reads consecutive
memory; walking a linked structure of the same length chases pointers all over the
heap and can be an order of magnitude slower at the same asymptotic complexity.

`reserve(n)` up front when the size is known avoids the reallocation-and-copy cycle
as it grows. Note that `reserve` changes capacity, not size — the vector is still
empty afterwards. `resize(n)` changes size and creates elements.

`std::vector<bool>` is a special case and a trap: it is a bit-packed specialisation,
not a real vector, and its elements are proxy objects rather than `bool&`. When you
need a genuine array of booleans that behaves normally, use `std::vector<char>` or
`std::deque<bool>`.

## deque: growable at both ends

`std::deque` supports O(1) `push_front` and `push_back`, plus O(1) indexing. It is
stored as a set of fixed-size blocks rather than one contiguous run.

Use it when elements arrive or leave at both ends — a BFS frontier that needs
front insertion, or a monotonic deque for sliding-window minima. For anything that
only grows at the back, a vector is faster because of the contiguity.

## The container adaptors

`std::stack`, `std::queue` and `std::priority_queue` are thin wrappers over another
container, exposing a restricted interface.

`priority_queue` is the one with real algorithmic content: a binary heap giving O(1)
access to the largest element and O(log n) push and pop.

```cpp
priority_queue<int> maxheap;                                  // largest on top
priority_queue<int, vector<int>, greater<int>> minheap;       // smallest on top
```

The default is a **max**-heap, which surprises people coming from languages whose
default is a min-heap. The `greater<int>` form is what Dijkstra's algorithm needs
and is worth memorising.

A heap supports exactly two things: look at the extreme element, and remove it. It
cannot search, cannot iterate in order, and has no way to remove or update an
arbitrary element. The standard workaround is lazy deletion — push the updated
entry and, when popping, discard entries that are stale.

## set and map: ordered, tree-backed

`std::set` and `std::map` are balanced binary search trees. Every operation is
O(log n) *guaranteed*, and iteration comes out in sorted order.

What justifies them over the hash containers is the ordering-aware queries:

```cpp
auto it = s.lower_bound(x);          // first element >= x
if (it != s.begin()) { --it; }       // the predecessor of x
```

That predecessor lookup is not expressible with a hash table at all, and it is the
usual reason to pay the log factor.

Call `lower_bound` as the member function, `s.lower_bound(x)`, not the free
`std::lower_bound(s.begin(), s.end(), x)`. The free version cannot use the tree
structure and degrades to O(n).

`std::multiset` allows duplicates. Its trap: `erase(value)` removes **every** copy.
To remove one, erase an iterator — `ms.erase(ms.find(value))`.

## unordered_set and unordered_map: hashed

Hash tables. Average O(1) for insert, find and erase, no ordering, no range
queries, and a worst case of O(n) per operation that adversarial input can trigger
deliberately.

Prefer them when you only ever ask "is this present" or "what value is stored under
this key", and n is large enough that the log factor is worth removing. See the
hashing notes for the anti-hash defence and for why `reserve` matters here.

## A decision table

| What you need | Container |
|---|---|
| Indexed sequence, grows at the back | `vector` |
| Grows at both ends | `deque` |
| Largest or smallest, repeatedly | `priority_queue` |
| Membership only, no order needed | `unordered_set` |
| Key → value, no order needed | `unordered_map` |
| Sorted iteration, or predecessor / successor | `set` / `map` |
| Duplicates, sorted | `multiset` |
| Integer keys in a small range | plain `vector` indexed by key |

The last row is the one most often missed. If the keys are bounded integers, an
array beats every associative container on both time and memory, with no hashing
and no tree.

## Iterator invalidation

Modifying a container while holding iterators into it is a common source of crashes
that reproduce inconsistently.

- `vector`: any reallocation (a `push_back` past capacity) invalidates everything.
  An `erase` invalidates from the erased position onwards.
- `deque`: insertion or erasure anywhere but the ends invalidates all iterators.
- `set`, `map`: only the erased element's iterator is invalidated. Others survive
  insertions and erasures, which is occasionally decisive.
- `unordered_*`: a rehash invalidates all iterators, though references to elements
  survive.

The `erase`-in-a-loop idiom that works for the node-based containers is:

```cpp
for (auto it = s.begin(); it != s.end(); ) {
    if (should_remove(*it)) it = s.erase(it);   // erase returns the next
    else ++it;
}
```
