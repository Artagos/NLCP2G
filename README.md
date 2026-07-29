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
 Chat UI ──▶ Router ──┬─▶ Tutor        conceptual Q&A + memory, no hints
   (Gemini flash-lite)│      push: operating rules · pull: facts, shared notes
                      ├─▶ Solution pipeline (all blind to the problem):
                      │      Feasibility screen ─▶ Executor ⇄ Critic ─▶ Sandbox
                      │      (INFEASIBLE)      (UNCLEAR)  (fidelity)  (AC/WA/TLE/RE/CE)
                      └─▶ (refusal / chitchat handled inline)
                                       │
                                  run log ──▶ Monitor (separate job, LLM-as-judge)
```

**Three memory stores**, each fitting what it holds — SQLite for the structured
domain model, a JSON document store for what the agent writes in its own words,
and markdown for operating rules an admin edits by hand. **A fidelity critic**
reviews every generated program against the learner's exact words. **A monitor**
grades past runs out of band. See [`HW2.md`](HW2.md) and [`traces/`](traces/).

## Problems: live from Codeforces

Problems are fetched from **Codeforces** (`backend/codeforces.py`). The CF API
lists problems but doesn't expose statements or tests, and the problem pages are
behind Cloudflare — so we use `cloudscraper` + BeautifulSoup to fetch and parse
the statement, the input/output specification, the sample tests, and the
time/memory limits. The user can switch problems anytime — the New-problem
button, or just say *"give me another problem"* (router intent `new_problem`).
The current problem is held server-side (`backend/state.py`).

**Verdicts on scraped problems run against the published samples** (AC/WA/RE/CE)
— CF's hidden tests aren't public on any judge.

### …and an offline bank when it isn't reachable

Codeforces statement pages currently sit behind an anti-bot challenge, so in
practice problems come from **`backend/bank.py`**: seven original problems
(written for this project) spanning ratings 800–1500.

Because we own the reference solution for these, each one **generates its own
stress test** — which restores the real TLE signal. Describe "check every pair"
on a million elements and it times out, and you find out. Scraped samples are far
too small to tell you that, and that feedback is the whole point of the product.
Time limits are calibrated against measured sandbox cost, and tests are seeded so
a restart reproduces them byte for byte.

## Layout

```
backend/
  main.py         FastAPI app, identity, endpoints, dispatch, run logging
  llm.py          Gemini shim (the only LLM-provider-specific file) + tool calling
  codeforces.py   fetch + parse real problems (cloudscraper + BeautifulSoup)
  memory.py       relational store: problems, attempts, notes, runs, scratchpad
  docstore.py     document store: agent-written facts + rules, private and shared
  rules.py        markdown store: loads rules/operating_rules.md, live on edit
  state.py        per-learner current problem + adaptive selection
  router.py       intent classifier (concept/meta/strategy/solution/…)
  tutor.py        guardrailed Q&A; pushes rules, pulls facts and notes
  screener.py     feasibility pre-screen (blind to the problem)
  translator.py   the executor: NL approach → C++ → critic loop → sandbox
  critic.py       the fidelity critic (second agent) + the Handoff object
  reflect.py      decides, per turn, whether anything is worth remembering
  monitor.py      the out-of-band judge (python -m backend.monitor)
  sandbox.py      host-side: build temp dir, invoke Docker, parse results
  problem.py      Problem model + the single last-resort fallback
  bank.py         offline bank: 7 original problems with generated stress tests
  prompts.py      system prompts (pure-executor + no-hints rules live here)
  requirements.txt
rules/
  operating_rules.md   hand-editable rules, injected on every run
tests/            153 pytest tests (no API key or bot token needed)
  bot.py          the Telegram bot runtime (channel, queue, admin routing)
  channels.py     Channel interface + Telegram client + FakeChannel
  turnqueue.py    per-learner FIFO: what happens mid-turn
  triggers.py     the nudge decision — Fire or Silence(reason)
  scheduler.py    the background heartbeat trigger
  worker.py       out-of-process sandbox worker (needs Docker)
  hooks.py        the signed run-complete webhook
  admin.py        the privileged operator subagent
scripts/
  demo_traces.py  regenerates traces/ against the live model
  demo_hw3.py     regenerates the channel/trigger/queue/admin traces
traces/           committed evidence: private-vs-shared, planted comment, …
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

## Memory — three stores

Identity is a `uid` cookie (default `guest`), switchable from the UI. No
password: the name **scopes** memory, it doesn't protect anything.

**1 · Relational — SQLite** (`backend/memory.py`, `cp_tutor.db`, gitignored).
The structured domain model: `problems` (queryable by rating and tag),
`attempts` (one row per try, with its verdict), `seen`, `messages`, `summaries`,
plus shared `notes`, the `runs` log, and the agents' `scratchpad`.

**2 · Non-relational — JSON documents** (`backend/docstore.py`, `memory_store/`).
What the agent writes in its own words:
- a **fact** is saved with a *cue* — the keywords a future request would contain
  — and is pulled back when the cue matches;
- a **rule** is attached on every run for its owner and never retrieved.

`backend/reflect.py` decides after each turn whether anything was worth saving.
Most turns save nothing.

**3 · Markdown — operating rules** (`rules/operating_rules.md`). Static rules,
pushed into every user-facing run. Edit the file and the next message picks it
up — no restart. Rule ids (`R1`…`R12`) are recorded per run and cited by the
monitor.

**Push and pull.** Rules are *pushed* (always present). Facts and shared notes
are *pulled* mid-run through real tools the model calls itself
(`retrieve_memory`, `read_problem_notes`).

**Private vs shared.** A learner's facts live in their own file, so another
learner's agent cannot read them — there is no filter to get wrong. Notes are
shared: one learner writes, everyone's agent can read. They arrive fenced as
`<untrusted-note author=…>` and are treated as data, never instructions
(see [`traces/02-planted-comment.md`](traces/02-planted-comment.md)).

**Guardrail:** memory is **never** handed to the translator, critic, or screener
— they stay blind, so no solution can leak into the code path.

## Tests and evidence

```bash
python -m pytest tests/ -q       # 55 tests, ~20s, no API key needed
python -m scripts.demo_traces    # regenerate traces/ against the live model
python -m backend.monitor        # grade the run log, write reports/monitor-*.md
```

[`traces/`](traces/) holds live runs proving the claims the unit tests can't:
private stays private, shared reaches everyone, a planted comment gets quoted
rather than obeyed, and the monitor's own findings.

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

## Running it on Telegram (HW3)

The agent also lives on a chat channel, with a background heartbeat and an
out-of-process sandbox worker. Three processes:

```bash
# 0. Create a bot with @BotFather, put the token in .env (never a personal
#    account). Add your own chat id to CP_TUTOR_ADMIN_CHAT_IDS to reach the
#    operator agent — empty means nobody is an admin.

uvicorn backend.main:app --app-dir .      # API + the run-complete webhook
python -m backend.bot                     # the chat bot (long polling)
python -m backend.worker                  # the sandbox worker (needs Docker)
python -m backend.scheduler --interval 900   # the background nudge trigger
```

Only the worker needs Docker, which is why it is a separate process: the bot,
API and scheduler can live on a small always-on host while execution happens
wherever a worker runs. With no worker running, jobs simply queue — the learner
was already told their run was accepted, and the trigger stays quiet while a run
is in flight.

**Firing the triggers on purpose:**

```bash
python -m backend.scheduler --once --now 2026-07-29T03:00   # quiet-hours branch
python -m backend.scheduler --once --dry-run                # decide, send nothing
python -m backend.worker --once                             # drain the run queue
python -m backend.bot --fake                                # no token needed
```

In the admin chat: `/tick` (forces a nudge pass, dry-run by default), `/queue`,
and `/admin <request>` for the operator agent. The boundary between the ordinary
and privileged paths is written down in [`ADMIN_BOUNDARY.md`](ADMIN_BOUNDARY.md).

Evidence for all of it: [`traces/06`](traces/06-triggers-and-silence.md),
[`07`](traces/07-queue-and-webhook.md), [`08`](traces/08-admin-boundary.md),
regenerated by `python -m scripts.demo_hw3`.
