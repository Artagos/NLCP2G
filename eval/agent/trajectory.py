"""One run of the agent, as an ordered list of tool calls.

This is the whole adapter: TOOL-typed spans, sorted by the time they started,
the tool's name and the arguments it was called with. It is short because the
tracing did the work — which is the reason tracing came first. Written against a
hand-rolled trajectory log instead, this file would have had to invent the
capture as well, and then be rewritten once real traces existed.

**Arguments, never results.** A trajectory says what the agent decided to do. If
what came back were folded in, a metric over it would be scoring the corpus and
the memory store rather than the agent's choices, and two runs that made
identical decisions would diverge because one search happened to return more.
`Span.outputs` is present in the portable trace — the RAG scorer adapters need
it — and nothing in this module touches it. `tests/test_trajectory_metrics.py`
pins that with a trace whose outputs are booby-trapped.

**Injected parameters are not arguments.** `retrieve_memory` and its siblings
take `state: Annotated[dict, InjectedState]` and
`tool_call_id: Annotated[str, InjectedToolCallId]`. LangGraph fills those in;
the model never sees them and cannot get them wrong. Scoring them would be
scoring the framework, so they are dropped here rather than in each predicate.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from . import store
from .store import TraceView

# Filled by LangGraph, not chosen by the model. See the module docstring.
INJECTED = {"state", "tool_call_id", "config", "runnable_config", "store"}


@dataclass(frozen=True)
class ToolCall:
    tool: str
    arguments: dict[str, Any]

    def __str__(self) -> str:
        shown = ", ".join(f"{k}={v!r}" for k, v in sorted(self.arguments.items()))
        return f"{self.tool}({shown})"


def _arguments(span: store.Span) -> dict[str, Any]:
    """The model-chosen arguments of one tool span.

    LangChain hands tool inputs through in more than one shape depending on how
    the call was made, so unwrap the common nestings rather than assuming one.
    """
    raw = span.inputs
    if isinstance(raw, dict):
        for key in ("args", "arguments", "input", "kwargs"):
            inner = raw.get(key)
            if isinstance(inner, dict):
                raw = inner
                break
    if not isinstance(raw, dict):
        # a single positional argument — the tools here each have one that
        # matters, so name it after the span rather than losing it
        return {"input": raw} if raw not in (None, "") else {}
    return {k: v for k, v in raw.items() if k not in INJECTED}


def tool_calls(trace: TraceView) -> list[ToolCall]:
    """The ordered trajectory of one traced run."""
    return [ToolCall(tool=span.tool_name, arguments=_arguments(span))
            for span in trace.of_type(store.TOOL)]


def tool_names(trace: TraceView) -> list[str]:
    """Just the sequence of names — what the selection metric compares."""
    return [call.tool for call in tool_calls(trace)]
