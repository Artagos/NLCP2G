# Running NLCP2G locally

Everything runs in containers. You need Docker, and nothing else — no Python, no
Node, no npm on the host. The web UI is built inside the image.

## Once

```bash
cp .env.example .env          # then fill in the two values below
docker compose build sandbox  # the C++ jail
docker compose build          # the app image
```

`.env` needs at minimum:

```
GEMINI_API_KEY=...                 # https://aistudio.google.com/apikey
TELEGRAM_BOT_TOKEN=...             # @BotFather -> /newbot
CP_TUTOR_WEBHOOK_SECRET=...        # any random string
CP_TUTOR_ADMIN_CHAT_IDS=           # your chat id, to reach /admin. empty = nobody
```

## Every time

```bash
docker compose up -d
docker compose logs -f app
```

- **Web UI** — http://localhost:8000
- **Chat** — whichever bot the token belongs to
- **Stop** — `docker compose down` (data survives; see below)

## What comes up

| Service | Responsible for |
|---|---|
| `app` | Telegram poll loop, FastAPI, the run-complete webhook, the web UI |
| `worker` | executing learner code in the sandbox |
| `scheduler` | the background nudge trigger, every 15 min |
| `monitor` | grading shipped runs, every 5 min |

`app` runs the poll loop and the API **in one process** deliberately. The webhook
reaches a learner's chat through the channel object the bot registered at
startup, and that object only exists inside the bot's own process. Split them and
every verdict is accepted, recorded, and then silently dropped.

## Where the state lives

`./data/` on the host, bind-mounted at `/data`:

```
data/cp_tutor.db      the relational store
data/memory_store/    the agent-written facts and rules
data/reports/         monitor output
```

`docker compose down` leaves it alone. `docker compose down -v` also drops the
sandbox scratch volume, which is fine — it only ever holds in-flight jobs.

`./rules/` is bind-mounted too, so editing `rules/operating_rules.md` on the host
changes agent behaviour on the **next message**. No rebuild, no restart. That
hot reload is a feature of the design, not a dev convenience.

## The one genuinely awkward bit

The worker starts sandbox containers, but it is itself a container. It talks to
the host daemon through the mounted socket — which means any path it names is
resolved against the **host** filesystem, not its own. A bind mount of its own
`/tmp` would silently produce an empty `/work`: no manifest, no results, and
every run returning an infra error instead of a verdict.

So in containers it stops naming paths. Both the worker and each sandbox mount
the same **named volume** (`nlcp2g_sandboxwork`), the job gets a subdirectory
inside it, and the image's entrypoint is overridden to point `runner.py` there:

```
docker run --rm --network=none --memory=384m --cpus=1 --pids-limit=64 \
  -v nlcp2g_sandboxwork:/workroot \
  --entrypoint python3 cp-tutor-sandbox /runner.py /workroot/cp_tutor_<id>
```

On the host, `run_cpp` still bind-mounts by path exactly as before. Both argv
shapes are pinned by `tests/test_sandbox_mounts.py`, including an assertion that
neither strategy quietly loses the containment flags.

This is why `compose down -v` is safe but `docker volume rm nlcp2g_sandboxwork`
mid-run is not.

## Running the tests

The suite needs no token, no API key and no daemon:

```bash
docker compose run --rm --entrypoint "" app sh -c "pip install pytest -q && python -m pytest tests -q"
```

The image excludes `tests/`, so on the host with Python available `python -m
pytest tests -q` is the shorter path.

## Troubleshooting

**`port is already allocated`** — something else holds 8000. A bare
`python -m backend.bot` from an earlier session is the usual culprit.

**Verdicts never arrive** — check `docker compose logs worker`. If Docker is
unreachable from inside it, the socket mount is the thing to look at. Jobs simply
queue while the worker is down; nothing is lost, and the learner was already told
their run was accepted.

**`ConnectionResetError [WinError 10054]` every few minutes** — cosmetic.
Telegram closing each long-poll window; asyncio logs the socket teardown at
ERROR. The bot is fine.

**Rule edits not taking effect** — they apply on the next message, not
retroactively, and only for `./rules` on the host (the copy baked into the image
is shadowed by the bind mount).
