# Stacks, queues and monotonic structures

## Stack: last in, first out

A stack exposes push, pop and top, all O(1), and hands back the most recently added
element first. It is the right structure whenever the natural order of processing
is "finish the thing I started most recently".

Two uses dominate.

**Matching nested structure.** Push on an opening token, pop and check on a closing
one. The input is balanced exactly when every pop finds a matching opener and the
stack ends empty.

**Turning recursion into iteration.** Any recursive traversal can be rewritten with
an explicit stack holding the state each pending call would have kept. This is the
standard fix when a recursive depth-first search overflows the call stack.

## Queue: first in, first out

A queue hands back the oldest element first. Its defining use is breadth-first
search, where processing in arrival order is what makes the search discover nodes
in order of distance.

```cpp
queue<int> q;
q.push(start);
while (!q.empty()) {
    int u = q.front(); q.pop();
    // ...
}
```

`front()` reads and `pop()` removes; `pop()` returns nothing. Forgetting the `pop`
is an infinite loop, and it is the single most common bug in a hand-written BFS.

## Monotonic stack

A monotonic stack keeps its contents in sorted order by popping anything that would
break the order before pushing. It answers "for each element, what is the nearest
element to the left that is smaller (or larger) than it" in O(n) total.

```cpp
vector<int> prev_smaller(n, -1);
stack<int> st;                       // holds indices, values increasing
for (int i = 0; i < n; ++i) {
    while (!st.empty() && a[st.top()] >= a[i]) st.pop();
    prev_smaller[i] = st.empty() ? -1 : st.top();
    st.push(i);
}
```

The inner `while` does not make this quadratic: each index is pushed once and
popped at most once, so the total number of pops across the whole loop is at most
n. This is the same accounting argument that makes two-pointer scans linear.

Storing indices rather than values is almost always the right choice — you can
recover the value from the index, but not the position from the value.

The pattern is what underlies largest-rectangle-in-a-histogram style reasoning, and
more generally any question of the form "how far does each element dominate".

## Monotonic deque and sliding-window extremes

To get the maximum of every window of length k in O(n), keep a deque of indices
whose values are decreasing. The front is always the maximum of the current window.

```cpp
deque<int> dq;
for (int i = 0; i < n; ++i) {
    while (!dq.empty() && dq.front() <= i - k) dq.pop_front();   // left of window
    while (!dq.empty() && a[dq.back()] <= a[i]) dq.pop_back();   // dominated
    dq.push_back(i);
    if (i >= k - 1) result[i - k + 1] = a[dq.front()];
}
```

Two different pops for two different reasons. The front pop drops indices that have
fallen out of the window. The back pop drops indices whose values can never be the
maximum again, because a later element is at least as large — and being later, it
survives in every window they do.

A `priority_queue` solves the same problem in O(n log n) with lazy deletion. The
deque is the O(n) version, and it is the reason `std::deque` earns its place.

## Choosing between them

| Question | Structure |
|---|---|
| Most recent unfinished thing | stack |
| Oldest waiting thing | queue |
| Nearest smaller / larger element | monotonic stack |
| Extreme over every fixed window | monotonic deque |
| Extreme over a changing set, arbitrary removals | `priority_queue` with lazy deletion |

## Mistakes worth knowing

**Reading `top()` or `front()` on an empty container.** This is undefined behaviour,
not an exception. Guard with `empty()` every time.

**Using the wrong comparison direction in a monotonic structure.** Popping on `>`
versus `>=` decides how ties are handled, and gets the boundaries of equal runs
wrong in opposite directions. Work one small example with duplicates by hand.

**Storing values when you need positions.** Once the window has to know which index
left, values are not enough.

**Believing the inner `while` costs O(n) per iteration.** It does not, and the
worry usually leads to abandoning a correct linear algorithm for a slower one.
