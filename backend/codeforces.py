"""Codeforces integration — fetch real problems (statement, I/O spec, sample tests).

The public CF API lists problems (metadata) but does NOT expose statements or
test data; those live on the HTML problem page, which is behind Cloudflare.
cloudscraper gets past that. We parse the statement, the input/output
specifications, the sample tests, and the time/memory limits.

Limitation: only the *sample* tests are on the page (CF's full hidden tests
aren't public). So verdicts here are AC/WA/RE/CE against samples — a real
correctness signal, but not the TLE-on-huge-input signal the calibrated offline
problem had. Generating stress tests for arbitrary problems is a natural next step.
"""
from __future__ import annotations

import random
import re
from urllib.parse import urljoin

import cloudscraper
from bs4 import BeautifulSoup

from .problem import Problem, Test

_ORIGIN = "https://codeforces.com"

_API_LIST = "https://codeforces.com/api/problemset.problems"
_PAGE = "https://codeforces.com/problemset/problem/{cid}/{idx}"

# Beginner-friendly, likely-simple-I/O rating band.
_MIN_RATING = 800
_MAX_RATING = 1200
# Tags that tend to break the "read stdin, print stdout" assumption.
_SKIP_TAGS = {"interactive", "*special"}

_scraper = cloudscraper.create_scraper()
_candidates: list[dict] | None = None


def _candidate_problems() -> list[dict]:
    global _candidates
    if _candidates is None:
        data = _scraper.get(_API_LIST, timeout=30).json()
        if data.get("status") != "OK":
            raise RuntimeError("Codeforces API returned non-OK status")
        out = []
        for p in data["result"]["problems"]:
            rating = p.get("rating")
            if not p.get("contestId") or rating is None:
                continue
            if not (_MIN_RATING <= rating <= _MAX_RATING):
                continue
            if _SKIP_TAGS.intersection(p.get("tags", [])):
                continue
            out.append(p)
        if not out:
            raise RuntimeError("no candidate problems from Codeforces")
        _candidates = out
    return _candidates


def _pre_text(pre) -> str:
    """Extract sample text, handling CF's per-line <div> wrapping."""
    if pre is None:
        return ""
    lines = pre.find_all("div", class_="test-example-line")
    text = "\n".join(l.get_text() for l in lines) if lines else pre.get_text()
    return text.strip("\n")


def _section_text(div) -> str:
    if div is None:
        return ""
    # drop the "Input"/"Output" section title, keep the body
    title = div.find(class_="section-title")
    if title:
        title.extract()
    return _tidy(div.get_text("\n"))


def _tidy(text: str) -> str:
    lines = [ln.rstrip() for ln in text.splitlines()]
    out: list[str] = []
    for ln in lines:
        if ln == "" and out and out[-1] == "":
            continue  # collapse runs of blank lines
        out.append(ln)
    return "\n".join(out).strip()


def _parse_limit(stmt, selector: str, unit_re: str, default: int, scale: float) -> int:
    el = stmt.select_one(selector)
    if el:
        m = re.search(unit_re, el.get_text())
        if m:
            return int(float(m.group(1)) * scale)
    return default


def fetch_problem(contest_id: int, index: str, meta: dict | None = None) -> Problem:
    url = _PAGE.format(cid=contest_id, idx=index)
    html = _scraper.get(url, timeout=30).text
    soup = BeautifulSoup(html, "html.parser")
    stmt = soup.select_one(".problem-statement")
    if stmt is None:
        raise ValueError(f"no problem-statement found at {url}")

    inputs = [_pre_text(d.find("pre")) for d in stmt.select(".sample-test .input")]
    outputs = [_pre_text(d.find("pre")) for d in stmt.select(".sample-test .output")]
    if not inputs or len(inputs) != len(outputs):
        raise ValueError(f"could not parse sample tests at {url}")
    tests = [
        Test(
            name=f"sample{i + 1}",
            stdin=inp if inp.endswith("\n") else inp + "\n",
            expected_stdout=out if out.endswith("\n") else out + "\n",
        )
        for i, (inp, out) in enumerate(zip(inputs, outputs))
    ]

    input_spec = _section_text(stmt.select_one(".input-specification"))
    output_spec = _section_text(stmt.select_one(".output-specification"))
    io_format = (
        "Standard input — read exactly this:\n"
        f"{input_spec or '(not specified)'}\n\n"
        "Standard output — write exactly this:\n"
        f"{output_spec or '(not specified)'}"
    )

    title_el = stmt.select_one(".header .title")
    title = title_el.get_text(strip=True) if title_el else ""
    # CF titles look like "A. Watermelon" — drop the leading index prefix so it
    # isn't duplicated with the "4A" we prepend below.
    title = re.sub(r"^[A-Za-z]\d?\.\s*", "", title) or f"{contest_id}{index}"

    # Rich HTML for the UI: make asset/link URLs absolute, drop scripts/styles,
    # keep the sample block (readers expect the examples inline). Built BEFORE we
    # strip samples for the plain-text version below.
    for tag in stmt.find_all(["script", "style"]):
        tag.decompose()
    for img in stmt.find_all("img"):
        if img.get("src"):
            img["src"] = urljoin(_ORIGIN, img["src"])
    for a in stmt.find_all("a"):
        if a.get("href"):
            a["href"] = urljoin(_ORIGIN, a["href"])
    statement_html = str(stmt)

    # Plain-text statement for the tutor's LLM context: drop the sample block.
    for s in stmt.select(".sample-tests"):
        s.decompose()
    statement = _tidy(stmt.get_text("\n"))

    tl = _parse_limit(soup.select_one(".problem-statement"),
                      ".time-limit", r"([\d.]+)\s*second", 2000, 1000.0)
    ml = _parse_limit(soup.select_one(".problem-statement"),
                      ".memory-limit", r"(\d+)\s*megabyte", 256, 1.0)

    meta = meta or {}
    return Problem(
        name=f"{contest_id}{index} - {title}",
        statement=statement,
        io_format=io_format,
        tags=meta.get("tags", []),
        tests=tests,
        time_limit_ms=tl,
        memory_limit_mb=ml,
        url=url,
        rating=meta.get("rating"),
        source="codeforces",
        statement_html=statement_html,
    )


def key_of(contest_id: int, index: str) -> str:
    return _PAGE.format(cid=contest_id, idx=index)


def parse_ref(url: str | None) -> tuple[int | None, str | None]:
    m = re.search(r"/problem/(\d+)/([^/]+)/?$", url or "")
    return (int(m.group(1)), m.group(2)) if m else (None, None)


def random_problem(
    max_attempts: int = 8,
    rng: random.Random | None = None,
    exclude_keys: set[str] | None = None,
    min_rating: int | None = None,
    max_rating: int | None = None,
) -> Problem:
    """Pick a random candidate and fetch it; retry on parse/network failure.

    Adaptive selection: `exclude_keys` avoids repeats; `min_rating`/`max_rating`
    target a difficulty band. Falls back to a wider pool if the band is empty.
    """
    candidates = _candidate_problems()
    exclude_keys = exclude_keys or set()
    rng = rng or random.Random()

    def in_band(p: dict) -> bool:
        r = p.get("rating")
        if min_rating is not None and (r is None or r < min_rating):
            return False
        if max_rating is not None and (r is None or r > max_rating):
            return False
        return True

    pool = [p for p in candidates
            if key_of(p["contestId"], p["index"]) not in exclude_keys and in_band(p)]
    if not pool:  # band exhausted or all seen — widen to anything unseen
        pool = [p for p in candidates
                if key_of(p["contestId"], p["index"]) not in exclude_keys] or candidates

    tried: set[tuple[int, str]] = set()
    last_err: Exception | None = None
    for _ in range(max_attempts):
        p = rng.choice(pool)
        key = (p["contestId"], p["index"])
        if key in tried:
            continue
        tried.add(key)
        try:
            return fetch_problem(p["contestId"], p["index"], meta=p)
        except Exception as exc:  # try a different problem
            last_err = exc
            continue
    raise RuntimeError(f"could not fetch a Codeforces problem: {last_err}")
