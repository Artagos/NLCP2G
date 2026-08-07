# Input, output and runtime errors

## Fast input and output

With 10⁵ or more numbers to read, the default synchronisation between C++ streams
and C stdio becomes the bottleneck. Two lines remove it:

```cpp
ios::sync_with_stdio(false);
cin.tie(nullptr);
```

`sync_with_stdio(false)` stops `cin` and `cout` mirroring `scanf` and `printf`
buffers. `cin.tie(nullptr)` stops `cout` being flushed before every read — the
flush exists so that interactive prompts appear before input is requested, which is
not something a batch program needs.

Both must come before any reading, and after them you must not mix `cin` with
`scanf` in the same program.

**`endl` flushes the stream.** In a loop printing 10⁵ lines that is 10⁵ flushes, and
it can cost more than the rest of the program. Print `'\n'` instead and let the
stream flush when it is ready:

```cpp
for (int i = 0; i < n; ++i) cout << answer[i] << '\n';
```

The exception is an interactive problem, where every response must reach the judge
immediately — there, flushing is required and `endl` (or an explicit `cout.flush()`)
is correct.

## Reading mixed lines and tokens

`cin >> x` stops at whitespace and leaves the newline in the buffer. A `getline`
straight afterwards therefore reads an empty string. Consume the rest of the line
first:

```cpp
int n;
cin >> n;
cin.ignore(numeric_limits<streamsize>::max(), '\n');
string line;
getline(cin, line);
```

Reading until end of input is `while (cin >> x)`, which stops on both EOF and a
malformed token. Checking `while (!cin.eof())` is the wrong idiom: the EOF flag is
only set *after* a failed read, so the loop runs one extra time with a stale value.

## Integer overflow

The most common wrong answer that is not a logic error at all. `int` holds up to
about 2.1 × 10⁹, and it is easy to exceed without any single value looking large.

```cpp
int a = 100000, b = 100000;
long long bad  = a * b;              // computed in int: overflows, then widens
long long good = (long long)a * b;   // widens first
```

The multiplication happens in the operands' type, and the assignment's type has no
influence. Casting one operand is what changes the arithmetic.

Places this bites: prefix sums of large arrays, products of two indices, a distance
accumulated over many edges, `mid` computed as `(lo + hi) / 2` with large bounds,
and anything counted modulo a prime near 10⁹.

Signed overflow is undefined behaviour in C++, so the compiler may assume it cannot
happen and optimise accordingly. The result is not merely a wrapped value — it can
be a program that behaves differently at `-O2` than at `-O0`.

## Undefined behaviour that shows up as a runtime error

**Out-of-bounds access.** `v[i]` performs no checking. Reading slightly past the end
usually returns garbage; writing past it corrupts the heap and the crash surfaces
somewhere unrelated. `v.at(i)` checks and throws, which is worth using while
debugging.

**Uninitialised variables.** A local `int x;` holds whatever was on the stack. Global
and static variables are zero-initialised; locals are not. This produces the classic
"works on my machine" failure.

**Dividing by zero.** Integer division by zero is undefined and typically raises a
signal. Floating-point division by zero gives infinity instead, quietly.

**Dereferencing `end()` or an invalidated iterator.** Common after `push_back`
reallocates a vector while a reference into it is still held.

**Recursion past the stack limit.** Discussed under recursion; reported as a runtime
error, not a memory limit.

## The verdicts and what they usually mean

| Verdict | Usual cause |
|---|---|
| WA — wrong answer | logic error, overflow, off-by-one, unhandled edge case |
| TLE — time limit exceeded | complexity too high, or a large constant factor |
| MLE — memory limit exceeded | array too big, recursion too deep, container growth |
| RE — runtime error | out of bounds, division by zero, stack overflow, uncaught throw |
| PE — presentation error | right values, wrong whitespace or ordering |
| CE — compile error | usually a missing header or a language-version mismatch |

RE and MLE overlap: a stack overflow is memory exhaustion reported as RE, and an
`std::bad_alloc` from an oversized allocation may appear as either.

## Output formatting

Floating-point output defaults to six significant digits, which is rarely what a
problem wants:

```cpp
cout << fixed << setprecision(10) << x << '\n';
```

`fixed` selects digits after the decimal point rather than significant digits.
Without it, `setprecision` counts total digits and large values lose their fraction.

When a problem specifies a tolerance such as 10⁻⁶, print two or three more digits
than that and let the checker do the comparison.

Trailing whitespace and a trailing newline are almost always accepted; a missing
separator between values is not.

## Reading the constraints before writing anything

The constraints tell you the complexity you are allowed and the integer width you
need. Two questions, answered before the first line of code:

- What is the largest n, and what complexity does that permit?
- What is the largest value any accumulator could reach, and does it fit in 32 bits?

Answering the second question at the start prevents most overflow bugs from ever
being written.
