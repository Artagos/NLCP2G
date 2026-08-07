"""Tracing: local, optional, and off unless you ask for it.

`llm.py` states the project's position on tracing plainly, and it is a good one:
LangSmith would send learner messages and generated C++ off the machine, so it
stays unset. Nothing about that argument has changed, so this module is built to
satisfy it rather than to override it:

* **local only** — traces go to a SQLite file on disk (`CP_TUTOR_TRACING_URI`,
  default `mlflow.db` beside the repo). No hosted service, no vendor, no
  network. `mlflow ui` reads the same file if you want to look at a span tree.
* **off by default** — nothing is recorded unless `CP_TUTOR_TRACING=1`.
* **absent by default** — MLflow is not in `backend/requirements.txt`; it lives
  in `requirements-eval.txt`, so the container that actually serves learners
  does not ship a measuring tool. Every entry point here degrades to a no-op
  when the import fails, which is why the whole test suite passes without it.

The rest of the codebase touches tracing only through this module. That keeps
one import of `mlflow` in the application, and it keeps every call site honest:
`with tracing.span(...)` reads the same whether tracing is on, off, or
uninstalled.

**What MLflow gives us and what it does not.** `mlflow.langchain.autolog()`
instruments LangChain and LangGraph wholesale — a CHAIN span per graph node, a
CHAT_MODEL span per model call carrying `mlflow.chat.tokenUsage` and
`mlflow.llm.model`, and a TOOL span per `ToolNode` execution carrying the tool's
arguments. What it does not give is a root span for a turn (the turn is not one
Runnable), a span for retrieval (numpy and BM25 are not LangChain), or the
`gen_ai.tool.name` attribute (measured: MLflow 3.15.1 leaves the tool name in
the span's *name* and sets no GenAI semantic-convention key at all). Those three
gaps are what the explicit helpers below fill.

**One caveat worth knowing.** `mlflow.langchain.autolog()` imports the
`langchain` umbrella package for a version check, even though the tracing code
itself only needs `langchain-core`. This project uses `langchain-core` directly
and has no other reason to depend on the umbrella, so `requirements-eval.txt`
carries it purely to keep autolog importable. Upstream has fixed this, but not
in a released version, so it is a real dependency for now rather than a
precaution.
"""
from __future__ import annotations

import contextlib
import contextvars
import logging
import os
import threading
from typing import Any, Iterator

log = logging.getLogger("cp_tutor.tracing")

# MLflow's SpanType values, restated as plain strings so importing this module
# never requires mlflow. They are checked against the real enum in the tests.
AGENT = "AGENT"
CHAIN = "CHAIN"
CHAT_MODEL = "CHAT_MODEL"
TOOL = "TOOL"
RETRIEVER = "RETRIEVER"
UNKNOWN = "UNKNOWN"

# The OpenTelemetry GenAI key for a tool's name. MLflow does not set it; we do,
# so a trace read back by `eval/agent/trajectory.py` carries the conventional
# attribute rather than relying on a span name that a framework upgrade could
# rename underneath us.
TOOL_NAME_ATTRIBUTE = "gen_ai.tool.name"

ENV_ENABLED = "CP_TUTOR_TRACING"
ENV_URI = "CP_TUTOR_TRACING_URI"
ENV_EXPERIMENT = "CP_TUTOR_TRACING_EXPERIMENT"

_DEFAULT_DB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                           "mlflow.db")

_lock = threading.Lock()
_state: dict[str, Any] = {"ready": False, "mlflow": None, "autolog": False}

# Who asked for this turn, and which eval case it belongs to. Context variables
# rather than parameters: `turn.run()` is reached from the HTTP handler, the
# Telegram bot and the eval harness, and threading an extra argument through all
# three to satisfy a measuring tool would let the measurement change the design.
_origin: contextvars.ContextVar[str] = contextvars.ContextVar("origin", default="")
_case: contextvars.ContextVar[str] = contextvars.ContextVar("case", default="")


def enabled() -> bool:
    """Read at call time, so tests and the harness can flip it per process."""
    return os.environ.get(ENV_ENABLED, "") == "1"


def tracking_uri() -> str:
    return os.environ.get(ENV_URI) or "sqlite:///" + _DEFAULT_DB.replace("\\", "/")


# --------------------------------------------------------------- the no-ops --

class _NullSpan:
    """Stands in for a span so call sites need no `if tracing.enabled()`."""

    def set_attribute(self, key: str, value: Any) -> None: ...
    def set_attributes(self, values: dict) -> None: ...
    def set_inputs(self, value: Any) -> None: ...
    def set_outputs(self, value: Any) -> None: ...

    @property
    def trace_id(self) -> str:
        return ""


NULL_SPAN = _NullSpan()


# ----------------------------------------------------------------- lifecycle --

def setup(experiment: str | None = None) -> bool:
    """Turn tracing on. Idempotent, and safe to call when MLflow is absent.

    Returns True when traces will actually be recorded, so a caller that needs
    to know (the eval harness does — it cannot score a run it did not capture)
    can fail loudly instead of producing an empty report.
    """
    if not enabled():
        return False
    with _lock:
        if _state["ready"]:
            return True
        try:
            import mlflow
        except Exception as exc:                       # pragma: no cover
            log.warning("tracing requested but mlflow is unavailable: %s", exc)
            return False

        mlflow.set_tracking_uri(tracking_uri())
        mlflow.set_experiment(
            experiment or os.environ.get(ENV_EXPERIMENT) or "nlcp2g")

        # The one call that instruments LangChain and LangGraph. If it fails we
        # keep the explicit spans rather than losing the turn: a broken
        # measuring tool must not break the thing being measured.
        try:
            mlflow.langchain.autolog()
            _state["autolog"] = True
        except Exception as exc:
            log.warning("langchain autolog unavailable, falling back to "
                        "explicit spans only: %s", exc)

        _state["mlflow"] = mlflow
        _state["ready"] = True
        return True


def active() -> bool:
    """True when `setup()` has succeeded in this process."""
    return bool(_state["ready"])


def reset() -> None:
    """Forget the configured state. Tests only."""
    with _lock:
        _state.update({"ready": False, "mlflow": None, "autolog": False})


def flush() -> None:
    """Drain MLflow's async export queue.

    Traces are exported on a background thread, so anything that reads a trace
    back in the same process — the eval harness does, immediately — sees nothing
    without this.
    """
    if not _state["ready"]:
        return
    with contextlib.suppress(Exception):
        _state["mlflow"].flush_trace_async_logging()


# --------------------------------------------------------------- the spans --

@contextlib.contextmanager
def span(name: str, span_type: str = UNKNOWN,
         attributes: dict | None = None) -> Iterator[Any]:
    """Open a span, or do nothing at all.

    Exceptions from the tracing library are swallowed on purpose. A turn that
    fails because the tracer fell over is a worse outcome than an untraced turn,
    and this system's whole point is answering a learner.
    """
    if not _state["ready"]:
        yield NULL_SPAN
        return
    try:
        with _state["mlflow"].start_span(
                name=name, span_type=span_type, attributes=attributes) as raw:
            yield raw
    except Exception as exc:                           # pragma: no cover
        log.debug("span %r failed, continuing untraced: %s", name, exc)
        yield NULL_SPAN


def traced(name: str | None = None, span_type: str = UNKNOWN):
    """Decorator form. Identity function when tracing is off."""
    def wrap(func):
        import functools

        @functools.wraps(func)
        def inner(*args, **kwargs):
            if not _state["ready"]:
                return func(*args, **kwargs)
            with span(name or func.__name__, span_type):
                return func(*args, **kwargs)
        return inner
    return wrap


def tag(**tags: str) -> None:
    """Attach tags to the trace currently being recorded."""
    if not _state["ready"] or not tags:
        return
    with contextlib.suppress(Exception):
        _state["mlflow"].update_current_trace(
            tags={k: str(v) for k, v in tags.items() if v not in (None, "")})


def name_tool(tool_name: str) -> None:
    """Record the conventional `gen_ai.tool.name` on the enclosing TOOL span.

    Called from inside each tool body. MLflow opens the TOOL span around the
    tool for us but leaves the name only in `span.name`; the trajectory adapter
    wants the semantic-convention key, and setting it here means the live trace
    and the exported one agree.
    """
    if not _state["ready"]:
        return
    with contextlib.suppress(Exception):
        current = _state["mlflow"].get_current_active_span()
        if current is not None:
            current.set_attribute(TOOL_NAME_ATTRIBUTE, tool_name)


def current_trace_id() -> str:
    if not _state["ready"]:
        return ""
    with contextlib.suppress(Exception):
        return _state["mlflow"].get_last_active_trace_id() or ""
    return ""


# ------------------------------------------------------------ who is asking --

@contextlib.contextmanager
def origin(value: str, case_id: str = "") -> Iterator[None]:
    """Declare who this turn is for: `api`, `ui` or `batch`.

    The taught convention, and the one that makes a trace store usable: without
    it, an eval sweep and a real conversation are indistinguishable rows, and
    every future question about production behaviour has to be asked of a table
    that is mostly synthetic.

    `case_id` joins a trace back to the scenario that produced it. It is high
    cardinality, which is exactly what you are warned against for *filter* tags
    — but this is a join key over a bounded eval set, read by id rather than
    scanned, which is the case where it is the right thing to store.
    """
    origin_token = _origin.set(value)
    case_token = _case.set(case_id)
    try:
        yield
    finally:
        _origin.reset(origin_token)
        _case.reset(case_token)


def turn_tags(**extra: str) -> dict[str, str]:
    """The tags every turn carries. Empty values are dropped by `tag`."""
    return {"request_origin": _origin.get() or "api",
            "eval_case_id": _case.get(), **extra}
