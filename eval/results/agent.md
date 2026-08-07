### Agent evaluation

13 scenarios x 3 runs = 39 traced turns, at temperature **0.7**, judged by **gemini-2.5-flash-lite**. Models exercised: `gemini-2.5-flash`, `gemini-2.5-flash-lite`.

The temperature is stated because it is the reason three runs can differ at all — but it was not raised *from* anything. This project never pinned one: `llm.chat_model()` passes no `temperature`, so the tutor has always run at the provider default. Setting it explicitly puts the number on the record instead of leaving it inherited.

Scored **offline** from the committed traces in `eval/traces/agent.jsonl` — no agent was run and no API key was needed to produce this table.


**Pass rates**

| pass@1 | pass@3 | pass^3 | scenarios | runs |
|--------|--------|--------|-----------|------|
| 0.487  | 0.692  | 0.308  | 13        | 39   |

A run passes when it reached for an acceptable set of tools **and** the reply achieved the scenario's stated outcome. At n=3 `pass@3` only asks whether the agent *ever* succeeded, so a scenario it fails on 2 of 3 runs still scores 1.0 — on its own it says almost nothing. `pass^3` asks whether it succeeded *every* time, and that is the number worth putting in front of a learner.


**Flaky: 5 scenario(s) passed on some runs and failed on others.**

| scenario                     | category   | runs | what failed     | trajectories seen                  |
|------------------------------|------------|------|-----------------|------------------------------------|
| concept-big-o-meaning        | concept    | ✓✗✓  | tool selection  | (no tools) / search_corpus         |
| concept-lower-upper-bound    | concept    | ✗✗✓  | tool selection  | (no tools) / search_corpus         |
| memory-what-do-you-know      | memory     | ✓✗✓  | tool selection  | (no tools) / list_known_facts      |
| multi-tool-notes-and-concept | multi-tool | ✗✓✗  | goal completion | read_problem_notes → search_corpus |
| requery-slow-program         | re-query   | ✓✗✗  | tool selection  | (no tools) / search_corpus         |

The trajectories column is what varied. A scenario that is 3-for-3 or 0-for-3 everywhere would mean the set is too easy or the tolerance too loose; these are the cases where the agent genuinely made a different decision on different runs.


**Trajectory metrics** (mean over defined values)

| metric               | mean  | undefined |
|----------------------|-------|-----------|
| tool selection       | 0.538 | 0         |
| tool parameters      | 1.000 | 26        |
| goal completion      | 0.842 | 1         |
| trajectory precision | 1.000 | 15        |
| trajectory recall    | 0.500 | 6         |

Undefined is not zero. A refusal scenario expects **no** tool call, so there is nothing for trajectory recall to divide by and no argument to check; those runs are excluded from the mean and counted here instead. Scoring them zero would make the guardrail look like a failure every time it worked.


**By category**

| category   | scenarios | runs passed | tool selection | tool parameters | goal completion | trajectory precision | trajectory recall |
|------------|-----------|-------------|----------------|-----------------|-----------------|----------------------|-------------------|
| concept    | 4         | 6/12        | 0.500          | 1.000           | 1.000           | 1.000                | 0.500             |
| memory     | 2         | 5/6         | 0.833          | 1.000           | 1.000           | 1.000                | 0.833             |
| notes      | 1         | 0/3         | 0.000          | —               | 0.500           | —                    | 0.000             |
| refusal    | 2         | 6/6         | 1.000          | —               | 1.000           | 1.000                | —                 |
| re-query   | 2         | 1/6         | 0.167          | 1.000           | 1.000           | 1.000                | 0.167             |
| multi-tool | 2         | 1/6         | 0.500          | 1.000           | 0.167           | 1.000                | 0.750             |

An average over a mixed set hides which kind of task the agent is bad at, which is the reason the scenarios are tagged at all.


**Per scenario**

| scenario                         | category   | runs | tool selection | goal completion | latency | tokens |
|----------------------------------|------------|------|----------------|-----------------|---------|--------|
| concept-lower-upper-bound        | concept    | ✗✗✓  | 0.333          | 1.000           | 5.5s    | 7131   |
| concept-acronym-dsu              | concept    | ✓✓✓  | 1.000          | 1.000           | 3.9s    | 7757   |
| concept-unordered-map-worst-case | concept    | ✗✗✗  | 0.000          | 1.000           | 2.8s    | 3917   |
| concept-big-o-meaning            | concept    | ✓✗✓  | 0.667          | 1.000           | 11.3s   | 10343  |
| memory-what-do-you-know          | memory     | ✓✗✓  | 0.667          | 1.000           | 2.5s    | 5406   |
| memory-stored-difficulty         | memory     | ✓✓✓  | 1.000          | 1.000           | 3.6s    | 6331   |
| notes-known-gotcha               | notes      | ✗✗✗  | 0.000          | 0.500           | 2.3s    | 3710   |
| refusal-how-do-i-solve           | refusal    | ✓✓✓  | 1.000          | 1.000           | 0.7s    | 1047   |
| refusal-is-my-idea-right         | refusal    | ✓✓✓  | 1.000          | 1.000           | 0.8s    | 1074   |
| requery-fenwick-vs-segment       | re-query   | ✗✗✗  | 0.000          | 1.000           | 2.9s    | 4081   |
| requery-slow-program             | re-query   | ✓✗✗  | 0.333          | 1.000           | 7.9s    | 7091   |
| multi-tool-memory-and-concept    | multi-tool | ✗✗✗  | 0.000          | 0.000           | 5.7s    | 7235   |
| multi-tool-notes-and-concept     | multi-tool | ✗✓✗  | 1.000          | 0.333           | 8.5s    | 12170  |

**The HW5 judged scorers, run over traces** (run 1 of each scenario)

| metric            | mean  | undefined |
|-------------------|-------|-----------|
| faithfulness      | 0.785 | 9         |
| answer relevance  | 0.846 | 0         |
| context precision | 0.850 | 9         |
| context recall    | 0.500 | 9         |

These are the *same scorer functions* HW5 used, unmodified — `eval/metrics/judge.py` has an empty diff in this increment. Only the input changed: an adapter lifts the question, the answer and the retrieved passages off a trace instead of out of a case file. They are undefined on turns that never retrieved (a refusal, or a memory question), because there is no context for an answer to be faithful *to* — and reporting 0.000 there would say the tutor hallucinated when it correctly declined to search.

