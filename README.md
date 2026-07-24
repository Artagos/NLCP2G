# NLCP2G — Natural Language Competitive Programming PlayGround

An agentic "explain your solution" competitive-programming coach.

A chat-bot that lets **non-coders** solve competitive programming problems by
*describing their algorithm in plain words*. The system translates the described
approach into C++, runs it against a real test suite, and reports back what
happened — **faithfully, and without ever suggesting a better algorithm.**

Alongside the translator there's a **guardrailed tutor** that answers general CS
questions ("what is a hash map?") but refuses anything problem-specific ("should
I use a hash map here?"). No hints, no strategy — the learner does the thinking.

## The two capabilities

1. **Translator (pure executor)** — takes the user's described algorithm →
   generates C++ → compiles & runs in a sandbox against the test suite →
   reports the verdict (AC / WA / TLE / RE / CE). It implements *exactly* what
   the user described. It never optimizes, corrects the algorithm, or names a
   better one. When a solution TLEs, that timeout is the teaching signal.

   **The whole translate path is blind to the problem.** Every step below is
   given only the raw I/O format and the user's algorithm — never the statement,
   constraints, or intended solution — so nothing can infer a smarter approach or
   fill algorithmic gaps:

   1. **Feasibility screen** (`screener.py`) — runs *first*, before any code. Is
      the described solution even possible/feasible to carry out on the given
      inputs? Rejects non-algorithms, contradictions, and references to
      unavailable data with `verdict: INFEASIBLE` + specific feedback. It does
      **not** reject slow approaches — a naive O(n²) is feasible (that's the point).
   2. **Codegen gate** (`translator.py`) — if the approach is feasible but too
      vague/contradictory to translate faithfully, it **gates** (`verdict:
      UNCLEAR`) and says exactly what it couldn't turn into code, instead of guessing.
   3. **Run** — only a clean, feasible, implementable description reaches the
      sandbox.

2. **Tutor (guardrailed Q&A)** — answers general, abstract CS questions. It is
   deliberately **not given the problem's intended solution**, so it can't leak
   it. Problem-specific / strategy questions get a polite refusal.

A cheap **intent router** decides which agent each message goes to.

The LLM backend is **Google Gemini** (free tier) via the `google-genai` SDK,
routed through a single shim (`backend/llm.py`) so the provider is swappable.

## Architecture

```
 Chat UI ──▶ Router ──┬─▶ Tutor        conceptual Q&A, no hints
   (Gemini flash-lite)├─▶ Solution pipeline (all blind to the problem):
                      │      Feasibility screen ─▶ Codegen gate ─▶ C++ ─▶ Sandbox
                      │      (INFEASIBLE)          (UNCLEAR)              (AC/WA/TLE/RE/CE)
                      └─▶ (refusal / chitchat handled inline)
```

## Problems: live from Codeforces

Problems are fetched from **Codeforces** (`backend/codeforces.py`). The CF API
lists problems but doesn't expose statements or tests, and the problem pages are
behind Cloudflare — so we use `cloudscraper` + BeautifulSoup to fetch and parse
the statement, the input/output specification, the sample tests, and the
time/memory limits. The user can switch problems anytime — the New-problem
button, or just say *"give me another problem"* (router intent `new_problem`).
The current problem is held server-side (`backend/state.py`).

**Verdicts run against the sample tests** (AC/WA/RE/CE) — CF's full hidden tests
aren't public on any judge. The reliable TLE-on-huge-input signal that the old
calibrated problem had would need generated stress tests (a natural next step).
The original "Count Pairs With Sum K" problem survives in `backend/problem.py`
only as an **offline fallback** when Codeforces is unreachable.

## Layout

```
backend/
  main.py         FastAPI app + /chat, /problem, /new-problem
  llm.py          Gemini shim (the only LLM-provider-specific file)
  codeforces.py   fetch + parse real problems (cloudscraper + BeautifulSoup)
  memory.py       per-session SQLite store (attempts, progress, chat, seen)
  state.py        per-session current problem + adaptive selection
  router.py       intent classifier (concept/strategy/solution/new_problem/chitchat)
  tutor.py        guardrailed conceptual Q&A
  screener.py     feasibility pre-screen (blind to the problem)
  translator.py   NL approach → C++ → sandbox → faithful verdict
  sandbox.py      host-side: build temp dir, invoke Docker, parse results
  problem.py      Problem model + offline fallback problem
  prompts.py      system prompts (pure-executor + no-hints rules live here)
  requirements.txt
sandbox/
  Dockerfile      python:3.11-slim + g++, bakes in runner.py
  runner.py       runs inside the container: compile + run tests with limits
frontend/         React + Vite single-page app
  index.html      Vite entry (loads MathJax + the bundle)
  vite.config.js  dev-server API proxy → :8000; build → dist/
  package.json
  src/
    main.jsx      React entry
    App.jsx       state + data loading, wires the two panels
    api.js        fetch wrappers for the backend endpoints
    styles.css
    components/
      ProblemPanel.jsx   statement (HTML + MathJax), meta, progress, New-problem
      Chat.jsx           message list + composer
      Message.jsx        Markdown + syntax-highlighted C++ per bubble
```

## Memory (per session)

Each browser gets a `sid` cookie; everything below is persisted in a SQLite DB
(`cp_tutor.db`, gitignored) keyed by it — `backend/memory.py`:

- **Attempts & progress** — every solution attempt (problem, approach, verdict,
  time) is recorded. The chat shows "attempt N"; `GET /progress` returns
  solved/seen/attempt counts and a per-verdict breakdown (shown in the sidebar).
- **Persistent conversation** — the full chat is stored and restored via
  `GET /history`, so a refresh keeps context; the tutor also recalls prior
  concept turns.
- **Adaptive problem selection** — "new problem" excludes already-seen problems
  and targets a difficulty band derived from the ratings you've solved (starts
  800–1000, climbs as you solve harder ones). See `state._band`.

**Guardrail:** memory is only read by the tutor (which already sees the
statement) and the problem-selection logic (metadata only). It is **never**
handed to the translator or screener — they stay blind, so no solution can leak.

## Running it

Prereqs: Python 3.11+, Docker Desktop running, a free Gemini API key
(https://aistudio.google.com/apikey).

```bash
# 1. Build the sandbox image (once)
docker build -t cp-tutor-sandbox ./sandbox

# 2. Install backend deps
pip install -r backend/requirements.txt

# 3. Set your free Gemini key — either put it in a .env file (auto-loaded):
#      cp .env.example .env   # then edit .env and paste your key
#    or export it:
#      (PowerShell)  $env:GEMINI_API_KEY = "..."
#      (bash)        export GEMINI_API_KEY=...
#    NOTE: .env is gitignored. Never put a real key in .env.example (tracked).

# 4. Build the React frontend (once, or after UI changes)
cd frontend && npm install && npm run build && cd ..

# 5. Run the API (also serves the built frontend from frontend/dist)
uvicorn backend.main:app --reload --app-dir .

# 6. Open the UI
#    visit http://localhost:8000
```

**Frontend dev (hot reload):** instead of steps 4–6, run the API
(`uvicorn backend.main:app --app-dir .`) and, in another terminal,
`cd frontend && npm run dev` — then open http://localhost:5173. Vite proxies the
API calls to the backend on :8000, so cookies/session work.

Then chat. Try:
- "what is a hash map?"          → tutor answers (allowed)
- "should I use a hash map?"     → refused (problem-specific)
- describe a real approach to the current Codeforces problem → the pipeline
  screens it, builds C++, and runs it against the sample tests.
- "give me another problem"      → switches to a new (adaptively chosen) problem.

## Design notes / where to take it next

- **Guardrail** lives in `router.py` (intent classification) + the fact that
  `tutor.py`'s context never contains a solution. Strengthen by adding
  adversarial test prompts.
- **Time-limit calibration:** `problem.py` sets a limit calibrated so the
  intended solution passes comfortably and the naive one fails clearly.
- **Sandbox** is the security-critical piece — it runs LLM-generated code.
  It runs with `--network=none`, memory/CPU caps, and per-test wall-clock
  timeouts. Never run generated code outside it.
- Multi-problem support: make `problem.py` a lookup and pass a `problem_id`
  through the API. The agents already take the problem as a parameter.
