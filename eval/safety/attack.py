"""The attack set: loaded, validated, and shaped like a scenario on purpose.

`Attack` carries the same `id`, `task` and `setup` attributes a
`eval.agent.scenario.Scenario` does, so `eval.agent.run_agent.capture` drives
both without knowing the difference. An attack is just a turn whose desired
outcome is "nothing happened", and running it through a second, near-identical
harness would be two chances to get the capture wrong.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any

import yaml

DATASET = os.path.join(os.path.dirname(__file__), "attacks.yaml")

CATEGORIES = ["direct", "indirect", "tool-abuse", "exfiltration"]


@dataclass
class Attack:
    id: str
    category: str
    task: str
    demand: str
    setup: dict[str, Any] = field(default_factory=dict)
    notes: str = ""


def load(path: str = DATASET) -> list[Attack]:
    with open(path, encoding="utf-8") as fh:
        raw = yaml.safe_load(fh) or []

    out: list[Attack] = []
    seen: set[str] = set()
    for entry in raw:
        aid = entry.get("id", "")
        if not aid:
            raise ValueError(f"an attack has no id: {entry!r}")
        if aid in seen:
            raise ValueError(f"duplicate attack id: {aid}")
        seen.add(aid)
        if entry.get("category") not in CATEGORIES:
            raise ValueError(f"{aid}: unknown category {entry.get('category')!r}. "
                             f"Known: {', '.join(CATEGORIES)}")
        if not (entry.get("demand") or "").strip():
            raise ValueError(f"{aid}: no `demand` — without it there is nothing "
                             f"to ask the judge, and 'did it resist?' has no "
                             f"definition")
        out.append(Attack(
            id=aid,
            category=entry["category"],
            task=entry["task"].strip(),
            demand=entry["demand"].strip(),
            setup=entry.get("setup") or {},
            notes=(entry.get("notes") or "").strip(),
        ))

    missing = set(CATEGORIES) - {a.category for a in out}
    if missing:
        raise ValueError(f"no attack covers: {', '.join(sorted(missing))}")
    return out


def by_category(attacks: list[Attack]) -> dict[str, list[Attack]]:
    groups: dict[str, list[Attack]] = {}
    for category in CATEGORIES:
        group = [a for a in attacks if a.category == category]
        if group:
            groups[category] = group
    return groups
