# CP Tutor — Implementation

An agentic tutor that lets **non-coders** solve competitive-programming problems
by **describing their algorithm in plain words**. The system translates the
described approach into C++, runs it against real test cases, and reports back
faithfully — while a guardrailed tutor answers *general* concept questions but
never leaks how to solve the current problem.

Repo: https://github.com/Artagos/NLCP2G  (**NLCP2G** — Natural Language Competitive Programming PlayGround)

---

## 1. High-level idea

- The learner never writes code. They **describe** an approach; the system
  builds and runs it.
- The system is a **faithful executor + honest feedback loop**, not a solver. It
  never suggests a better algorithm or gives strategy hints.
- Two non-negotiable guardrails:
  1. **The code path is blind to the problem.** The feasibility screener and the
     C++ generator see only the raw input/output format + the user's words —
     never the statement or intended solution. They *cannot* infer a smarter
     approach or fill algorithmic gaps.
  2. **The tutor answers concepts in the abstract only.** It sees the statement
     (to recognise and refuse problem-specific questions) but never a solution.

---

## 2. Architecture

```
 React SPA (frontend/) ──HTTP──▶ FastAPI (backend/main.py)
                                     │
                        ┌────────────┴─────────────────────────────┐
                        │ Router (Gemini flash-lite)                │
                        │ classifies intent + difficulty delta      │
                        └────────────┬─────────────────────────────┘
  concept│meta │ strategy │ solution │ new_problem │ summarize │ chitchat
          │         │         │            │            │
          ▼         ▼         ▼            ▼            ▼
        Tutor   (refuse)  Solution     load new     Summarizer
       (Gemini)           pipeline     (adaptive/    (per-problem
        │  ▲              │            calibrated)    recap)
        │  └─ pull: retrieve_memory / read_problem_notes
        └──── push: rules/operating_rules.md + learner rule docs
                          │
                          ▼
    Feasibility screen ─▶ EXECUTOR (C++ codegen) ⇄ CRITIC (fidelity)
    (blind, INFEASIBLE)   (blind, UNCLEAR)   Handoff{status,result,
                          │      ▲            needs_approval}, ≤2 rounds
                          │      └── shared scratchpad (run-keyed)
                          ▼
                   Sandbox (Docker + g++) ─▶ Verdict explainer (no-hints)
                   AC/WA/TLE/RE/CE                    │
                                                      ▼
                                              run log (`runs`)
                                                      │
                        ┌─────────────────────────────┘
                        ▼   out of band, its own clock
              MONITOR  python -m backend.monitor [--watch N]
              per-run judge (named values + rationale) ─▶ batch analyst
                                                      ─▶ reports/*.md

 Problems: Codeforces (codeforces.py) · Stores: SQLite + JSON docs + markdown
```

---

## 3. The chat pipeline (per message)

1. **Router** (`router.py`, Gemini `gemini-2.5-flash-lite`, structured output)
   classifies the message into one intent and, for `new_problem`, infers a
   `rating_delta`.
2. Dispatch (`main._handle`):
   - **concept** → `tutor.answer(...)` (allowed, general CS only).
   - **meta** → also `tutor.answer(...)`. Messages addressed to the agent rather
     than about CS: the learner stating a preference or background, asking what
     it remembers, or asking what other learners noted about this problem. The
     tutor is the only agent holding the retrieval tools, so anything needing
     memory or notes has to arrive here.
   - **strategy** → canned refusal (would leak the solution).
   - **solution** → the solution pipeline (below).
   - **new_problem** → auto-summarize the current problem, then load a new one at
     an adaptive or calibrated difficulty.
   - **summarize** → recap the current problem's activity.
   - **chitchat** → friendly nudge.
3. Every exchange (user + assistant) is persisted to memory, tagged with the
   problem it happened on.

### The solution pipeline (`translator.translate_and_run`)

```
described approach
   │
   ├─▶ Feasibility screen (screener.py) ── not feasible ─▶ verdict INFEASIBLE + why
   │        (is this even a doable procedure on the given inputs? slow is OK)
   ▼
   C++ codegen (translator.py) ─────────── can't implement ─▶ verdict UNCLEAR + why
   │        (implement EXACTLY what was said; gate, don't guess)
   ▼
   Fidelity critic (critic.py) ─────────── escalate ───────▶ NEEDS_CLARIFICATION
   │   Handoff{status, result, needs_approval}
   │     approved → run · revise → rebuild once · escalate → ask the learner
   ▼
   Sandbox (sandbox.py → Docker) ────────── infra down ─────▶ SANDBOX_UNAVAILABLE
   │
   ▼
   Verdict facts → verdict explainer (no-hints) → AC / WA / TLE / RE / CE
```

Four layers can stop before the sandbox, each giving specific feedback:
- **INFEASIBLE** — the described "solution" can't be carried out (not an
  algorithm, contradictory, needs unavailable data). *Slowness is never a reason
  to reject.*
- **UNCLEAR** — feasible but too vague/contradictory to translate faithfully.
- **NEEDS_CLARIFICATION** — the critic escalated: the code could not be written
  without deciding something the learner never specified, or two rebuilds still
  didn't match their words.
- Only a clean, feasible, implementable, *faithful* program reaches the sandbox.

### The critic (`critic.py`) — the second agent

Blind in exactly the way the executor is: raw I/O format, the learner's words,
and the generated code. Nothing else. It cannot judge correctness because it does
not know the problem — which is what leaves fidelity as the only question it can
answer, and what makes it hard to talk into approving code just because the code
is good.

Rubric: **C1** invented logic · **C2** dropped step · **C3** substituted method ·
**C4** silent repair. Explicitly *not* violations: I/O scaffolding, naming, loop
form, integer width, and the algorithm being slow or wrong.

It has a written **delegation brief** (`prompts.CRITIC_DELEGATION_BRIEF`) stating
its scope, when it acts alone, when it escalates, and a budget of 2 rounds. It has
no tools. Both agents append to the `scratchpad` table under the run id —
append-only and attributed, so a handoff is replayed rather than reconstructed.
Violations from *every* round are kept, including ones later fixed, so a
caught-and-corrected C1 is still visible to the monitor.

---

## 4. Backend modules

| File | Responsibility |
|---|---|
| `main.py` | FastAPI app, identity middleware, endpoints, dispatch, run logging, serves the built SPA |
| `llm.py` | Single Gemini shim (`google-genai`): `generate` / `generate_structured` / `generate_with_tools`, lazy client, retries on 429/500/503 |
| `router.py` | Intent + `rating_delta` classifier (structured output) |
| `tutor.py` | Guardrailed Q&A; assembles pushed context and exposes the pull tools |
| `screener.py` | Feasibility pre-screen (blind to the problem) |
| `translator.py` | The **executor**: NL → C++ (blind), gating, critic loop, sandbox run, faithful verdict |
| `critic.py` | The **fidelity critic** (second agent) + the `Handoff` object |
| `reflect.py` | Decides, per turn, whether anything is worth remembering |
| `monitor.py` | The out-of-band judge: grades the run log, writes `reports/*.md` |
| `summarizer.py` | Per-problem recap (no hints) |
| `sandbox.py` | Host side: build temp dir, invoke Docker, parse results |
| `problem.py` | `Problem` model + offline fallback problem |
| `codeforces.py` | Fetch + parse real problems (cloudscraper + BeautifulSoup) |
| `state.py` | Per-learner current problem + adaptive/calibrated selection |
| `memory.py` | **Relational store**: problems, attempts, notes, runs, scratchpad, judgments |
| `docstore.py` | **Document store**: agent-written facts and rules, private + shared |
| `rules.py` | **Markdown store**: loads and parses `rules/operating_rules.md` |
| `prompts.py` | All system prompts (the pure-executor & no-hints rules live here) |

---

## 5. Problems: Codeforces

`codeforces.py` fetches real problems. The public CF **API** lists problems
(metadata) but not statements or tests; those live on the HTML page behind
Cloudflare, so we use **cloudscraper** + **BeautifulSoup** to fetch and parse:

- **statement** (plain text) for the tutor's context,
- **statement_html** (rich HTML, images made absolute, scripts stripped) for the
  UI — rendered with **MathJax** so `$$$…$$$` math displays,
- **io_format** (input + output specification) — the *only* problem info the
  screener/translator ever see,
- **sample tests** (input/output pairs),
- **time/memory limits**.

**Verdicts run against the sample tests** (AC/WA/RE/CE). CF's full hidden tests
aren't public on any judge, so the huge-input TLE signal isn't available for
arbitrary problems (a "generated stress tests" feature would restore it). The
original calibrated "Count Pairs With Sum K" problem remains only as an **offline
fallback** when Codeforces is unreachable.

---

## 6. Difficulty control

Switching problems ("give me another problem", or the 🎲 button):

- **No difficulty asked** → adaptive default band from what the session has
  solved (starts 800–1000, climbs as harder problems are solved). `state._band`.
- **Easier/harder asked** → the router infers a **`rating_delta`** (multiple of
  100) **calibrated to the wording** — "slightly harder" ≈ +100, "much harder" ≈
  +300, "way harder" ≈ +400/500, mirrored for easier, explicit numbers honored.
  Selection targets `current_rating + delta` exactly (`state._target_band`).
- Already-seen problems are excluded; the candidate pool spans ratings 800–3500.

---

## 7. Memory — three stores, and both directions

Identity is a `uid` cookie (default `guest`), set via `POST /whoami`. No
password: the name **scopes** memory, it doesn't protect anything. The session
scope is derived from it (`sid = "u:<uid>"`), so switching learner in one browser
switches everything private — which is what makes private-vs-shared testable in
two tabs rather than two machines.

### 7.1 Relational — SQLite (`memory.py`)

The structured domain model: things we filter, join, and aggregate on.

| Table | Holds | Scope |
|---|---|---|
| `problems` | name, rating, tags, limits — queryable via `find_problems(min_rating, max_rating, tag)` | shared |
| `attempts` | one row per solution attempt (approach text, verdict, time) | per learner |
| `seen` | every problem shown + a `solved` flag | per learner |
| `messages` | full chat log (role, content, intent, `problem_key`) | per learner |
| `summaries` | saved per-problem recaps | per learner |
| `session_current` | the current problem pointer | per learner |
| `notes` | learner-written notes on a problem — **untrusted input** | **shared** |
| `runs` | the run log the monitor grades | operational |
| `scratchpad` | executor↔critic handoffs, append-only, keyed by `run_id` | operational |
| `judgments` | the monitor's verdicts | operational |

### 7.2 Non-relational — JSON documents (`docstore.py`)

Free-form memory the agent writes in its own words, where no fixed schema fits.
On disk as `memory_store/private/<user>.json` and `memory_store/shared.json` —
deliberately readable, so you can `cat` it during a demo.

- **fact** — something learned that would otherwise be re-asked, saved **with a
  cue** (keywords a future request would contain, plus a note of when it
  applies). Retrieved by cue match; when it surfaces, the model decides what to
  do with it.
- **rule** — a standing change in behaviour. Attached on every run for its owner,
  never retrieved. The model doesn't decide what to do — the rule says; it decides
  only whether the rule applies.

**Privacy is structural.** A user's private documents are a separate file. B's
agent cannot read A's facts because they are not on its read path — there is no
`WHERE user_id = ?` to get wrong and no prompt instruction to override.

### 7.3 Operating rules — markdown (`rules/operating_rules.md`, `rules.py`)

Static rules an admin can open and fix in seconds. Rule ids (`R1`…`R12`) are
parsed out, recorded on every run, and cited by the monitor. The file is re-read
when its mtime changes, so an edit is live on the next message.

*Not injected into the blind path* — the screener and the C++ generator get none
of it. Rules in their context would be a channel for problem knowledge to leak in.

### 7.4 Push and pull

- **Push** (every user-facing run, unasked): the rules file + the learner's rule
  documents.
- **Pull** (mid-run, only when needed): `retrieve_memory(query)` for facts matched
  on their cue, and `read_problem_notes()` for what other learners wrote. These
  are real function calls; `llm.generate_with_tools` drives the loop manually so
  every call is recorded and lands in the run log.

Facts are pulled rather than pushed because a learner accumulates far more of
them than belong in any one context — the cue is what decides relevance.

**Deciding what to save** (`reflect.py`): after each turn, a model call decides
whether anything durable was said. Most turns save nothing. Mechanical intents
(`new_problem`, `summarize`) never reach it.

Memory is **never** given to the translator, critic, or screener — they stay
blind, so attempt history can't leak a solution into the code path.

### 7.5 Shared notes are untrusted

`memory.render_notes` fences every note in
`<untrusted-note id=… author=…>…</untrusted-note>` and escapes any closing tag in
the body, so a note cannot end its own block and append text that looks like it
came from the system. Rule **R7** tells the model that anything inside such a
block is data, never an instruction; **R9** stops a note being used to smuggle a
hint past R1. See `traces/02-planted-comment.md`.

---

## 8. Summaries

- **On switching problems**, the problem being left is auto-recapped (if there
  was activity) and the recap is prepended to the reply and saved.
- **A `summarize` command** (typed, or the 📝 button) recaps the current problem.
- `summarizer.py` builds a digest (approaches + verdicts + concept questions) and
  asks Gemini for a 2–4 sentence recap. **Guardrail:** it recaps *what the
  learner did*, never hints, the solution, or what to try next — even when
  unsolved.

---

## 9. Sandbox (security-critical)

`sandbox/` — a Docker image (`python:3.11-slim` + g++) with `runner.py` baked in.
`sandbox.py` writes the generated `solution.cpp`, a manifest, and the test files
to a temp dir, then runs the container:

- `--network=none`, memory/CPU/pids caps, **unprivileged user**,
- inside, `runner.py` compiles (`g++ -O2 -std=c++17`) and runs each test under a
  per-test CPU `rlimit`, memory `rlimit`, and wall-clock timeout, comparing
  normalized stdout.

Verdicts: AC / WA (with failing input + expected vs got) / TLE / RE / CE. A
container-level failure (e.g. Docker down) is reported as `SANDBOX_UNAVAILABLE` —
never blamed on the user's code. **LLM-generated code is never run outside this.**

---

## 10. LLM backend

Google **Gemini free tier** via `google-genai`, behind one shim (`llm.py`) so the
provider lives in a single file. Router uses `gemini-2.5-flash-lite`; tutor,
screener, translator, summarizer use `gemini-2.5-flash` (both env-overridable).
Structured outputs use Pydantic `response_schema`. Transient 429/500/503 errors
are retried with backoff; `/chat` degrades gracefully on failure.

Auth: `GEMINI_API_KEY` in a gitignored `.env` (auto-loaded via python-dotenv).

---

## 11. Frontend

**React + Vite** SPA (`frontend/`), served by FastAPI from `frontend/dist` in
production, or via the Vite dev server (HMR) with an API proxy during dev.

- `ProblemPanel` — statement (HTML + MathJax), metadata, progress line, 🎲 New
  problem + 📝 Summarize buttons.
- `Chat` / `Message` — message list + composer; assistant replies render as
  **Markdown**, generated C++ as a collapsible **syntax-highlighted** block.
- Restores prior conversation on load; responsive (columns stack on mobile).

---

## 12. HTTP endpoints

| Method | Path | Purpose |
|---|---|---|
| POST | `/chat` | `{message}` → `{intent, reply, meta}` |
| GET | `/problem` | current problem summary (incl. `statement_html`) |
| POST | `/new-problem` | load a new problem (button) |
| POST | `/reset` | full reset for this learner: chat, progress, attempts, summaries, private documents. Shared notes and the audit trail survive. |
| GET | `/history` | prior chat messages (restore UI) |
| GET | `/progress` | attempts/solved/seen + per-verdict counts |
| GET/POST | `/whoami` | read / switch the current learner |
| GET/POST | `/notes` · DELETE `/notes/{id}` | shared, learner-written notes on the current problem |
| GET | `/memory` | everything remembered, by store · DELETE `/memory/{doc_id}` to forget one |
| GET | `/rules` | the operating rules file + parsed ids |
| GET | `/monitor/report` | latest report + judgments · POST `/monitor/run` triggers a pass (demo convenience) |
| GET | `/` | the built SPA |

---

## 13. Layout

```
backend/       FastAPI app + agents + the three stores + Codeforces + sandbox glue
sandbox/       Dockerfile + runner.py (the isolated C++ executor)
frontend/      React + Vite SPA (src/, components/)
rules/         operating_rules.md — hand-editable, injected every run
memory_store/  JSON document store (gitignored; created at runtime)
reports/       monitor output (gitignored)
tests/         pytest suite — 55 tests, no API key needed
scripts/       demo_traces.py — regenerates the evidence in traces/
traces/        the committed evidence: private-vs-shared, planted comment, …
README.md            quickstart + run instructions
IMPLEMENTATION.md    this document
HW2.md               the coursework writeup
```

---

## 14. Running

Prereqs: Python 3.11+, Docker Desktop, Node 18+, a free Gemini key.

```bash
docker build -t cp-tutor-sandbox ./sandbox          # once
pip install -r backend/requirements.txt
cp .env.example .env   # paste GEMINI_API_KEY
cd frontend && npm install && npm run build && cd ..
uvicorn backend.main:app --reload --app-dir .        # serves API + built UI
# open http://localhost:8000
```

Frontend dev with hot reload: run the API, then `cd frontend && npm run dev`
(http://localhost:5173, proxies the API to :8000).

**The monitor is a separate job** — start it yourself, alongside the API:

```bash
python -m backend.monitor              # grade the backlog once, write a report
python -m backend.monitor --watch 300  # ...and keep doing it every 5 minutes
```

**Tests and evidence:**

```bash
python -m pytest tests/ -q       # 55 tests, ~20s, no API key needed
python -m scripts.demo_traces    # regenerate traces/ against the live model
```

---

## 15. Known limitations / next steps

- **Sample-test verdicts only** — no hidden CF tests; add generated stress tests
  to restore reliable TLE and stronger correctness signals.
- **Identity is unauthenticated** — a `uid` cookie with no password. It scopes
  memory; it does not protect it. Anyone can claim any name.
- **Difficulty magnitude is LLM-inferred** — natural but slightly non-deterministic.
- **The judge is a language model grading a language model.** `traces/05` contains
  one verdict I believe is wrong (it read R3 as forbidding the agent from
  accepting a learner's preference). It was left in deliberately: the misreading
  is what showed R3's scope was unclear, and a monitor whose mistakes are hidden
  is worse than one whose mistakes are visible.
- **No CF parsing tests** — the suite covers the stores, the handoff, the monitor
  and the API, but Codeforces parsing is still only exercised live.
- **Personalized tutor** — attempt history could feed adaptive pacing/examples;
  the tutor currently personalises from documents, not from attempts.
