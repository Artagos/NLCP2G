"""The agent eval set: end-to-end scenarios, loaded and validated.

Shaped after `eval/anchors.py`, which loads the retrieval cases — same idea, a
hand-authored YAML file with a loader strict enough that a typo fails loudly
instead of quietly scoring zero.

A scenario is a task the learner gives the tutor, the tool calls that would be a
reasonable way to serve it, and what the reply has to achieve. The middle one is
a *set of acceptable alternatives* rather than a single expected sequence,
because more than one trajectory is often right: a concept question can be
answered from one search or from two, and pinning the single sequence the model
happened to produce on the day would measure conformity to a recording rather
than competence.

Argument expectations are declarative predicates rather than literal values. The
exact string a model puts in `query` is not knowable in advance and is not the
point; that it searched for the concept rather than pasting the learner's whole
sentence is the point, and that is what `includes_any` checks.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any

import yaml

from backend.tools.tutor_tools import TUTOR_TOOLS

DATASET = os.path.join(os.path.dirname(__file__), "scenarios.yaml")

# Grouping for the per-scenario breakdown in the report. An average over a mixed
# set hides which kind of task the agent is bad at.
CATEGORIES = [
    "concept",          # a general question the corpus can answer
    "memory",           # something the tutor should already know about them
    "notes",            # what other learners said about this problem
    "refusal",          # R1: must reach no tool at all
    "re-query",         # the first search should miss and a second should fix it
    "multi-tool",       # needs more than one source
]

KNOWN_TOOLS = {t.name for t in TUTOR_TOOLS}

# The predicate vocabulary an `arguments:` block may use. Kept deliberately small
# — a scenario file that can express arbitrary code is a scenario file nobody
# can read at a glance, and readability is what makes an eval set worth trusting.
PREDICATES = {"includes_any", "includes_all", "excludes", "between", "equals"}


@dataclass
class Scenario:
    id: str
    category: str
    task: str
    outcome: str
    # each entry is one acceptable ordered sequence of tool names
    accepted: list[list[str]] = field(default_factory=list)
    # tool name -> arg name -> {predicate: value}
    arguments: dict[str, dict[str, dict]] = field(default_factory=dict)
    setup: dict[str, Any] = field(default_factory=dict)
    notes: str = ""

    @property
    def tool_free(self) -> bool:
        """True when every acceptable trajectory is the empty one.

        The refusal scenario is the case that matters: `graphs/turn.py::_refuse`
        never reaches a model, so a correct run calls nothing. Metrics that
        divide by the number of expected calls are *undefined* here rather than
        zero, which is the same discipline the retrieval metrics use for the
        out-of-corpus cases.
        """
        return all(not alt for alt in self.accepted)


def _check_predicates(scenario_id: str, arguments: dict) -> None:
    for tool, args in arguments.items():
        if tool not in KNOWN_TOOLS:
            raise ValueError(
                f"{scenario_id}: arguments for unknown tool {tool!r}. "
                f"Known tools: {', '.join(sorted(KNOWN_TOOLS))}")
        for arg, constraint in args.items():
            if not isinstance(constraint, dict):
                raise ValueError(
                    f"{scenario_id}: {tool}.{arg} must be a mapping of "
                    f"predicate to value, got {type(constraint).__name__}")
            unknown = set(constraint) - PREDICATES
            if unknown:
                raise ValueError(
                    f"{scenario_id}: {tool}.{arg} uses unknown predicate(s) "
                    f"{', '.join(sorted(unknown))}. "
                    f"Known: {', '.join(sorted(PREDICATES))}")


def load(path: str = DATASET) -> list[Scenario]:
    """Read the scenario file, validating hard.

    Every failure here is a mistake in the data rather than in a run, so it is
    worth catching before a single model call is paid for.
    """
    with open(path, encoding="utf-8") as fh:
        raw = yaml.safe_load(fh) or []

    out: list[Scenario] = []
    seen: set[str] = set()
    for entry in raw:
        sid = entry.get("id", "")
        if not sid:
            raise ValueError(f"a scenario has no id: {entry!r}")
        if sid in seen:
            raise ValueError(f"duplicate scenario id: {sid}")
        seen.add(sid)

        category = entry.get("category", "")
        if category not in CATEGORIES:
            raise ValueError(
                f"{sid}: unknown category {category!r}. "
                f"Known: {', '.join(CATEGORIES)}")

        accepted = entry.get("accepted")
        if accepted is None:
            raise ValueError(f"{sid}: no `accepted` trajectories given. Use "
                             f"`accepted: [[]]` to mean 'no tools, on purpose'.")
        if not isinstance(accepted, list) or not accepted:
            raise ValueError(f"{sid}: `accepted` must be a non-empty list of "
                             f"tool-name sequences")
        for alt in accepted:
            if not isinstance(alt, list):
                raise ValueError(f"{sid}: each `accepted` entry must be a list "
                                 f"of tool names, got {alt!r}")
            for name in alt:
                if name not in KNOWN_TOOLS:
                    raise ValueError(
                        f"{sid}: unknown tool {name!r} in an accepted "
                        f"trajectory. Known: {', '.join(sorted(KNOWN_TOOLS))}")

        arguments = entry.get("arguments") or {}
        _check_predicates(sid, arguments)

        if not (entry.get("outcome") or "").strip():
            raise ValueError(f"{sid}: no `outcome` — there is nothing to judge "
                             f"goal completion against")

        out.append(Scenario(
            id=sid,
            category=category,
            task=entry["task"],
            outcome=entry["outcome"].strip(),
            accepted=accepted,
            arguments=arguments,
            setup=entry.get("setup") or {},
            notes=(entry.get("notes") or "").strip(),
        ))

    missing = set(CATEGORIES) - {s.category for s in out}
    if missing:
        raise ValueError(f"no scenario covers: {', '.join(sorted(missing))}")
    return out


def by_category(scenarios: list[Scenario]) -> dict[str, list[Scenario]]:
    """Grouped, in CATEGORIES order, skipping empties."""
    groups: dict[str, list[Scenario]] = {}
    for category in CATEGORIES:
        group = [s for s in scenarios if s.category == category]
        if group:
            groups[category] = group
    return groups
