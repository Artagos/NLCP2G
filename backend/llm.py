"""Thin LLM shim over Google Gemini (free tier).

All three agents go through here, so the provider lives in exactly one place.
Uses the native google-genai SDK because it supports Pydantic `response_schema`
structured outputs directly — the router and translator rely on that.

Auth: set GEMINI_API_KEY (or GOOGLE_API_KEY). Get a free key at
https://aistudio.google.com/apikey

Models are env-overridable so you can swap tiers without touching code:
  CP_TUTOR_ROUTER_MODEL   (default gemini-2.5-flash-lite — cheap classification)
  CP_TUTOR_MAIN_MODEL     (default gemini-2.5-flash      — tutor + translator)
"""
from __future__ import annotations

import os
import time
from typing import Callable, Type, TypeVar

from google import genai
from google.genai import errors as genai_errors
from google.genai import types
from pydantic import BaseModel

# Transient statuses worth retrying: overloaded, rate-limited, server errors.
_RETRY_STATUS = {429, 500, 503}
_MAX_ATTEMPTS = 4

ROUTER_MODEL = os.environ.get("CP_TUTOR_ROUTER_MODEL", "gemini-2.5-flash-lite")
MAIN_MODEL = os.environ.get("CP_TUTOR_MAIN_MODEL", "gemini-2.5-flash")

T = TypeVar("T", bound=BaseModel)

_client: genai.Client | None = None


def _get_client() -> genai.Client:
    """Lazy so the package imports without a key present (tests, CI)."""
    global _client
    if _client is None:
        api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
        # genai.Client() also resolves the key from the env on its own; passing
        # it explicitly keeps the failure mode obvious if it's unset.
        _client = genai.Client(api_key=api_key) if api_key else genai.Client()
    return _client


def _with_retries(call: Callable[[], T]) -> T:
    """Retry transient Gemini errors (503 overloaded, 429 rate-limited, 5xx)
    with exponential backoff. Non-transient errors propagate immediately."""
    delay = 0.6
    for attempt in range(1, _MAX_ATTEMPTS + 1):
        try:
            return call()
        except genai_errors.APIError as exc:
            code = getattr(exc, "code", None)
            if code not in _RETRY_STATUS or attempt == _MAX_ATTEMPTS:
                raise
            time.sleep(delay)
            delay *= 2
    raise RuntimeError("unreachable")  # pragma: no cover


def _to_contents(messages: list[dict]) -> list[types.Content]:
    """Convert [{'role': 'user'|'assistant', 'content': str}] to Gemini contents.
    Gemini uses 'model' for the assistant role."""
    contents: list[types.Content] = []
    for m in messages:
        role = "model" if m["role"] == "assistant" else "user"
        contents.append(
            types.Content(role=role, parts=[types.Part.from_text(text=m["content"])])
        )
    return contents


def generate(system: str, messages: list[dict], model: str = MAIN_MODEL) -> str:
    """Plain text generation (tutor, verdict explainer)."""
    resp = _with_retries(lambda: _get_client().models.generate_content(
        model=model,
        contents=_to_contents(messages),
        config=types.GenerateContentConfig(system_instruction=system),
    ))
    return (resp.text or "").strip()


# --------------------------------------------------------------- tool calling

class Tool:
    """One callable the model may invoke mid-run.

    `params` is a small JSON-schema fragment (object with string/integer/boolean
    properties) — enough for the retrieval tools, and it keeps the declaration
    readable next to the function it describes.
    """

    def __init__(self, name: str, description: str, params: dict, fn: Callable[..., str]):
        self.name = name
        self.description = description
        self.params = params
        self.fn = fn


_TYPES = {
    "string": types.Type.STRING,
    "integer": types.Type.INTEGER,
    "number": types.Type.NUMBER,
    "boolean": types.Type.BOOLEAN,
}


def _schema(spec: dict) -> types.Schema:
    props = {
        name: types.Schema(
            type=_TYPES.get(p.get("type", "string"), types.Type.STRING),
            description=p.get("description", ""),
        )
        for name, p in (spec.get("properties") or {}).items()
    }
    return types.Schema(
        type=types.Type.OBJECT,
        properties=props or None,
        required=spec.get("required") or None,
    )


def generate_with_tools(
    system: str,
    messages: list[dict],
    tools: list[Tool],
    model: str = MAIN_MODEL,
    max_rounds: int = 4,
) -> tuple[str, list[dict]]:
    """Generate a reply, letting the model *pull* context through tools.

    Returns (text, calls) where `calls` is [{name, args, result}] in order — the
    trace of what the agent chose to fetch, which is logged with the run.

    Function calling is driven manually rather than by the SDK's automatic mode
    so that every call is observable and recorded; a monitor that cannot see
    which memories were fetched cannot judge whether they were used.
    """
    if not tools:
        return generate(system, messages, model), []

    by_name = {t.name: t for t in tools}
    declarations = [
        types.FunctionDeclaration(name=t.name, description=t.description,
                                  parameters=_schema(t.params))
        for t in tools
    ]
    config = types.GenerateContentConfig(
        system_instruction=system,
        tools=[types.Tool(function_declarations=declarations)],
        # we run the loop ourselves; don't let the SDK invoke anything
        automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
    )

    contents = _to_contents(messages)
    calls: list[dict] = []

    for _ in range(max_rounds):
        resp = _with_retries(lambda: _get_client().models.generate_content(
            model=model, contents=contents, config=config))

        candidate = (resp.candidates or [None])[0]
        parts = list(getattr(getattr(candidate, "content", None), "parts", None) or [])
        requested = [p.function_call for p in parts if getattr(p, "function_call", None)]

        if not requested:
            return (resp.text or "").strip(), calls

        # echo the model's turn back, then answer each call it made
        contents.append(types.Content(role="model", parts=parts))
        replies = []
        for fc in requested:
            tool = by_name.get(fc.name)
            args = dict(fc.args or {})
            if tool is None:
                result = f"No such tool: {fc.name}"
            else:
                try:
                    result = tool.fn(**args)
                except Exception as exc:      # a broken tool must not kill the turn
                    result = f"Tool error: {type(exc).__name__}: {exc}"
            calls.append({"name": fc.name, "args": args, "result": result})
            replies.append(types.Part.from_function_response(
                name=fc.name, response={"result": result}))
        contents.append(types.Content(role="user", parts=replies))

    # ran out of rounds — ask for a final answer with no tools available
    return generate(system, messages, model), calls


def generate_structured(
    system: str,
    messages: list[dict],
    schema: Type[T],
    model: str = MAIN_MODEL,
) -> T:
    """Structured generation validated against a Pydantic model (router, codegen)."""
    resp = _with_retries(lambda: _get_client().models.generate_content(
        model=model,
        contents=_to_contents(messages),
        config=types.GenerateContentConfig(
            system_instruction=system,
            response_mime_type="application/json",
            response_schema=schema,
        ),
    ))
    parsed = resp.parsed
    if isinstance(parsed, schema):
        return parsed
    # Fallback if the SDK didn't auto-parse for some reason.
    return schema.model_validate_json(resp.text or "{}")
