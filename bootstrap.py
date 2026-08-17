#!/usr/bin/env python3
"""One command that brings NLCP2G up on a machine that has only Python.

    python bootstrap.py              # the full container stack
    python bootstrap.py --web-only   # web UI + API, no Telegram token needed
    python bootstrap.py --check      # diagnose, change nothing
    python bootstrap.py --no-docker  # host venv, for when Docker is not an option
    python bootstrap.py --tests      # run the suite
    python bootstrap.py --down       # stop everything

`LOCAL_SETUP.md` remains the explanation of *why* the stack is shaped the way it
is; this file is the same instructions in a form that also checks its work. It
reimplements nothing — every step here is a documented command, run for you,
with the failure modes turned into sentences instead of stack traces.

**Why this file is written in old Python.** It uses no f-strings, no annotations
and no library outside the standard one. The point of a bootstrap script is to
be the *first* thing that runs on an unknown machine, and a script that cannot
be parsed cannot tell you why: on Python 3.5 an f-string is a SyntaxError raised
before line 1 executes, so the version check never gets its chance and the user
is told "invalid syntax" when the real answer is "your Python is too old".
Everything below parses back to 2.7 so that the check can always speak. The rest
of the codebase is modern and should stay that way; this is the doormat, and the
doormat has different requirements from the house.

**What it will not do.** It never overwrites a value you already put in `.env`,
never touches `data/`, and never runs `docker compose down -v`. Setup scripts
that clean up after you are how people lose a database they had five minutes of
work in. Anything destructive is left for you to type yourself.
"""

import sys

# Before anything else, and deliberately before the imports below: `secrets` and
# `subprocess.run` are 3.6, `venv` is 3.3, and a machine older than that needs a
# sentence rather than a traceback.
if sys.version_info < (3, 6):
    sys.stderr.write(
        "NLCP2G bootstrap needs Python 3.6 or newer to run, and you have %s.\n"
        "The stack itself runs in Docker and does not care what Python you have "
        "on the host, so upgrading only this is enough:\n"
        "  https://www.python.org/downloads/\n"
        % ".".join(str(n) for n in sys.version_info[:3]))
    raise SystemExit(1)

import argparse
import os
import platform
import re
import secrets
import shutil
import socket
import subprocess
import tempfile
import time

try:
    from urllib.request import urlopen
except ImportError:                                   # pragma: no cover - py2
    from urllib2 import urlopen                       # type: ignore

# The host Python the --no-docker path needs. The container path needs nothing:
# the image pins 3.12 itself, which is the entire argument for preferring it.
HOST_PYTHON_MIN = (3, 11)

# Values in .env.example that mean "you still have to fill this in". Matched
# exactly, so a real key that happens to contain "change-me" is left alone.
PLACEHOLDERS = ("", "...", "change-me", "paste-your-botfather-token-here")

DEFAULT_PORT = 8000
HEALTH_TIMEOUT_S = 180          # a cold `pip install` inside the image is slow

_DRY_RUN = False


# --------------------------------------------------------------------------
# saying things
# --------------------------------------------------------------------------
# No colour and no unicode. This runs in cmd.exe, in PowerShell, over ssh, and
# inside CI logs, and a box-drawing character that renders as mojibake in one of
# them costs more than the prettiness is worth.

_STEP = [0]


def step(text):
    _STEP[0] += 1
    print("")
    print("== %d. %s" % (_STEP[0], text))


def say(text):
    print("   " + text)


def ok(text):
    print("   [ok] " + text)


def warn(text):
    print("   [!!] " + text)


def die(text, hint=None):
    print("")
    print("   [XX] " + text)
    if hint:
        for line in hint.strip().splitlines():
            print("        " + line)
    raise SystemExit(1)


def run(argv, cwd=None, check=True, capture=False, quiet=False):
    """Run a command, showing it first so the script teaches as it goes.

    Everything this does is something you could have typed, and printing the
    argv means a failure here leaves you holding the command that failed rather
    than a description of it.
    """
    if not quiet:
        say("$ " + " ".join(_quote(a) for a in argv))
    if _DRY_RUN:
        return 0 if not capture else ""
    if capture:
        proc = subprocess.Popen(argv, cwd=cwd, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT)
        out = proc.communicate()[0].decode("utf-8", "replace")
        if check and proc.returncode != 0:
            die("that command failed (exit %d)." % proc.returncode, out[-2000:])
        return out
    code = subprocess.call(argv, cwd=cwd)
    if check and code != 0:
        die("that command failed (exit %d)." % code)
    return code


def _quote(arg):
    if arg == "":
        return '""'
    return '"' + arg + '"' if " " in arg else arg


def probe(argv):
    """True if the command exists and exits 0. Used for capability tests only."""
    try:
        with open(os.devnull, "wb") as null:
            return subprocess.call(argv, stdout=null, stderr=null) == 0
    except Exception:
        return False


# --------------------------------------------------------------------------
# the repository
# --------------------------------------------------------------------------

def repo_root():
    """Where this script lives, verified to be the repo rather than assumed.

    Checked rather than trusted because the most common way to run a bootstrap
    script wrong is to run a copy of it from somewhere else, and the resulting
    `docker compose` error names a missing file instead of the mistake.
    """
    here = os.path.dirname(os.path.abspath(__file__))
    for marker in ("docker-compose.yml", "backend", ".env.example"):
        if not os.path.exists(os.path.join(here, marker)):
            die("this does not look like the NLCP2G repository: %s is missing "
                "from %s." % (marker, here),
                "Run bootstrap.py from inside a clone, not from a copy of the "
                "file on its own.")
    return here


def features(root):
    """What this checkout happens to contain.

    The repository grows a branch at a time — retrieval, the eval harness and
    the corpus arrived after the container stack did — so the script asks the
    working tree what exists instead of hard-coding one branch's layout. A hint
    that is only printed when the file backing it is present cannot go stale.
    """
    j = os.path.join
    return {
        "eval_reqs": os.path.exists(j(root, "requirements-eval.txt")),
        "corpus": os.path.isdir(j(root, "corpus")),
        "corpus_index": os.path.isdir(j(root, "corpus", "index")),
        "build_index": os.path.exists(j(root, "scripts", "build_index.py")),
        "tests": os.path.isdir(j(root, "tests")),
        "frontend_dist": os.path.isdir(j(root, "frontend", "dist")),
    }


# --------------------------------------------------------------------------
# .env
# --------------------------------------------------------------------------

def read_env(path):
    values = {}
    if not os.path.exists(path):
        return values
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            values[key.strip()] = value.strip()
    return values


def patch_env(path, updates):
    """Set keys in `.env`, preserving every comment, order and untouched value.

    Rewriting the file wholesale would be four lines shorter and would throw
    away the explanations in `.env.example`, which are the only place several of
    these variables are described. A key that is already present is edited in
    place; one that is absent is appended under a heading.
    """
    if not updates:
        return
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        lines = fh.read().splitlines()

    remaining = dict(updates)
    out = []
    for line in lines:
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            key = stripped.split("=", 1)[0].strip()
            if key in remaining:
                out.append("%s=%s" % (key, remaining.pop(key)))
                continue
        out.append(line)

    if remaining:
        out.append("")
        out.append("# added by bootstrap.py")
        for key in sorted(remaining):
            out.append("%s=%s" % (key, remaining[key]))

    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(out) + "\n")


def unset(values, key):
    return values.get(key, "") in PLACEHOLDERS


def ensure_env(root, web_only, assume_yes):
    """Create `.env` from the example, then make sure it can actually start."""
    path = os.path.join(root, ".env")
    example = os.path.join(root, ".env.example")

    if not os.path.exists(path):
        if _DRY_RUN:
            say("would copy .env.example -> .env")
        else:
            shutil.copyfile(example, path)
        ok("created .env from .env.example")
    else:
        ok(".env already exists (left alone; only missing values are filled)")

    values = read_env(path)
    updates = {}

    # The webhook secret only has to be unguessable, so there is no reason to
    # make a human invent one. Anything the machine can decide, it decides.
    if unset(values, "CP_TUTOR_WEBHOOK_SECRET"):
        updates["CP_TUTOR_WEBHOOK_SECRET"] = secrets.token_urlsafe(32)
        ok("generated CP_TUTOR_WEBHOOK_SECRET")

    # ...and the API key is the one thing nobody can generate for you.
    if unset(values, "GEMINI_API_KEY"):
        key = ""
        if not assume_yes and sys.stdin.isatty() and not _DRY_RUN:
            print("")
            print("   A free Gemini API key is required. Get one at:")
            print("     https://aistudio.google.com/apikey")
            try:
                key = input("   Paste it here (or press Enter to skip): ").strip()
            except (EOFError, KeyboardInterrupt):
                key = ""
        if key:
            updates["GEMINI_API_KEY"] = key
            ok("stored GEMINI_API_KEY in .env")
        else:
            warn("GEMINI_API_KEY is not set. The stack will start and the web UI "
                 "will load, but every model call will fail.")
            warn("Put a key in .env and re-run, or `docker compose restart app`.")

    # The gap LOCAL_SETUP.md leaves open: `app` runs the Telegram poll loop and
    # the API in one process, so a missing token is not "no chat bot", it is a
    # crash-looping container and no web UI at all. Rather than document that,
    # detect it and take the web-only route, which needs no token.
    token_missing = unset(values, "TELEGRAM_BOT_TOKEN")
    if token_missing and not web_only:
        warn("TELEGRAM_BOT_TOKEN is not set.")
        say("The `app` service runs the chat poll loop and the API in the SAME")
        say("process, so without a token it exits at startup and the web UI")
        say("never comes up either. Two ways forward:")
        say("  - create a bot with @BotFather, put the token in .env, re-run; or")
        say("  - run the web UI on its own, which needs no token.")
        if assume_yes or not sys.stdin.isatty() or _DRY_RUN:
            web_only = True
            say("--> continuing in web-only mode.")
        else:
            try:
                answer = input("   Start in web-only mode? [Y/n] ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                answer = "y"
            if answer in ("", "y", "yes"):
                web_only = True
            else:
                die("nothing started.",
                    "Put TELEGRAM_BOT_TOKEN in .env and run bootstrap.py again.")

    # Not merely "skip the write": under --dry-run the file may never have been
    # copied, so patching it would fail on a machine that has no .env yet, which
    # is precisely the machine most likely to be running a dry run first.
    if _DRY_RUN:
        if updates:
            say("would set in .env: " + ", ".join(sorted(updates)))
        return web_only

    patch_env(path, updates)
    return web_only


# --------------------------------------------------------------------------
# docker
# --------------------------------------------------------------------------

def compose_command():
    """The compose invocation this machine actually has, or None.

    v2 is a `docker` subcommand and v1 is a separate `docker-compose` binary.
    Both are still in the wild, and the difference is invisible until a command
    fails, so it is resolved once here and threaded through.
    """
    if shutil.which("docker") and probe(["docker", "compose", "version"]):
        return ["docker", "compose"]
    if shutil.which("docker-compose"):
        return ["docker-compose"]
    return None


def compose_version(argv):
    try:
        out = subprocess.check_output(argv + ["version"],
                                      stderr=subprocess.STDOUT)
        text = out.decode("utf-8", "replace")
    except Exception:
        return None
    found = re.search(r"v?(\d+)\.(\d+)\.(\d+)", text)
    return tuple(int(n) for n in found.groups()) if found else None


def docker_install_hint():
    system = platform.system()
    if system == "Windows":
        return ("Install Docker Desktop: https://docs.docker.com/desktop/install/windows-install/\n"
                "Then start it and wait for the whale icon to stop animating.")
    if system == "Darwin":
        return ("Install Docker Desktop: https://docs.docker.com/desktop/install/mac-install/\n"
                "Then open it from Applications and wait for it to report Running.")
    return ("Install Docker Engine: https://docs.docker.com/engine/install/\n"
            "Then `sudo systemctl start docker`, and add yourself to the docker\n"
            "group (`sudo usermod -aG docker $USER`) so this works without sudo.")


def check_docker(required):
    """Is there a working daemon? Returns the compose argv, or None."""
    if not shutil.which("docker"):
        if required:
            die("Docker is not installed, or not on PATH.", docker_install_hint())
        warn("Docker not found.")
        return None

    if not probe(["docker", "info"]):
        if required:
            die("Docker is installed but the daemon is not reachable.",
                "Start Docker Desktop (or `sudo systemctl start docker`) and "
                "wait until\n`docker info` succeeds, then run this again.")
        warn("Docker is installed but the daemon is not running.")
        return None

    argv = compose_command()
    if argv is None:
        if required:
            die("Docker works, but Docker Compose is not available.",
                "Docker Desktop ships it. On a plain Engine install:\n"
                "  https://docs.docker.com/compose/install/linux/")
        warn("Docker Compose not available.")
        return None

    ok("docker daemon reachable, using `%s`" % " ".join(argv))
    return argv


def image_exists(name):
    return probe(["docker", "image", "inspect", name])


def port_is_taken(port):
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(0.4)
    try:
        return sock.connect_ex(("127.0.0.1", port)) == 0
    finally:
        sock.close()


def overlay_file(root, web_only, port):
    """A generated compose overlay, or None when the defaults already fit.

    Written to a temp directory rather than the repo so that it cannot end up
    committed, and cannot be mistaken later for a file somebody hand-wrote.

    `ports` needs `!override` because compose *concatenates* list-valued keys
    when merging files: a plain second entry would publish 8000 as well as the
    port you asked for, and 8000 was the thing that was already busy.
    """
    if not web_only and port == DEFAULT_PORT:
        return None

    lines = ["# generated by bootstrap.py; not part of the repository",
             "services:", "  app:"]
    if web_only:
        # Same image, same env, one process instead of two: uvicorn serves the
        # API and the built SPA and never constructs a Telegram channel.
        lines.append("    command: python -m uvicorn backend.main:app "
                     "--host 0.0.0.0 --port 8000")
    if port != DEFAULT_PORT:
        lines.append('    ports: !override ["%d:8000"]' % port)

    handle = tempfile.NamedTemporaryFile(
        mode="w", suffix=".nlcp2g-overlay.yml", delete=False, encoding="utf-8")
    handle.write("\n".join(lines) + "\n")
    handle.close()
    return handle.name


def compose_argv(base, root, overlay):
    argv = list(base) + ["--project-directory", root,
                         "-f", os.path.join(root, "docker-compose.yml")]
    if overlay:
        argv += ["-f", overlay]
    return argv


def wait_for_http(url, timeout):
    deadline = time.time() + timeout
    last = ""
    while time.time() < deadline:
        try:
            handle = urlopen(url, timeout=4)
            try:
                code = handle.getcode()
            finally:
                handle.close()
            if code and code < 500:
                return True, ""
        except Exception as exc:                     # noqa: BLE001 - reported
            last = str(exc)
        time.sleep(2)
    return False, last


# --------------------------------------------------------------------------
# the modes
# --------------------------------------------------------------------------

def do_check(root, args):
    step("Environment")
    say("python      %s (%s)" % (platform.python_version(), sys.executable))
    say("platform    %s %s" % (platform.system(), platform.release()))
    say("repository  %s" % root)

    step("Docker")
    base = check_docker(required=False)
    if base:
        version = compose_version(base)
        if version:
            say("compose version %s" % ".".join(str(n) for n in version))
            if version < (2, 24, 0):
                warn("--port needs compose 2.24+ (for `!override`); "
                     "everything else works.")

    step("Configuration")
    env_path = os.path.join(root, ".env")
    if not os.path.exists(env_path):
        warn(".env does not exist yet (bootstrap.py will create it)")
    else:
        values = read_env(env_path)
        for key, consequence in (
                ("GEMINI_API_KEY", "every model call fails"),
                ("TELEGRAM_BOT_TOKEN", "the `app` container exits at startup, "
                                       "taking the web UI with it"),
                ("CP_TUTOR_WEBHOOK_SECRET", "verdicts are rejected")):
            if unset(values, key):
                warn("%s is not set -> %s" % (key, consequence))
            else:
                ok("%s is set" % key)

    step("The sandbox image")
    # Worth its own check because the failure is silent and misattributed: the
    # stack comes up perfectly, the tutor answers, and only an actual code
    # attempt fails — with SANDBOX_UNAVAILABLE, which reads as "Docker is
    # broken" when the truth is that one image was never built. Nothing else in
    # the stack notices it is missing.
    if base is None:
        say("skipped (no Docker).")
    elif image_exists("cp-tutor-sandbox"):
        ok("cp-tutor-sandbox is built")
    else:
        warn("cp-tutor-sandbox is NOT built.")
        say("Everything else will work. Running learner C++ will not: every")
        say("attempt returns SANDBOX_UNAVAILABLE, through both the web UI and")
        say("chat. Build it with `python bootstrap.py`, or directly:")
        say("  docker build -t cp-tutor-sandbox ./sandbox")

    step("Ports")
    if port_is_taken(args.port):
        warn("something is already listening on %d." % args.port)
        say("`docker compose up` will fail with 'port is already allocated'.")
        say("Either stop it, or run with --port <other>.")
    else:
        ok("port %d is free" % args.port)

    step("This checkout")
    found = features(root)
    say("tests/            %s" % ("present" if found["tests"] else "absent"))
    if found["corpus"]:
        say("corpus/           present, index %s"
            % ("built" if found["corpus_index"] else "NOT BUILT"))
        if not found["corpus_index"] and found["build_index"]:
            say("                  build it: python scripts/build_index.py")
    if found["eval_reqs"]:
        say("requirements-eval.txt present (evaluation extras, not needed to run)")

    print("")
    ok("check complete. Nothing was changed.")


def do_up(root, args):
    step("Docker")
    base = check_docker(required=True)

    if args.port != DEFAULT_PORT:
        version = compose_version(base)
        if version and version < (2, 24, 0):
            die("--port needs Docker Compose 2.24 or newer (yours is %s)."
                % ".".join(str(n) for n in version),
                "Upgrade Docker Desktop, or free port 8000 and drop --port.")

    step("Configuration")
    web_only = ensure_env(root, args.web_only, args.yes)

    step("Ports")
    if port_is_taken(args.port):
        die("something is already listening on port %d." % args.port,
            "Usually an earlier run of this stack, or a bare "
            "`python -m backend.bot`.\n"
            "Stop it, or start on another port:  python bootstrap.py --port 8010")
    ok("port %d is free" % args.port)

    overlay = overlay_file(root, web_only, args.port)
    argv = compose_argv(base, root, overlay)

    step("Building the sandbox image (the C++ jail)")
    say("This is a separate image with no network and no API key. Learner code")
    say("runs in it; nothing else does.")
    run(argv + ["build", "sandbox"], cwd=root)

    step("Building the application image")
    say("Stage 1 builds the React UI with node; stage 2 is the Python runtime.")
    say("First run pulls both base images, so expect a few minutes.")
    run(argv + ["build"], cwd=root)

    step("Starting")
    services = ["app"] if web_only else []
    if web_only:
        say("web-only: starting `app` alone, serving the API and the UI.")
        say("No chat poll loop, so no Telegram token is needed. The worker,")
        say("scheduler and monitor are not started.")
    else:
        say("Starting app + worker + scheduler + monitor.")
    run(argv + ["up", "-d"] + services, cwd=root)

    step("Waiting for the app to answer")
    url = "http://localhost:%d/" % args.port
    say("polling %s" % url)
    if _DRY_RUN:
        ok("(dry run)")
        return
    good, error = wait_for_http(url, HEALTH_TIMEOUT_S)
    if not good:
        warn("no answer after %ds (last error: %s)" % (HEALTH_TIMEOUT_S, error))
        say("The last 40 log lines follow. A missing GEMINI_API_KEY or")
        say("TELEGRAM_BOT_TOKEN shows up here as an exception at startup.")
        run(argv + ["logs", "--tail", "40", "app"], cwd=root, check=False)
        die("the stack started but did not become reachable.",
            "Fix what the log reports, then: %s up -d" % " ".join(base))

    print("")
    ok("NLCP2G is up:  " + url)
    say("logs:  %s logs -f app" % " ".join(base))
    say("stop:  python bootstrap.py --down        (your data in ./data survives)")
    if web_only:
        say("")
        say("Running web-only. To add the chat bot: put TELEGRAM_BOT_TOKEN in")
        say(".env, then `python bootstrap.py` with no --web-only.")

    found = features(root)
    if found["corpus"] and not found["corpus_index"] and found["build_index"]:
        say("")
        warn("corpus/index is not built, so retrieval will find nothing.")
        say("Build it:  python scripts/build_index.py")


def do_down(root, args):
    step("Stopping")
    base = check_docker(required=True)
    argv = compose_argv(base, root, None)
    run(argv + ["down"], cwd=root)
    print("")
    ok("stopped. ./data is untouched, so the database and memory stores survive.")
    say("To also drop the sandbox scratch volume: %s down -v" % " ".join(base))


def do_tests(root, args):
    step("Running the test suite")
    found = features(root)
    if not found["tests"]:
        die("this checkout has no tests/ directory.")

    base = check_docker(required=False)
    if base:
        say("tests/ is excluded from the image on purpose, so it is mounted in.")
        mount = os.path.abspath(os.path.join(root, "tests")).replace("\\", "/")
        argv = compose_argv(base, root, None)
        code = run(argv + ["run", "--rm", "--no-deps", "--entrypoint", "",
                           "-v", mount + ":/app/tests",
                           "app", "python", "-m", "pytest", "tests", "-q"],
                   cwd=root, check=False)
    else:
        warn("no Docker; falling back to this machine's Python.")
        say("The suite needs no key, no token and no daemon, so this is fine")
        say("as long as the backend's dependencies are installed.")
        code = run([sys.executable, "-m", "pytest", "tests", "-q"],
                   cwd=root, check=False)
    if _DRY_RUN:
        return
    if code:
        die("tests failed (exit %d)." % code)
    ok("suite passed.")


def do_no_docker(root, args):
    """The host path: a venv, the dependencies, and the UI if node is around.

    Kept because "install Docker" is not always an answer somebody is allowed to
    give — a locked-down machine, a CI image, a laptop where Desktop will not
    start. What it cannot provide is the sandbox: running untrusted C++ needs
    the container, and pretending otherwise would be the one lie worth avoiding.
    """
    step("Host Python")
    if sys.version_info < HOST_PYTHON_MIN:
        die("this path needs Python %s or newer; you are running %s."
            % (".".join(str(n) for n in HOST_PYTHON_MIN),
               platform.python_version()),
            "The container path has no such requirement, because the image\n"
            "pins its own interpreter. Try: python bootstrap.py")
    ok("python %s" % platform.python_version())

    venv_dir = os.path.join(root, ".venv")
    bin_dir = "Scripts" if os.name == "nt" else "bin"
    exe = ".exe" if os.name == "nt" else ""
    venv_python = os.path.join(venv_dir, bin_dir, "python" + exe)

    step("Virtual environment")
    if os.path.exists(venv_python):
        ok(".venv already exists, reusing it")
    else:
        run([sys.executable, "-m", "venv", venv_dir], cwd=root)
        ok("created .venv (gitignored)")

    step("Dependencies")
    run([venv_python, "-m", "pip", "install", "--upgrade", "pip", "--quiet"],
        cwd=root, check=False)
    run([venv_python, "-m", "pip", "install", "-r",
         os.path.join("backend", "requirements.txt")], cwd=root)
    found = features(root)
    if found["eval_reqs"] and args.eval_extras:
        run([venv_python, "-m", "pip", "install", "-r", "requirements-eval.txt"],
            cwd=root)

    step("Configuration")
    ensure_env(root, True, args.yes)

    step("Web UI")
    if found["frontend_dist"]:
        ok("frontend/dist already built")
    elif shutil.which("npm"):
        say("building the SPA with npm (the container path does this for you)")
        npm = shutil.which("npm")
        front = os.path.join(root, "frontend")
        run([npm, "install", "--no-audit", "--no-fund"], cwd=front)
        run([npm, "run", "build"], cwd=front)
        if not _DRY_RUN:
            ok("built frontend/dist")
    else:
        warn("npm not found, so the UI cannot be built here.")
        say("The API will still serve; http://localhost:%d/ will 404 until")
        say("frontend/dist exists. Install Node 22+, or use the container path.")

    step("Code execution")
    if check_docker(required=False):
        if image_exists("cp-tutor-sandbox"):
            ok("cp-tutor-sandbox is built, so learner code can be run.")
        else:
            warn("Docker is available but cp-tutor-sandbox is not built, so")
            say("every attempt will return SANDBOX_UNAVAILABLE. Build it once:")
            say("  docker build -t cp-tutor-sandbox ./sandbox")
    else:
        warn("no Docker, so running learner C++ is unavailable on this path.")
        say("Everything else (chat, memory, retrieval) works. Attempts will")
        say("report SANDBOX_UNAVAILABLE, which is honest rather than broken.")

    print("")
    ok("host setup complete. Start it with:")
    say("  %s -m uvicorn backend.main:app --host 127.0.0.1 --port %d"
        % (venv_python, args.port))
    say("")
    say("That serves the API and the UI, and needs no Telegram token. To also")
    say("run the chat bot, use `%s -m backend.bot`," % venv_python)
    say("which serves the API and the poll loop in one process - see")
    say("LOCAL_SETUP.md for why that is required rather than convenient.")


def main():
    parser = argparse.ArgumentParser(
        description="Set up and start NLCP2G on this machine.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="With no options: builds the images and starts the whole stack.")
    parser.add_argument("--check", action="store_true",
                        help="diagnose this machine and change nothing")
    parser.add_argument("--web-only", action="store_true",
                        help="web UI and API only; no Telegram token required")
    parser.add_argument("--no-docker", action="store_true",
                        help="set up a host venv instead of containers")
    parser.add_argument("--tests", action="store_true",
                        help="run the test suite and exit")
    parser.add_argument("--down", action="store_true",
                        help="stop the stack (leaves ./data alone)")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT,
                        help="publish on this port instead of %d" % DEFAULT_PORT)
    parser.add_argument("--eval-extras", action="store_true",
                        help="also install requirements-eval.txt, where present")
    parser.add_argument("--yes", "-y", action="store_true",
                        help="never prompt; take the safe default every time")
    parser.add_argument("--dry-run", action="store_true",
                        help="print every command instead of running it")
    args = parser.parse_args()

    global _DRY_RUN
    _DRY_RUN = args.dry_run

    root = repo_root()
    print("NLCP2G bootstrap")
    print("repository: %s" % root)
    if _DRY_RUN:
        print("DRY RUN - nothing will be executed or written")

    try:
        if args.check:
            do_check(root, args)
        elif args.down:
            do_down(root, args)
        elif args.tests:
            do_tests(root, args)
        elif args.no_docker:
            do_no_docker(root, args)
        else:
            do_up(root, args)
    except KeyboardInterrupt:
        print("")
        die("interrupted. Nothing was rolled back; re-running is safe.")


if __name__ == "__main__":
    main()
