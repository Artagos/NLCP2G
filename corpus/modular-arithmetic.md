# Modular arithmetic

## Why answers are asked modulo a prime

Counting problems produce numbers with thousands of digits. Rather than require big
integers, statements ask for the answer modulo a fixed number — almost always
`1000000007`, written `1e9+7`, and occasionally `998244353`.

Both are prime, which matters: modulo a prime, every non-zero value has a
multiplicative inverse, so division works. Modulo a composite it does not, and the
usual division technique silently breaks.

`1e9+7` is chosen because it is prime and because it is just under 2³⁰, so two
values below it multiply to under 2⁶⁰ — which fits in a signed 64-bit integer with
room to spare.

## The rules that hold

Addition, subtraction and multiplication all commute with taking the remainder:

```
(a + b) mod m = ((a mod m) + (b mod m)) mod m
(a - b) mod m = ((a mod m) - (b mod m) + m) mod m
(a * b) mod m = ((a mod m) * (b mod m)) mod m
```

So you can reduce at every step rather than at the end, which is the whole point —
the intermediate values never grow.

Division does **not** work this way. `(a / b) mod m` is not `(a mod m) / (b mod m)`,
and integer division discards information the modulus cannot recover.

## Subtraction and the negative remainder

In C++, `%` follows the sign of the dividend: `(-3) % 7` is `-3`, not `4`. That is
different from the mathematical convention and different from Python.

So any subtraction needs correcting:

```cpp
long long sub(long long a, long long b) { return ((a - b) % MOD + MOD) % MOD; }
```

Adding `MOD` before the second `%` pushes a negative result back into range. This is
a very common bug because it only manifests when the subtraction happens to go
negative, which may not occur on the sample input.

## Multiplication and overflow

With `MOD` near 10⁹, the product of two reduced values is near 10¹⁸ — which fits in
`long long` (max about 9.2 × 10¹⁸) but not in `int`.

```cpp
long long mul(long long a, long long b) { return a % MOD * (b % MOD) % MOD; }
```

If both operands are `int`, the multiplication is done in `int` and overflows
*before* the assignment to a wider type. Casting one operand first is what forces
the wider arithmetic:

```cpp
int a, b;
long long bad  = a * b;              // overflows in int, then widens
long long good = (long long)a * b;   // widens first
```

## Fast exponentiation

Raising to a power by repeated squaring, in O(log e):

```cpp
long long power(long long base, long long e, long long mod) {
    long long result = 1;
    base %= mod;
    while (e > 0) {
        if (e & 1) result = result * base % mod;
        base = base * base % mod;
        e >>= 1;
    }
    return result;
}
```

The loop reads the exponent's binary representation from the bottom up, multiplying
in the current square whenever the bit is set. Reducing `base` before the loop
matters if the caller passes something already large.

## Modular inverse and division

The inverse of `a` modulo `m` is the value `x` with `a * x ≡ 1 (mod m)`. Dividing by
`a` means multiplying by that inverse.

When `m` is prime and `a` is not a multiple of it, Fermat's little theorem gives it
directly:

```cpp
long long inv(long long a) { return power(a, MOD - 2, MOD); }
```

That costs O(log m) per inverse. When `m` is not prime, the extended Euclidean
algorithm is required instead, and the inverse only exists when `gcd(a, m) = 1`.

For many inverses of consecutive small numbers, there is an O(n) recurrence that
beats calling `power` n times:

```cpp
inv[1] = 1;
for (int i = 2; i < n; ++i)
    inv[i] = (MOD - (MOD / i) * inv[MOD % i] % MOD) % MOD;
```

## Binomial coefficients

The standard setup precomputes factorials and their inverses, after which any
binomial is O(1):

```cpp
fact[0] = 1;
for (int i = 1; i < N; ++i) fact[i] = fact[i-1] * i % MOD;
ifact[N-1] = inv(fact[N-1]);
for (int i = N-1; i > 0; --i) ifact[i-1] = ifact[i] * i % MOD;

long long C(int n, int k) {
    if (k < 0 || k > n) return 0;
    return fact[n] * ifact[k] % MOD * ifact[n-k] % MOD;
}
```

Note the inverse factorials are built downwards from a single `inv` call, rather
than inverting each factorial separately — one O(log m) call instead of n of them.

The guard on out-of-range `k` matters: without it the arrays are indexed negatively.

## Mistakes worth knowing

**Forgetting the modulus somewhere in a long expression.** One unreduced
multiplication overflows and poisons everything downstream. Reduce after every
multiply.

**Negative results from subtraction.** Covered above, and the most common of all.

**Dividing.** `a / b % MOD` is essentially never what you want.

**Using `int` for the accumulator.** Products need 64 bits even when the operands
fit in 32.

**Assuming the modulus is prime when the statement chose a composite.** Fermat's
theorem does not apply, and `power(a, MOD-2)` returns a plausible wrong number.

**Taking the modulus of a value that is already a count of things you then compare.**
Once reduced, a value is no longer ordered like the original, so comparisons and
maxima must happen before the reduction, never after.
