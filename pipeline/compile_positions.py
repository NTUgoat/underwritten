"""Compile the live positions from Markdown, enforcing METHOD.md §9.

§9 puts real constraints on a position, and every one of them is a thing that
is easy to intend and easy to forget at one in the morning on the third draft:

* a stance in the first **eight words**;
* expected return as a **spread over a bottom-up cost of capital, in basis
  points** - never a target price;
* every hurdle line **cites its source**;
* **three** dated, machine-checkable kill criteria - not two, not four;
* no target price, IRR, MOIC, Sharpe ratio, or benchmark-relative alpha, because
  those describe a different kind of investor from the one writing.

This compiler refuses to emit anything unless all of that holds. The point is
not tidiness: a position that quietly drops its third kill criterion is a
position with no falsifier, which is the one thing the whole publication claims
to always have.

    .venv/Scripts/python.exe -m pipeline.compile_positions

See docs/positions.md for the authoring format.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

from . import config
from .compile_note import (
    FIGURE,
    NoteError,
    Problems,
    check_bare_numerals,
    parse_citations,
    parse_front_matter,
    segments_for,
)

SOURCE = config.ROOT / "docs" / "positions.md"
OUTPUT = config.DERIVED / "positions.json"

STANCE_WORD_LIMIT = 8
REQUIRED_KILL_CRITERIA = 3

# §9: vocabulary that describes a different kind of investor. Matched
# case-insensitively on whole words so "alphabet" and "shareholder" are safe.
FORBIDDEN = {
    r"\btarget price\b": "a target price (§9 requires a spread over the hurdle)",
    r"\bprice target\b": "a price target (§9 requires a spread over the hurdle)",
    r"\bIRR\b": "IRR",
    r"\bMOIC\b": "MOIC",
    r"\bSharpe\b": "a Sharpe ratio",
    r"\balpha\b": "benchmark-relative alpha",
    r"\bTSR vs\b": "a benchmark-relative comparison",
}

HEADER = re.compile(
    r"^##\s+(?P<issuer>.+?)\s*\((?P<ticker>[A-Z.]+)\)\s*\|\s*cik=(?P<cik>\d+)\s*\|\s*"
    r"(?P<status>OPEN|HELD|KILLED)\s*\|\s*opened=(?P<opened>[\d-]+)\s*$",
    re.MULTILINE,
)
SUBHEAD = re.compile(r"^###\s+(.+?)\s*$", re.MULTILINE)
HURDLE_ROW = re.compile(r"^\|\s*(.+?)\s*\|\s*(\d+)\s*\|\s*(.+?)\s*\|\s*$", re.MULTILINE)
KILL_ROW = re.compile(r"^\s*\d\.\s*([\d-]{10})\s*\|\s*(machine|manual)\s*\|\s*(.+?)\s*$", re.MULTILINE)
SPREAD = re.compile(r"central\s*=\s*(-?\d+).*?low\s*=\s*(-?\d+).*?high\s*=\s*(-?\d+)", re.S)
TOTAL = re.compile(r"^total\s*:\s*(\d+)\s*$", re.MULTILINE)
EDITION = re.compile(r"^edition\s*:\s*(.+?)\s*$", re.MULTILINE)


def _sections(block: str) -> dict[str, str]:
    """Split one position body into its ### subsections."""
    out: dict[str, str] = {}
    marks = list(SUBHEAD.finditer(block))
    for i, mark in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(block)
        out[mark.group(1).strip().lower()] = block[mark.end() : end].strip()
    return out


def _require(parts: dict[str, str], key: str, issuer: str) -> str:
    if key not in parts or not parts[key].strip():
        raise NoteError(f"{issuer}: missing required '### {key}' section (§9).")
    return parts[key].strip()


def _check_forbidden(text: str, issuer: str, problems: Problems) -> None:
    for pattern, label in FORBIDDEN.items():
        if re.search(pattern, text, re.IGNORECASE):
            problems.errors.append(
                f"  {issuer}: uses {label}. §9 excludes it deliberately - it "
                f"describes a different kind of investor."
            )


def compile_positions(source: Path = SOURCE) -> dict:
    if not source.exists():
        raise NoteError(f"No positions at {source}. See its docstring for the format.")

    meta, body, _ = parse_front_matter(source.read_text(encoding="utf-8"))
    citations, prose = parse_citations(body)
    problems = Problems(
        header=(
            "No positions were written. METHOD.md §9 governs what a position "
            "must carry, and every failure below is one of its requirements."
        )
    )

    heads = list(HEADER.finditer(prose))
    if not heads:
        raise NoteError(
            "No positions found. Each opens with:\n"
            "  ## Issuer Name (TICK) | cik=1234567 | OPEN | opened=2026-09-06"
        )

    positions = []
    for i, head in enumerate(heads):
        end = heads[i + 1].start() if i + 1 < len(heads) else len(prose)
        block = prose[head.end() : end]
        issuer = head.group("issuer")
        parts = _sections(block)

        _check_forbidden(block, issuer, problems)

        # -- stance: eight words, and it is the first thing a reader sees ----
        stance = _require(parts, "stance", issuer)
        n_words = len(re.findall(r"\b[\w'-]+\b", stance))
        if n_words > STANCE_WORD_LIMIT:
            problems.errors.append(
                f"  {issuer}: stance is {n_words} words; §9 allows "
                f"{STANCE_WORD_LIMIT}. Say it shorter: {stance!r}"
            )

        # -- hurdle: built line by line, every line cited --------------------
        hurdle_text = _require(parts, "hurdle", issuer)
        lines = []
        for label, bps, source_text in HURDLE_ROW.findall(hurdle_text):
            if label.lower() in {"line", "---", ":---"}:
                continue
            if not source_text or source_text.strip() in {"", "-", "---"}:
                problems.errors.append(
                    f"  {issuer}: hurdle line {label!r} cites no source (§9)."
                )
            lines.append({"label": label, "bps": int(bps), "source": source_text})
        if not lines:
            problems.errors.append(f"  {issuer}: hurdle has no cited lines (§9).")

        total_match = TOTAL.search(hurdle_text)
        edition_match = EDITION.search(hurdle_text)
        if not total_match:
            problems.errors.append(f"  {issuer}: hurdle has no 'total: <bps>' line.")
        if not edition_match:
            problems.errors.append(
                f"  {issuer}: hurdle has no 'edition:' line naming the dated "
                f"Damodaran edition it came from (§9)."
            )
        total_bps = int(total_match.group(1)) if total_match else 0
        if lines and total_bps and sum(x["bps"] for x in lines) != total_bps:
            problems.errors.append(
                f"  {issuer}: hurdle lines sum to "
                f"{sum(x['bps'] for x in lines)} bps but total says {total_bps}."
            )

        # -- expected return: a spread, in basis points ----------------------
        spread_text = _require(parts, "expected spread", issuer)
        spread_match = SPREAD.search(spread_text)
        if not spread_match:
            problems.errors.append(
                f"  {issuer}: expected spread must read "
                f"'central=<bps> low=<bps> high=<bps>' (§9)."
            )
        central, low, high = (
            (int(spread_match.group(1)), int(spread_match.group(2)), int(spread_match.group(3)))
            if spread_match
            else (0, 0, 0)
        )
        if spread_match and not (low <= central <= high):
            problems.errors.append(
                f"  {issuer}: spread is incoherent - low {low}, central "
                f"{central}, high {high}."
            )

        # -- kill criteria: exactly three, each dated ------------------------
        kill_text = _require(parts, "kill criteria", issuer)
        kills = [
            {
                "n": n + 1,
                "by_date": by_date,
                "machine_checkable": kind == "machine",
                "test": test,
                "status": "NOT_TRIGGERED",
                "last_checked": "",
            }
            for n, (by_date, kind, test) in enumerate(KILL_ROW.findall(kill_text))
        ]
        if len(kills) != REQUIRED_KILL_CRITERIA:
            problems.errors.append(
                f"  {issuer}: {len(kills)} kill criteria; §9 requires exactly "
                f"{REQUIRED_KILL_CRITERIA}. A position with fewer has no "
                f"published falsifier, which is the point of publishing it."
            )

        def prose_for(key: str) -> dict:
            text = _require(parts, key, issuer)
            check_bare_numerals(text, 0, problems)
            return {"segments": segments_for(text, citations, 0, problems)}

        positions.append(
            {
                "id": f"{head.group('cik')}-{head.group('ticker').lower()}",
                "issuer": issuer,
                "ticker": head.group("ticker"),
                "cik": int(head.group("cik")),
                "status": head.group("status"),
                "opened": head.group("opened"),
                "author": meta.get("author", "Jex Lin"),
                "horizons_years": [10, 20],
                "stance": stance,
                "variant_perception": prose_for("variant perception"),
                "what_would_have_to_be_true": [
                    {"segments": segments_for(item.strip("- ").strip(), citations, 0, problems)}
                    for item in _require(parts, "what would have to be true", issuer).splitlines()
                    if item.strip().startswith("-")
                ],
                "hurdle": {
                    "total_bps": total_bps,
                    "edition": edition_match.group(1) if edition_match else "",
                    "lines": lines,
                },
                "expected_spread_bps": {
                    "central": central,
                    "low": low,
                    "high": high,
                    "basis": "spread over the hurdle above",
                },
                "downside": prose_for("downside"),
                "kill_criteria": kills,
                "disclosure": _require(parts, "disclosure", issuer),
            }
        )

    problems.raise_if_any()
    return {"as_at": meta.get("as_at", config.AS_AT_DATE), "positions": positions}


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    try:
        data = compile_positions()
    except NoteError as exc:
        print(str(exc))
        print("\nNothing was written.")
        return 1

    OUTPUT.write_text(json.dumps(data, indent=2), encoding="utf-8")
    for position in data["positions"]:
        print(
            f"  {position['ticker']:<6} {position['status']:<7} "
            f"hurdle {position['hurdle']['total_bps']} bps, "
            f"spread {position['expected_spread_bps']['central']} bps, "
            f"{len(position['kill_criteria'])} kill criteria"
        )
    print(f"\nWritten -> {OUTPUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
