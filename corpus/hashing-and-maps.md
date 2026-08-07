# Hashing, maps and sets

## What hashing buys and what it costs

A hash table stores keys by computing a number from each key and using it to pick a
bucket. Lookups do not compare the key against everything stored; they go straight
to one bucket and search only there. That makes insert, erase and find **average
O(1)**, independent of how many items are in the table.

The cost is that all ordering information is gone. A hash table cannot tell you the
smallest key, cannot iterate in sorted order, and cannot answer "what is the first
key at least x". If you need any of those, you want an ordered container instead.

The other cost is the word "average". The O(1) is not a worst-case guarantee, and
the gap between the two is a real failure mode rather than a theoretical footnote.

## unordered_map versus map

The two associative containers in C++ answer the same interface and behave
completely differently underneath.

| | `std::map` | `std::unordered_map` |
|---|---|---|
| Structure | balanced binary tree | hash table |
| find / insert / erase | O(log n) guaranteed | O(1) average, O(n) worst |
| Iteration order | sorted by key | unspecified |
| `lower_bound` / `upper_bound` | yes | no |
| Key requirement | `operator<` | hash function + `operator==` |
| Constant factor | larger per operation | smaller, but cache behaviour varies |

The rule of thumb: if you never need order, `unordered_map` is usually faster. If
you need ordered iteration, range queries, or a predecessor lookup, `map` is not
just more convenient but structurally necessary.

The same distinction applies to `std::set` and `std::unordered_set`, which are the
same containers without values attached.

## The worst case is reachable on purpose

`unordered_map` with integer keys uses, in libstdc++, an identity-like hash: the
key modulo the bucket count, where the bucket count is a prime. That is fast and
fine on ordinary data.

It is also predictable, which means an adversarial test can be constructed of keys
that all land in the same bucket. Every operation then degenerates to a linear scan
of one enormous bucket, and an O(n) algorithm becomes O(n²). This is a known and
routinely exploited failure on judges with open hacking.

Two defences. Mix a random value into the hash so the mapping cannot be predicted
ahead of time:

```cpp
struct Hash {
    size_t operator()(long long x) const {
        static const uint64_t R =
            chrono::steady_clock::now().time_since_epoch().count();
        x += R;
        x = (x ^ (x >> 30)) * 0xbf58476d1ce4e5b9ULL;
        x = (x ^ (x >> 27)) * 0x94d049bb133111ebULL;
        return x ^ (x >> 31);
    }
};
unordered_map<long long, int, Hash> m;
```

Or use `std::map` and accept the log factor, which is often the simpler call when
n is small enough that O(n log n) fits comfortably.

## Reserve, and the cost of rehashing

A hash table grows by allocating a bigger bucket array and re-inserting everything.
That is amortised O(1), but the reallocations are real work and they invalidate
iterators.

When the final size is known roughly in advance, saying so avoids all of it:

```cpp
unordered_map<int,int> m;
m.reserve(n);
m.max_load_factor(0.25);   // fewer collisions, more memory
```

This is one of the cheapest speedups available on a hash-heavy solution.

## operator[] inserts, and that matters

For both `map` and `unordered_map`, `m[k]` **default-constructs an entry** if `k`
is absent. Reading a missing key through `operator[]` therefore silently grows the
container.

```cpp
if (m[k] > 0) { ... }        // inserts k with value 0 if it was missing
if (m.count(k) && m.at(k) > 0) { ... }   // does not insert
```

That matters for correctness when you later iterate the map and find keys you never
meant to add, and for memory when the lookup happens in a loop. Use `count`,
`find`, or C++20's `contains` for membership tests, and `at` for checked reads.

The counting idiom `++freq[x]` relies on this default-construction deliberately, and
is fine — the entry is one you want.

## When a plain array beats a hash table

If the keys are integers in a bounded range, an array indexed by the key is a
perfect hash table with no collisions, no hashing cost and excellent cache
behaviour:

```cpp
vector<int> freq(MAXV + 1, 0);
for (int x : a) ++freq[x];
```

This is dramatically faster than `unordered_map` and immune to anti-hash tests. It
is worth checking the constraints for a bound on the values before reaching for a
hash container at all; when values are up to 10⁶ or so, the array is the better
answer.

When the values are large but few, coordinate compression gets you back to the
array case: sort the distinct values, and replace each value by its index in that
sorted list.
