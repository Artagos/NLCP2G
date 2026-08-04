"""The model layer: LangChain chat models over Google Gemini (free tier).

Every agent in this system reaches a model through here, so the provider lives
in exactly one place. That was true when this called the native `google-genai`
SDK and it stays true now that it builds LangChain runnables — which is the
point of doing it this way round. The graphs in `backend/graphs/` bind tools and
structure output through the standard `BaseChatModel` interface, so swapping
Gemini for another provider is a change to this file and nothing else.

Auth: set GEMINI_API_KEY (or GOOGLE_API_KEY). Get a free key at
https://aistudio.google.com/apikey

Models are env-overridable so you can swap tiers without touching code:
  CP_TUTOR_ROUTER_MODEL   (default gemini-2.5-flash-lite — cheap classification)
  CP_TUTOR_MAIN_MODEL     (default gemini-2.5-flash      — tutor + translator)

Two functions are kept as plain callables rather than being folded into the
graphs: `generate` and `generate_structured`. Single-shot calls with no tools
and no branching gain nothing from being a graph, and every node that needs one
can just call it. They also remain the seam the test suite stubs.

Tracing is deliberately NOT enabled. LangSmith would send learner messages and
generated code off the machine; if you want it, set LANGCHAIN_TRACING_V2 and
LANGCHAIN_API_KEY yourself and know what you are agreeing to.
"""
from __future__ import annotations

import os
from typing import Type, TypeVar

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langchain_google_genai import ChatGoogleGenerativeAI
from pydantic import BaseModel

ROUTER_MODEL = os.environ.get("CP_TUTOR_ROUTER_MODEL", "gemini-2.5-flash-lite")
MAIN_MODEL = os.environ.get("CP_TUTOR_MAIN_MODEL", "gemini-2.5-flash")

# Transient failures worth retrying: overloaded, rate-limited, server errors.
# The free tier throttles hard, and a 503 should cost a second, not a turn.
MAX_ATTEMPTS = 4

T = TypeVar("T", bound=BaseModel)

_models: dict[str, BaseChatModel] = {}


def chat_model(model: str = MAIN_MODEL, **kwargs) -> BaseChatModel:
    """The chat model, memoised per (model, kwargs).

    Lazy so the package still imports without a key present (tests, CI): nothing
    here touches the network or validates credentials until something invokes it.

    Retries are the integration's own (`max_retries`), which backs off on the
    google-api-core transient errors — 429, 500, 503. That is deliberate and it
    is *not* the same as wrapping this in `.with_retry()`: a `RunnableRetry` is
    a plain Runnable, so it has neither `bind_tools` nor
    `with_structured_output`, and every caller in this system needs one or the
    other. Wrapping here would break the tutor, the operator, the router, the
    critic and the judge simultaneously, and no stubbed test would notice.
    """
    cache_key = model + repr(sorted(kwargs.items()))
    if cache_key not in _models:
        api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
        _models[cache_key] = ChatGoogleGenerativeAI(
            model=model,
            google_api_key=api_key,
            max_retries=MAX_ATTEMPTS,
            **kwargs,
        )
    return _models[cache_key]


def to_messages(system: str, messages: list[dict]) -> list[BaseMessage]:
    """[{'role': 'user'|'assistant', 'content': str}] -> LangChain messages.

    The dict form is what the rest of the codebase speaks (it is what SQLite
    stores in `messages`), so the conversion lives here rather than leaking
    LangChain types into the stores.
    """
    out: list[BaseMessage] = [SystemMessage(content=system)] if system else []
    for m in messages:
        content = m["content"]
        out.append(AIMessage(content=content) if m["role"] == "assistant"
                   else HumanMessage(content=content))
    return out


def text_of(message: BaseMessage) -> str:
    """The plain text of a chat message.

    `.text` moved from a method to a property in langchain-core 1.x, and the
    compatibility shim left the property *callable* — so the obvious
    `callable(...)` probe silently takes the deprecated path and warns. Read the
    property, and only fall back to calling it on an older core.
    """
    value = getattr(message, "text", None)
    if value is None:
        return str(getattr(message, "content", "") or "")
    if isinstance(value, str):
        return value
    return str(value() if callable(value) else value)


def generate(system: str, messages: list[dict], model: str = MAIN_MODEL) -> str:
    """Plain text generation (verdict explainer, summarizer, monitor analyst)."""
    return text_of(chat_model(model).invoke(to_messages(system, messages))).strip()


def generate_structured(
    system: str,
    messages: list[dict],
    schema: Type[T],
    model: str = MAIN_MODEL,
) -> T:
    """Structured generation validated against a Pydantic model.

    Used by the router, the feasibility screener, the code generator, the
    fidelity critic, the reflector and the monitor's judge — everywhere a reply
    is branched on rather than shown to a human. `with_structured_output`
    guarantees the shape, so callers can read fields instead of parsing prose.
    """
    structured = chat_model(model).with_structured_output(schema)
    result = structured.invoke(to_messages(system, messages))
    if isinstance(result, schema):
        return result
    # Belt and braces: some providers hand back a dict rather than the model.
    return schema.model_validate(result)
