"""Markdown tables, shared by both runners.

Small on purpose. The output of an eval harness is a table someone reads and
believes, so the formatting should be boring and the numbers should not be
rounded until the last possible moment.
"""
from __future__ import annotations


def table(headers: list[str], rows: list[list[str]]) -> str:
    """A GitHub-flavoured markdown table, columns padded to line up in source."""
    widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(cell))

    def line(cells: list[str]) -> str:
        return "| " + " | ".join(c.ljust(widths[i]) for i, c in enumerate(cells)) + " |"

    out = [line(headers), "|" + "|".join("-" * (w + 2) for w in widths) + "|"]
    out.extend(line(row) for row in rows)
    return "\n".join(out)


def num(value: float | None, places: int = 3) -> str:
    """A score, or an em dash for undefined. Never a zero standing in for one."""
    return "—" if value is None else f"{value:.{places}f}"


def delta(after: float, before: float, places: int = 3) -> str:
    """Signed change, with a plus sign so a gain reads as one at a glance."""
    difference = after - before
    if abs(difference) < 10 ** (-places) / 2:
        return "±0"
    return f"{difference:+.{places}f}"
