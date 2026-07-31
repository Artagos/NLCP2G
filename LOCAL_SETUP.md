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

| Service | Responsible for | Docker socket |
|---|---|---|
| `app` | Telegram poll loop, FastAPI, the run-complete webhook, the web UI | yes |
| `worker` | executing learner code for the chat path | yes |
| `scheduler` | the background nudge trigger, every 15 min | no |
| `monitor` | grading shipped runs, every 5 min | no |

`app` runs the poll loop and the API **in one process** deliberately. The webhook
reaches a learner's chat through the channel object the bot registered at
startup, and that object only exists inside the bot's own process. Split them and
every verdict is accepted, recorded, and then silently dropped.

**Two services start sandbox containers, not one.** The chat path defers a run to
the worker, because a conversation cannot hold a 20-second request open. The web
path does not — a browser can, so `/chat` compiles and runs inline. `app`
therefore needs the socket as much as `worker` does; without it, every attempt
through the web UI returns `SANDBOX_UNAVAILABLE` while Telegram works fine, which
reads as "Docker is broken" and is really "that container has no way to reach
it". `scheduler` and `monitor` execute nothing and are deliberately socket-free.

Mounting the Docker socket is effectively root on the host. That is acceptable
here because this is a local dev stack — and no worse than running the same code
as a bare process — but it is not a shape to expose publicly unchanged.

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

A container starting a sibling container cannot name a path. `app` and `worker`
talk to the **host** daemon through the mounted socket, so any path they hand it
is resolved against the host filesystem, not their own. A bind mount of their own
`/tmp` silently produces an empty `/work`: no manifest, no results, and every run
returning an infra error instead of a verdict.

So in containers they stop naming paths. The starting container and the sandbox
mount the same **named volume** (`nlcp2g_sandboxwork`), the job gets a
subdirectory inside it, and the image's entrypoint is overridden to point
`runner.py` there:

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

The suite needs no token, no API key and no daemon. `tests/` is deliberately not
in the image, so mount it:

```bash
docker compose run --rm --no-deps --entrypoint "" \
  -v "${PWD}/tests:/app/tests" app python -m pytest tests -q
```

pytest is already installed in the image. On a host with Python available,
`python -m pytest tests -q` is the shorter path.

## Troubleshooting

**`port is already allocated`** — something else holds 8000. A bare
`python -m backend.bot` from an earlier session is the usual culprit.

**Verdicts never arrive (chat)** — check `docker compose logs worker`. Jobs
simply queue while the worker is down; nothing is lost, and the learner was
already told their run was accepted.

**`SANDBOX_UNAVAILABLE` (web)** — the `app` container cannot reach the daemon.
Check it directly:

```bash
docker compose exec app docker ps        # should list containers, not error
docker compose exec app printenv CP_TUTOR_SANDBOX_VOLUME
```

Blank output or a socket error means the `/var/run/docker.sock` mount or the
`sandboxwork` volume is missing from the `app` service.

**`ConnectionResetError [WinError 10054]` every few minutes** — cosmetic.
Telegram closing each long-poll window; asyncio logs the socket teardown at
ERROR. The bot is fine.

**Rule edits not taking effect** — they apply on the next message, not
retroactively, and only for `./rules` on the host (the copy baked into the image
is shadowed by the bind mount).
