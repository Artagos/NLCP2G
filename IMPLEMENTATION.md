# CP Tutor — Implementation

An agentic tutor that lets **non-coders** solve competitive-programming problems
by **describing their algorithm in plain words**. The system translates the
described approach into C++, runs it against real test cases, and reports back
faithfully — while a guardrailed tutor answers *general* concept questions but
never leaks how to solve the current problem.

Repo: https://github.com/Artagos/cp-tutor

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
       concept │ strategy │ solution │ new_problem │ summarize │ chitchat
          │         │         │            │            │
          ▼         ▼         ▼            ▼            ▼
        Tutor   (refuse)  Solution     load new     Summarizer
       (Gemini)           pipeline     (adaptive/    (per-problem
                          │            calibrated)    recap)
                          ▼
          Feasibility screen ─▶ C++ codegen ─▶ Sandbox (Docker + g++)
          (blind, INFEASIBLE)   (blind,          (AC/WA/TLE/RE/CE)
                                 UNCLEAR)
                                     │
                          Verdict explainer (no-hints)

 Problems: Codeforces (backend/codeforces.py)  ·  Memory: SQLite (backend/memory.py)
```

---

## 3. The chat pipeline (per message)

1. **Router** (`router.py`, Gemini `gemini-2.5-flash-lite`, structured output)
   classifies the message into one intent and, for `new_problem`, infers a
   `rating_delta`.
2. Dispatch (`main._handle`):
   - **concept** → `tutor.answer(...)` (allowed, general CS only).
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
   Sandbox (sandbox.py → Docker) ────────── infra down ─────▶ SANDBOX_UNAVAILABLE
   │
   ▼
   Verdict facts → verdict explainer (no-hints) → AC / WA / TLE / RE / CE
```

Three layers can stop before the sandbox, each giving specific feedback:
- **INFEASIBLE** — the described "solution" can't be carried out (not an
  algorithm, contradictory, needs unavailable data). *Slowness is never a reason
  to reject.*
- **UNCLEAR** — feasible but too vague/contradictory to translate faithfully.
- Only a clean, feasible, implementable description reaches the sandbox.

---

## 4. Backend modules

| File | Responsibility |
|---|---|
| `main.py` | FastAPI app, session cookie middleware, endpoints, dispatch, serves the built SPA |
| `llm.py` | Single Gemini shim (`google-genai`): `generate` / `generate_structured`, lazy client, retries on 429/500/503 |
| `router.py` | Intent + `rating_delta` classifier (structured output) |
| `tutor.py` | Guardrailed conceptual Q&A (sees statement, never a solution) |
| `screener.py` | Feasibility pre-screen (blind to the problem) |
| `translator.py` | NL → C++ (blind), gating, sandbox run, faithful verdict explanation |
| `summarizer.py` | Per-problem recap (no hints) |
| `sandbox.py` | Host side: build temp dir, invoke Docker, parse results |
| `problem.py` | `Problem` model + offline fallback problem |
| `codeforces.py` | Fetch + parse real problems (cloudscraper + BeautifulSoup) |
| `state.py` | Per-session current problem + adaptive/calibrated selection |
| `memory.py` | Per-session SQLite store (attempts, seen, messages, summaries) |
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

## 7. Memory (per session)

SQLite (`cp_tutor.db`, gitignored) via `memory.py`, keyed by an `sid` **cookie**
set by middleware (no login — memory = "this browser").

| Table | Holds |
|---|---|
| `session_current` | the current problem pointer per session |
| `seen` | every problem shown + a `solved` flag |
| `attempts` | one row per solution attempt (problem, rating, approach text, verdict, time) |
| `messages` | full chat log (role, content, intent, `problem_key`, time) |
| `summaries` | saved per-problem recaps |

**Written when:** a problem loads (`seen` + `session_current`), a solution runs
(`attempts`, drives the "attempt N" counter), any chat turn (`messages`).

**Read by (guardrail):**
- the **tutor** — prior concept-turn history (`tutor_history`),
- **problem selection** — solved ratings + seen keys (metadata only),
- the **summarizer** — a problem's attempts + concept questions,
- `/history` (restore chat), `/progress` (sidebar stats).

Memory is **never** given to the translator or screener — they stay blind, so
attempt history can't leak a solution into the code path.

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
| GET | `/history` | prior chat messages (restore UI) |
| GET | `/progress` | attempts/solved/seen + per-verdict counts |
| GET | `/` | the built SPA |

---

## 13. Layout

```
backend/    FastAPI app + agents + memory + Codeforces + sandbox glue
sandbox/    Dockerfile + runner.py (the isolated C++ executor)
frontend/   React + Vite SPA (src/, components/)
README.md            quickstart + run instructions
IMPLEMENTATION.md    this document
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

---

## 15. Known limitations / next steps

- **Sample-test verdicts only** — no hidden CF tests; add generated stress tests
  to restore reliable TLE and stronger correctness signals.
- **Single anonymous session per browser cookie** — no accounts/cross-device.
- **Difficulty magnitude is LLM-inferred** — natural but slightly non-deterministic.
- **No automated test suite yet** — verification so far has been manual/live; a
  `tests/` suite (sandbox verdicts, CF parsing on fixtures, mocked-LLM routing)
  is the obvious next addition.
- **Personalized tutor** — the stored attempt history could feed adaptive
  pacing/examples (not yet wired in).
