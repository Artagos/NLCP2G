# Debugging wrong answers and timeouts

## Diagnose before you edit

The instinct on a failed submission is to change something and resubmit. That is
the slowest route. A verdict carries information, and reading it narrows the search
before any code changes.

- **Wrong answer on test 1** means the approach or the output format is wrong. Look
  at the sample by hand.
- **Wrong answer on a later test** usually means an unhandled edge case or an
  overflow, not a broken idea.
- **Time limit exceeded** with a correct answer on small inputs means the complexity
  is too high, or a constant factor is out of control.
- **Runtime error** means memory: out of bounds, division by zero, or stack depth.

## Stress testing, the reliable technique

The only dependable way to find a wrong answer on data you cannot see is to generate
data yourself and compare against something you trust.

Three pieces: the fast solution under test, a brute force that is obviously correct
but too slow, and a generator producing **small random** inputs. Loop until they
disagree.

```bash
for i in $(seq 1 1000); do
    python gen.py $i > in.txt
    ./fast  < in.txt > out1.txt
    ./brute < in.txt > out2.txt
    if ! diff -q out1.txt out2.txt > /dev/null; then echo "differs on $i"; break; fi
done
```

The generator must produce **small** cases — n between 1 and 8. A failing case with
n = 5 can be read and understood; one with n = 1000 cannot, and the point of the
exercise is a case you can trace by hand.

Seed the generator from the loop counter so a failure is reproducible.

This finds essentially every logic bug that random testing can find, and it is the
standard answer to "my solution is wrong and I do not know why".

## Edge cases worth checking every time

Most wrong answers on later tests are one of these:

- **n = 1**, or an empty input where the problem allows it
- **All elements equal**, which breaks strict comparisons and tie-breaking
- **Maximum values**, which is where overflow appears
- **Minimum values**, including negatives and zero when they are permitted
- **Already sorted, and reverse sorted** input
- **Duplicates**, when the reasoning assumed distinct values
- **The answer being zero or the whole array** — boundaries of the output range

Reading the constraints for the extremes and trying each one takes a couple of
minutes and catches more than rereading the code does.

## Finding the slow part

Before optimising, know what is slow. Compute the operation count from the
complexity and the constraints: if n = 2 × 10⁵ and the algorithm is O(n²), that is
4 × 10¹⁰ and no micro-optimisation will save it. The algorithm has to change.

If the complexity looks fine and it is still slow, the constant factor is the
suspect:

- `std::map` and `std::set` where an array or a sorted vector would do — node-based
  containers cost a pointer chase and a cache miss per operation.
- `endl` in a printing loop, flushing every line.
- Missing `ios::sync_with_stdio(false)` with large input.
- Passing containers by value into a function called in a loop.
- Building strings by repeated concatenation.
- `unordered_map` being attacked by anti-hash tests, turning O(1) into O(n).
- Allocating inside a loop what could be allocated once outside.

Timing a section locally with `chrono` on a worst-case input is more reliable than
guessing which part is slow.

## When it works locally and fails on the judge

This gap almost always means undefined behaviour, and the usual causes are an
uninitialised variable, an out-of-bounds access that happens to land in harmless
memory locally, or reliance on a compiler-specific behaviour.

Compiling with sanitisers turns most of these into an immediate, located error:

```bash
g++ -std=c++17 -O2 -Wall -Wextra -fsanitize=address,undefined -g sol.cpp
```

The address sanitiser catches out-of-bounds and use-after-free; the undefined
behaviour sanitiser catches signed overflow and bad shifts. Both slow the program
considerably, which is fine for a small test case.

`-Wall -Wextra` alone catches a surprising share of real bugs — unused variables
that reveal a typo, comparisons between signed and unsigned, uninitialised reads.

## Debugging output that does not break the submission

Print diagnostics to standard error, which the judge ignores:

```cpp
cerr << "dp[" << i << "] = " << dp[i] << '\n';
```

`cout` is the answer and must contain nothing else. A stray debug line on standard
output is a wrong answer or a presentation error with a perfectly correct algorithm
behind it.

Better still, guard the diagnostics so they compile away:

```cpp
#ifdef LOCAL
#define dbg(x) cerr << #x " = " << (x) << '\n'
#else
#define dbg(x)
#endif
```

Then compile locally with `-DLOCAL`.

## Reading your own code for the usual suspects

When stress testing is impractical, reread with a specific checklist rather than
generally:

- Every loop bound: `<` versus `<=`, and whether the last element is covered.
- Every index: 0-based data with 1-based input, or the reverse.
- Every accumulator's type: does the largest possible value fit?
- Every subtraction: can it go negative, and does that matter?
- Every early `return` or `break`: does it skip necessary cleanup?
- Every array size: is it big enough for the maximum n, plus one where needed?
- Multi-test-case runs: is all global state reset between cases?

That last one deserves attention. When a problem has several test cases in a single
run, stale state from the previous case is a bug that passes the first sample and
fails on the second — and it looks nothing like a state-reset problem from the
symptoms.
