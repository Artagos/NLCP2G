"""A portable trace, so the numbers survive without a server.

MLflow stores traces in a SQLite backend, and `*.db` is gitignored — as it
should be, since a binary store is not evidence anyone can read. But this
project has claimed for two increments that its evaluation reproduces offline
from committed artefacts, and an agent eval that can only be recomputed by
paying for thirty more live turns would quietly end that.

So every trace is flattened to the small dataclasses below and written as JSONL.
Everything downstream — all four trajectory metrics, the RAG-scorer adapters and
the whole safety detector — is a pure function of a `TraceView`, never of an
MLflow object. That has three consequences worth stating:

* the committed `eval/traces/*.jsonl` recompute every number with no API key,
  no tracking server and no MLflow installed at all;
* the same metric code runs unchanged against a live trace, because
  `from_mlflow` is the only place that knows what MLflow looks like;
* the tests can build a trace by hand in six lines instead of standing up a
  tracking backend.

`inputs` and `outputs` are both kept. The RAG adapter needs the final answer and
the retrieved passages, so discarding outputs would make Part 2.3 impossible.
`trajectory.py` must nonetheless never read an output — see the note there.
"""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from typing import Any

# MLflow's SpanType values, as plain strings so this module imports with no
# dependency on mlflow being installed.
AGENT = "AGENT"
CHAIN = "CHAIN"
LLM = "LLM"
CHAT_MODEL = "CHAT_MODEL"
TOOL = "TOOL"
RETRIEVER = "RETRIEVER"

# A LangChain chat model produces a CHAT_MODEL span, never an LLM one. Measured,
# not assumed: `ChatGoogleGenerativeAI` under `mlflow.langchain.autolog()` emits
# `CHAT_MODEL`. Anything filtering on `SpanType.LLM` alone finds nothing and
# reports zero tokens, which looks like a quiet instrumentation gap rather than
# a wrong constant. Both are accepted so a future provider that emits LLM counts.
MODEL_SPAN_TYPES = (CHAT_MODEL, LLM)

# The tool name. `gen_ai.tool.name` is the OpenTelemetry GenAI convention and the
# key the assignment names — but MLflow 3.15.1 does NOT populate it: the tool
# name arrives as the span's own name and nothing else. `backend/tracing.py`
# sets the conventional key explicitly from inside each tool so a live trace
# carries it, and the span name remains the fallback for anything MLflow opened
# on its own.
TOOL_NAME_KEYS = ("gen_ai.tool.name", "mlflow.spanFunctionName")

# Verified against a real Gemini call:
#   mlflow.chat.tokenUsage = {"input_tokens": 65, "output_tokens": 16,
#                             "total_tokens": 81, "cache_read_input_tokens": 0}
#   mlflow.llm.model       = "gemini-2.5-flash-lite"
TOKEN_KEYS = ("mlflow.chat.tokenUsage",)
MODEL_KEYS = ("mlflow.llm.model", "gen_ai.request.model", "ls_model_name")
PROVIDER_KEYS = ("mlflow.llm.provider",)


@dataclass(frozen=True)
class Span:
    span_id: str
    parent_id: str | None
    name: str
    span_type: str
    start_ns: int
    end_ns: int
    attributes: dict[str, Any] = field(default_factory=dict)
    inputs: Any = None
    outputs: Any = None

    @property
    def duration_ms(self) -> float:
        return max(0, self.end_ns - self.start_ns) / 1e6

    @property
    def tool_name(self) -> str:
        """The tool this span executed, or "" if it is not a tool span."""
        for key in TOOL_NAME_KEYS:
            value = self.attributes.get(key)
            if value:
                return str(value)
        return self.name or ""

    @property
    def model(self) -> str:
        for key in MODEL_KEYS:
            value = self.attributes.get(key)
            if value:
                return str(value)
        return ""

    @property
    def tokens(self) -> dict[str, int]:
        """`{input, output, total}`, empty when the span carries no usage."""
        for key in TOKEN_KEYS:
            usage = self.attributes.get(key)
            if isinstance(usage, str):
                try:
                    usage = json.loads(usage)
                except ValueError:
                    continue
            if isinstance(usage, dict):
                out = {}
                for name, aliases in (
                        ("input", ("input_tokens", "prompt_tokens")),
                        ("output", ("output_tokens", "completion_tokens")),
                        ("total", ("total_tokens",))):
                    for alias in aliases:
                        if isinstance(usage.get(alias), int):
                            out[name] = usage[alias]
                            break
                if out:
                    return out
        return {}


@dataclass(frozen=True)
class TraceView:
    trace_id: str
    tags: dict[str, str] = field(default_factory=dict)
    spans: list[Span] = field(default_factory=list)

    def of_type(self, span_type: str) -> list[Span]:
        """Spans of one type, in the order they started.

        Start-time order, not storage order: the span tree is a tree, and the
        sequence a reader cares about is the sequence things happened in.
        """
        return sorted((s for s in self.spans if s.span_type == span_type),
                      key=lambda s: s.start_ns)

    @property
    def root(self) -> Span | None:
        roots = [s for s in self.spans if not s.parent_id]
        return min(roots, key=lambda s: s.start_ns) if roots else None

    @property
    def case_id(self) -> str:
        """The eval case this trace belongs to — the join key, not a filter."""
        return self.tags.get("eval_case_id", "")

    @property
    def origin(self) -> str:
        return self.tags.get("request_origin", "")

    @property
    def latency_ms(self) -> float:
        return self.root.duration_ms if self.root else 0.0

    @property
    def model_spans(self) -> list[Span]:
        """Every model call, in order — CHAT_MODEL and LLM alike."""
        spans = [s for s in self.spans if s.span_type in MODEL_SPAN_TYPES]
        return sorted(spans, key=lambda s: s.start_ns)

    @property
    def tokens(self) -> dict[str, int]:
        """Token usage summed over every model span in the trace."""
        total: dict[str, int] = {}
        for span in self.model_spans:
            for key, value in span.tokens.items():
                total[key] = total.get(key, 0) + value
        return total

    @property
    def models(self) -> list[str]:
        """Distinct model names this turn reached for, in order of first use."""
        seen: list[str] = []
        for span in self.model_spans:
            if span.model and span.model not in seen:
                seen.append(span.model)
        return seen

    def to_dict(self) -> dict:
        return {"trace_id": self.trace_id, "tags": self.tags,
                "spans": [asdict(s) for s in self.spans]}

    @classmethod
    def from_dict(cls, raw: dict) -> "TraceView":
        return cls(trace_id=raw["trace_id"], tags=raw.get("tags") or {},
                   spans=[Span(**s) for s in raw.get("spans") or []])


# ------------------------------------------------------------- mlflow bridge --

def _plain(value: Any) -> Any:
    """Make a span payload JSON-safe without losing what it said.

    MLflow hands back whatever the traced function was given, which for this
    system includes LangChain message objects and a `Problem` dataclass. They
    are rendered rather than dropped, because the RAG adapter reads them.
    """
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(k): _plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    for attribute in ("content", "text"):
        got = getattr(value, attribute, None)
        if isinstance(got, str):
            return got
    return str(value)


def from_mlflow(trace) -> TraceView:
    """Flatten a live `mlflow.entities.Trace` into the portable form.

    The single place in this codebase that knows MLflow's object model.
    Attribute access is defensive on purpose: MLflow moved several of these
    between 2.x and 3.x, and a rename should cost one span field rather than
    the whole export.
    """
    info = getattr(trace, "info", None)
    data = getattr(trace, "data", None)

    trace_id = str(getattr(info, "trace_id", None)
                   or getattr(trace, "trace_id", "") or "")
    tags = {str(k): str(v) for k, v in (getattr(info, "tags", None) or {}).items()}

    spans: list[Span] = []
    for raw in (getattr(data, "spans", None) or []):
        attributes = dict(getattr(raw, "attributes", None) or {})
        spans.append(Span(
            span_id=str(getattr(raw, "span_id", "")),
            parent_id=(str(getattr(raw, "parent_id", "") or "") or None),
            name=str(getattr(raw, "name", "")),
            span_type=str(getattr(raw, "span_type", "") or "UNKNOWN"),
            start_ns=int(getattr(raw, "start_time_ns", 0) or 0),
            end_ns=int(getattr(raw, "end_time_ns", 0) or 0),
            attributes={str(k): _plain(v) for k, v in attributes.items()},
            inputs=_plain(getattr(raw, "inputs", None)),
            outputs=_plain(getattr(raw, "outputs", None)),
        ))
    return TraceView(trace_id=trace_id, tags=tags, spans=spans)


# --------------------------------------------------------------- slimming --
#
# A raw MLflow trace of one turn is about 40 MB, and thirty-nine of them came to
# 1.5 GB. That is not a committable artefact and it took a measurement to see
# why. Two causes, both structural rather than accidental:
#
#   * every payload is stored TWICE — once in `span.inputs` / `span.outputs`
#     and again as the `mlflow.spanInputs` / `mlflow.spanOutputs` attributes;
#   * a LangGraph CHAIN span's payload is the WHOLE graph state at that
#     superstep, which for this system means the message history, the fenced
#     problem statement and every retrieved passage — re-serialised on both
#     sides of every node, so it grows quadratically with the number of nodes.
#
# The `CHAIN LangGraph` span in one ordinary turn measured 15.6 MB by itself.
#
# So the committed trace keeps what the metrics and the detector actually read
# and drops the rest. CHAIN spans keep their name and timing, which is all the
# span tree needs from them, and lose their payload entirely. Model spans keep
# tokens and model name, not the prompt. Tool, retrieval and root spans keep
# their payloads, because the trajectory, the RAG adapters and the injection
# checks all read them.
#
# Nothing downstream loses information: the full trace is still in `mlflow.db`
# for anyone who wants to open it in the UI. What is committed is the evidence.

KEEP_ATTRIBUTES = frozenset({
    "gen_ai.tool.name", "mlflow.chat.tokenUsage", "mlflow.llm.model",
    "mlflow.llm.provider", "tool_call_id",
})

# Span types whose inputs and outputs are read by something downstream.
PAYLOAD_TYPES = frozenset({AGENT, TOOL, RETRIEVER})

# A rendered five-passage search result runs to a few kB; the injection checks
# scan it, so it is kept whole up to a bound that no honest tool result reaches.
MAX_PAYLOAD_CHARS = 20_000


def _clip(value):
    if isinstance(value, str) and len(value) > MAX_PAYLOAD_CHARS:
        return value[:MAX_PAYLOAD_CHARS] + f"…[clipped, {len(value)} chars]"
    if isinstance(value, dict):
        return {k: _clip(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_clip(v) for v in value]
    return value


def slim(trace: TraceView) -> TraceView:
    """Project a trace down to what the evidence needs. See the note above."""
    spans = []
    for span in trace.spans:
        keeps_payload = span.span_type in PAYLOAD_TYPES
        spans.append(Span(
            span_id=span.span_id,
            parent_id=span.parent_id,
            name=span.name,
            span_type=span.span_type,
            start_ns=span.start_ns,
            end_ns=span.end_ns,
            attributes={k: v for k, v in span.attributes.items()
                        if k in KEEP_ATTRIBUTES},
            inputs=_clip(span.inputs) if keeps_payload else None,
            outputs=_clip(span.outputs) if keeps_payload else None,
        ))
    return TraceView(trace_id=trace.trace_id, tags=dict(trace.tags), spans=spans)


# ----------------------------------------------------------------- the file --

def export(path: str, traces: list[TraceView], *, compact: bool = True) -> None:
    """Write JSONL — one trace per line, keys sorted, so a diff is readable.

    Slimmed by default; pass `compact=False` only if you genuinely want the
    forty-megabyte version, and do not commit it.
    """
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        for trace in traces:
            if compact:
                trace = slim(trace)
            fh.write(json.dumps(trace.to_dict(), sort_keys=True,
                                ensure_ascii=False) + "\n")
    os.replace(tmp, path)


def load(path: str) -> list[TraceView]:
    with open(path, encoding="utf-8") as fh:
        return [TraceView.from_dict(json.loads(line))
                for line in fh if line.strip()]
