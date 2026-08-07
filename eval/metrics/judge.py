"""The four judged generation metrics, as an LLM-as-judge on the project's own seam.

    faithfulness       is every claim in the answer supported by the context?
    answer_relevance   does the answer address the question that was asked?
    context_precision  were the retrieved chunks relevant?
    context_recall     did retrieval get everything the golden answer needs?

**Why hand-rolled rather than DeepEval or Ragas.** Both were considered and both
are permitted. Three things decided it. The scorers go through
`llm.chat_model`, which is the single seam the whole test suite already stubs —
so `tests/test_judge_scorers.py` drives them offline with a scripted model,
which is not possible when a library owns its own LLM client. Caching is ours,
so a re-run is free and byte-identical rather than merely cheap. And no new
dependency tree lands on Python 3.14. The cost is that these definitions are
mine: they are written out below rather than cited, so they can be checked.

**Judge bias, and what is actually done about it.**

*Self-preference.* A model scores its own output generously. The generator is
`gemini-2.5-flash` and the judge is `gemini-2.5-flash-lite` — a different model.
(`gemini-2.5-pro` was the first choice and was abandoned: it returns 503
UNAVAILABLE under free-tier load often enough that a full run could not
complete. A judge that cannot be re-run is not reproducible, which is worth more
here than the stronger model.)

That leaves two residual risks, and rather than assert they are small,
`--judge-swap` measures one of them: it re-judges a stratified subset with
`gemini-2.5-flash`, the generator's own model, and the report prints the
difference. A judge scoring its own family's output higher shows up as a
positive delta, in the numbers, where a reader can weigh it.

The other residual is not measured and should not be glossed: same vendor, same
family, same training lineage. Only an independent-vendor judge would remove
it, and there is not one available here.

*Position.* Models over-credit whatever comes first. Context precision judges
each chunk in its own call, so there is no list and no position to be biased by.
It costs more calls; the cache makes that a one-time cost, and chunks shared
between the two configurations are judged once.

*Verbosity.* A long answer looks more thorough and scores higher on a holistic
rubric. Faithfulness therefore decomposes the answer into atomic claims and
scores the *fraction* supported, so adding unsupported sentences lowers the
score instead of padding it. Answer relevance keeps an explicit rubric with the
lengths named, and its prompt says outright that length is not quality.

*Judge-model mismatch.* The judge model and temperature are pinned in code, and
written into the report header, so a number can always be traced to what
produced it.

Everything returns a float in [0, 1], or `None` where the metric is undefined —
context recall against an out-of-corpus case has no golden claims to recall, and
scoring that zero would punish the set for containing honest unanswerable cases.
"""
from __future__ import annotations

import os
from typing import Type, TypeVar

from pydantic import BaseModel, Field

from backend import llm
from backend.rag.diskcache import JsonCache

CACHE_DIR = os.path.join(os.path.dirname(__file__), "..", "cache")

# Deliberately not MAIN_MODEL. See the module docstring. Read at call time, not
# captured at import, so `--judge-swap` can rebind it for the sensitivity run.
JUDGE_MODEL = os.environ.get("CP_TUTOR_JUDGE_MODEL", "gemini-2.5-flash-lite")
JUDGE_TEMPERATURE = 0.0

T = TypeVar("T", bound=BaseModel)


def _cache() -> JsonCache:
    return JsonCache(os.path.join(CACHE_DIR, "judge.json"))


def _ask(system: str, prompt: str, schema: Type[T], tag: str) -> T:
    """One judged call, cached by everything that could change the answer."""
    cache = _cache()
    key = JsonCache.key(tag, JUDGE_MODEL, JUDGE_TEMPERATURE, system, prompt,
                        schema.model_json_schema())
    cached = cache.get(key)
    if cached is not None:
        return schema.model_validate(cached)

    model = llm.chat_model(JUDGE_MODEL, temperature=JUDGE_TEMPERATURE)
    result = model.with_structured_output(schema).invoke(
        [{"role": "system", "content": system}, {"role": "user", "content": prompt}])
    if not isinstance(result, schema):
        result = schema.model_validate(result)
    cache.put(key, result.model_dump())
    return result


# ------------------------------------------------------------- claim splitting

class _Claims(BaseModel):
    claims: list[str] = Field(description="atomic factual claims, one per item")


CLAIMS_SYSTEM = """\
Break the text into atomic factual claims.

An atomic claim states exactly one checkable fact. Split compound sentences into
separate claims. Drop hedges, pleasantries, restatements of the question, and
anything that is not a factual assertion. Keep each claim self-contained: resolve
pronouns so it can be understood without the surrounding text.

If the text makes no factual claims at all — it declines to answer, or says the
material does not cover the question — return an empty list.
"""


def claims_of(text: str) -> list[str]:
    """Text -> atomic claims. Cached, so a claim set is computed once."""
    if not text.strip():
        return []
    return _ask(CLAIMS_SYSTEM, text, _Claims, "claims-v1").claims


# --------------------------------------------------------------- faithfulness

class _Support(BaseModel):
    supported: list[bool] = Field(
        description="one true/false per claim, in the order given")


FAITHFULNESS_SYSTEM = """\
You check whether each claim is supported by the reference passages.

For each numbered claim, answer true only if the passages state it or directly
entail it. Answer false if the passages are silent on it, contradict it, or only
suggest it — including when the claim is something you independently know to be
true. You are checking grounding in these passages, not correctness in general.

Return one true/false per claim, in the same order, and exactly as many as you
were given.
"""


def faithfulness(answer: str, contexts: list[str]) -> float | None:
    """Fraction of the answer's claims that the retrieved context supports.

    `None` when the answer makes no claims — an answer that correctly declines
    has nothing to be unfaithful about, and scoring it 0.0 would say the
    opposite of what happened while scoring it 1.0 would reward silence.
    """
    claims = claims_of(answer)
    if not claims:
        return None
    if not contexts:
        return 0.0

    numbered = "\n".join(f"{i}. {c}" for i, c in enumerate(claims, 1))
    joined = "\n\n---\n\n".join(contexts)
    verdicts = _ask(
        FAITHFULNESS_SYSTEM,
        f"Passages:\n\n{joined}\n\nClaims:\n{numbered}",
        _Support, "faithfulness-v1").supported

    # A short response would otherwise score high by answering less; pad any
    # missing verdicts as unsupported so an incomplete reply cannot help.
    verdicts = (verdicts + [False] * len(claims))[:len(claims)]
    return sum(1 for v in verdicts if v) / len(claims)


# ------------------------------------------------------------ answer relevance

class _Rating(BaseModel):
    score: int = Field(description="0, 1, 2 or 3 as defined in the rubric")


RELEVANCE_SYSTEM = """\
Rate how well the answer addresses the question that was actually asked.

  3  answers the question directly and completely
  2  answers it, but partially, or buried in material that was not asked for
  1  addresses the general topic without answering the question
  0  does not address the question, or answers a different one

Judge only whether the question was addressed. Do NOT reward length, detail, or
confident phrasing — a two-sentence answer that fully answers the question
scores 3, and a long one that circles it scores 1.

One exception: if the answer states that the available material does not cover
the question, and that is a reasonable response to it, score 3. Correctly
declining is addressing the question.
"""


def answer_relevance(query: str, answer: str) -> float:
    """0-3 rubric, rescaled to [0, 1]."""
    rating = _ask(RELEVANCE_SYSTEM, f"Question: {query}\n\nAnswer: {answer}",
                  _Rating, "relevance-v1").score
    return max(0, min(3, rating)) / 3.0


# ----------------------------------------------------------- context precision

class _Useful(BaseModel):
    useful: bool = Field(description="true if this passage helps answer it")


PRECISION_SYSTEM = """\
You decide whether one reference passage is useful for answering one question.

Answer true only if the passage contains information a correct answer would
draw on. Answer false if it is merely about a related topic, shares vocabulary
with the question, or covers a neighbouring technique without addressing what
was asked.

You are judging this passage alone. There is no list and no ranking; nothing
depends on where it came from.
"""


def context_precision(query: str, contexts: list[str]) -> float | None:
    """Fraction of retrieved passages that a correct answer would draw on.

    Each passage gets its own call, which is what removes position bias
    entirely rather than mitigating it. Cached per (query, passage), so the
    reranked and un-reranked runs share every judgement for a passage both
    retrieved.
    """
    if not contexts:
        return None
    verdicts = [
        _ask(PRECISION_SYSTEM, f"Question: {query}\n\nPassage:\n{text}",
             _Useful, "precision-v1").useful
        for text in contexts
    ]
    return sum(1 for v in verdicts if v) / len(verdicts)


# -------------------------------------------------------------- context recall

RECALL_SYSTEM = """\
You check whether each claim from a reference answer can be found in the
retrieved passages.

For each numbered claim, answer true if the passages contain the information
that claim expresses, even if worded differently. Answer false if the passages
do not contain it.

This measures whether retrieval found the right material, not whether anyone
wrote a good answer from it. Return one true/false per claim, in order.
"""


def context_recall(golden_answer: str, contexts: list[str]) -> float | None:
    """Fraction of the golden answer's claims present in the retrieved context.

    `None` when the golden answer has no claims to find — the out-of-corpus
    cases, whose golden answer is an instruction to decline.
    """
    claims = claims_of(golden_answer)
    if not claims:
        return None
    if not contexts:
        return 0.0

    numbered = "\n".join(f"{i}. {c}" for i, c in enumerate(claims, 1))
    joined = "\n\n---\n\n".join(contexts)
    verdicts = _ask(
        RECALL_SYSTEM,
        f"Passages:\n\n{joined}\n\nClaims:\n{numbered}",
        _Support, "recall-v1").supported

    verdicts = (verdicts + [False] * len(claims))[:len(claims)]
    return sum(1 for v in verdicts if v) / len(claims)


# ----------------------------------------------------------------- abstention

class _Abstained(BaseModel):
    abstained: bool = Field(
        description="true if the answer declines rather than attempting one")


ABSTENTION_SYSTEM = """\
Decide whether the answer declines to answer.

True if it says the material does not cover the question, that it cannot answer
from what it was given, or otherwise refuses. False if it attempts a substantive
answer, even a hedged or partial one.

An answer that explains what the material DOES cover and then answers anyway has
not declined. An answer that names the gap and stops has.
"""


def abstained(answer: str) -> float:
    """1.0 if the answer declines. Only meaningful on out-of-corpus cases.

    Not one of the four required metrics, but the out-of-corpus cases are
    otherwise unmeasurable: there is no golden context to score retrieval
    against, and the only thing worth knowing is whether the system invented an
    answer. Reported separately rather than folded into an average.
    """
    return 1.0 if _ask(ABSTENTION_SYSTEM, answer, _Abstained,
                       "abstention-v1").abstained else 0.0
