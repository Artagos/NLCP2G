# Strings and pattern matching

## The cost of the obvious approach

Searching for a pattern of length m in a text of length n by trying every starting
position is O(nm). With n = 10⁵ and m = 10⁵ that is 10¹⁰ character comparisons,
which is far too slow.

In practice the naive scan is fine for small inputs and is what `std::string::find`
does. The algorithms below exist for when it is not.

## KMP: prefix function

The Knuth–Morris–Pratt algorithm searches in O(n + m) by never re-examining a
character of the text. Its core is the **prefix function**: for each position of a
string, the length of the longest proper prefix that is also a suffix ending there.

```cpp
vector<int> prefix_function(const string& s) {
    int n = s.size();
    vector<int> pi(n, 0);
    for (int i = 1; i < n; ++i) {
        int j = pi[i - 1];
        while (j > 0 && s[i] != s[j]) j = pi[j - 1];
        if (s[i] == s[j]) ++j;
        pi[i] = j;
    }
    return pi;
}
```

"Proper" means it cannot be the whole string, which is why `pi[0]` is always 0.

The inner `while` looks like it could be linear each time, making the whole thing
quadratic. It cannot: `j` increases by at most one per iteration of the outer loop,
so it can decrease at most n times in total across the entire run — the same
accounting argument that makes two-pointer scans linear.

To search, run the prefix function over `pattern + '#' + text`, where `#` occurs in
neither. Every position where the value equals the pattern's length is a match. The
separator is essential; without it a value can exceed the pattern length by
overlapping the boundary.

The prefix function answers more than search. The shortest period of a string of
length n is `n - pi[n-1]`, which is how "does this string consist of a repeated
block" is decided in linear time.

## Z-function

The Z-array gives, for each position, the length of the longest substring starting
there that is also a prefix of the whole string. It solves the same problems as the
prefix function and many people find it easier to reason about.

```cpp
vector<int> z_function(const string& s) {
    int n = s.size();
    vector<int> z(n, 0);
    for (int i = 1, l = 0, r = 0; i < n; ++i) {
        if (i < r) z[i] = min(r - i, z[i - l]);
        while (i + z[i] < n && s[z[i]] == s[i + z[i]]) ++z[i];
        if (i + z[i] > r) { l = i; r = i + z[i]; }
    }
    return z;
}
```

`[l, r)` is the rightmost segment known to match a prefix. Inside it, a previously
computed value can be reused; outside, comparison starts from scratch. Same linear
bound, same reasoning.

## Polynomial hashing

Treat a string as a number in some base, modulo a large prime. Two equal substrings
have equal hashes; two different ones almost certainly do not.

```cpp
// h[i] = hash of the first i characters
h[0] = 0;
for (int i = 0; i < n; ++i)
    h[i+1] = (h[i] * BASE + s[i]) % MOD;

// hash of s[l..r), given pw[k] = BASE^k % MOD
long long sub = (h[r] - h[l] * pw[r-l]) % MOD;
if (sub < 0) sub += MOD;
```

After O(n) preprocessing, any substring hash is O(1) — which makes substring
equality O(1) and turns a great many string problems into easy ones.

The caveat is that it is probabilistic. With a single 64-bit-ish modulus and n
substrings compared, birthday collisions become likely enough to matter, and on
judges with hacking the base and modulus can be attacked directly if they are
predictable. Two defences: use two independent moduli and compare both hashes, and
choose the base randomly at run time rather than fixing it at 31.

## When to use which

| Need | Use |
|---|---|
| Find one pattern in one text | KMP, Z-function, or `string::find` if n is small |
| Compare arbitrary substrings repeatedly | polynomial hashing |
| Shortest period, borders of a string | prefix function |
| Many patterns at once | Aho–Corasick |
| All distinct substrings, suffix ordering | suffix array |
| Palindromes at every centre | Manacher's algorithm |

Hashing is the most flexible and the least rigorous; KMP and Z are exact and
specific. For a single search either is fine, and the choice is whichever you can
write correctly from memory.

## C++ string mechanics worth knowing

`std::string` concatenation in a loop can be quadratic, because each `+` may
allocate and copy. Building with `+=` into a reserved string, or writing into a
`vector<char>`, avoids it.

Passing strings by value copies them. Take `const string&` in helper functions, or
`string_view` when you only read.

Comparing with `==` is O(length), not O(1) — a loop doing string comparisons is
carrying a hidden factor.

Characters are just small integers: `s[i] - 'a'` gives an index into a 26-element
array, which is the standard way to count letters. That subtraction assumes the
input is lowercase Latin, and silently produces out-of-range indices when it is not.

## Mistakes worth knowing

**Omitting the separator in the KMP concatenation.** Matches that straddle the
boundary are reported.

**A single weak hash modulus.** Works on the samples, collides on the real tests.

**Off-by-one in the substring hash.** Half-open versus inclusive again; pick one.

**Assuming `pi[0]` could be non-zero.** It cannot; a proper prefix of a
single-character string is empty.

**Using `endl` in a loop that prints many lines.** Each one flushes, and the
input/output cost dominates everything else.
