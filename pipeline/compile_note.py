"""Compile the written note from Markdown into the site's segment JSON.

Writing 2,500 words of prose directly as citation-segmented JSON is miserable
and error-prone, and errors here are published errors. So the note is authored
in Markdown at ``docs/note.md`` and compiled to ``data/derived/note.json``.

The compiler is not a convenience. It is where METHOD.md §8.3 is enforced:

    Every numeral in published prose resolves to an accession number.

A bare number in the prose is a **build failure**, naming the line it is on.
That is the same rule the site applies at render time, moved to the moment of
writing, where it is cheap to fix and impossible to forget.

    .venv/Scripts/python.exe -m pipeline.compile_note

--------------------------------------------------------------------------
Authoring format
--------------------------------------------------------------------------

    ---
    title: What listing-document promises were worth
    standfirst: One sentence under the title.
    author: Jex Lin
    dated: 2026-09-05
    ---

    ## The finding

    Of the {{214|m1}} metrics defined across {{50|m2}} issuers, {{58|m3}}
    had been discontinued by 2026-08-28.

    > A pull quote is a blockquote.

    ~~A superseded sentence, struck in place per §8.5.~~ (revised 2026-09-08:
    the sentence was wrong about Grab.)

    [m1]: 0001104659-21-064530 | 10-K | 2023-11-14 | cik=1759546
          "verbatim sentence from the filing, unedited"

Rules the compiler enforces:

* ``{{value|key}}`` is a cited figure; ``key`` must have a definition.
* Every citation definition needs an accession, a form, a date, and a quote.
* A digit appearing in prose outside ``{{...}}`` fails the build, unless it is
  part of a date, a section reference (§7.2), or an explicitly allowed word.
* Nothing is emitted at all if any check fails - a partial note is worse than
  no note, because it looks finished.
"""

from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

from . import config

NOTE_SOURCE = config.ROOT / "docs" / "note.md"
NOTE_OUTPUT = config.DERIVED / "note.json"

FIGURE = re.compile(r"\{\{([^|}]+)\|([A-Za-z0-9_-]+)\}\}")
# A citation definition may wrap onto indented continuation lines - the verbatim
# quote is usually long enough that it has to. Matching only the first line
# silently loses the quote and reports "no quoted sentence" about a definition
# that has one, which is a confusing error about the wrong thing.
CITATION_DEF = re.compile(
    r"^\[([A-Za-z0-9_-]+)\]:[ \t]*(.*(?:\n[ \t]+\S.*)*)", re.MULTILINE
)
STRUCK = re.compile(r"~~(.+?)~~\s*\(revised ([0-9-]+):\s*(.+?)\)", re.DOTALL)

# Digits that are legitimately not a cited figure.
ALLOWED_BARE = re.compile(
    r"""
    (?:\d{4}-\d{2}-\d{2})      # an ISO date
  | (?:§\s*\d+(?:\.\d+)*)      # a METHOD.md section reference
  | (?:\b(?:19|20)\d{2}\b)     # a bare year
  | (?:\b10-[KQ]\b|\b20-F\b|\b6-K\b|\b8-K\b|\bS-[14]\b|\bF-[14]\b|\b25-NSE\b)
  | (?:\bItem\s+\d+\.\d+\b)    # 8-K item references
  | (?:\bASC\s+\d+\b|\bIFRS\s+\d+\b)
  | (?:\d{10}-\d{2}-\d{6})     # an accession number
    """,
    re.VERBOSE,
)


class NoteError(Exception):
    """The note cannot be compiled. Nothing is written."""


@dataclass(frozen=True)
class Citation:
    key: str
    accession: str
    form: str
    filed: str
    cik: int | None
    quote: str

    def as_dict(self, value: str) -> dict:
        out = {
            "value": value,
            "quote": self.quote,
            "form": self.form,
            "filed": self.filed,
            "accession": self.accession,
        }
        if self.cik is not None:
            out["cik"] = self.cik
        return out


@dataclass
class Problems:
    """Collected authoring failures. Reported together, never one at a time.

    `header` is set by the caller so the positions compiler does not report a
    §9 violation under a §8.3 heading - a misleading error in a project whose
    whole claim is precision is worse than a terse one.
    """

    errors: list[str] = field(default_factory=list)
    header: str = (
        "The note was not written. METHOD.md §8.3 requires every numeral in "
        "published prose to resolve to an accession number."
    )

    def add(self, line_no: int, message: str) -> None:
        self.errors.append(f"  docs/note.md:{line_no}: {message}")

    def raise_if_any(self) -> None:
        if self.errors:
            raise NoteError(self.header + "\n\n" + "\n".join(self.errors))


def parse_front_matter(text: str) -> tuple[dict[str, str], str, int]:
    """Return (metadata, body, body_start_line)."""
    if not text.startswith("---"):
        raise NoteError("docs/note.md must open with a --- front-matter block.")
    end = text.find("\n---", 3)
    if end == -1:
        raise NoteError("Front matter is not closed with ---.")
    head = text[3:end]
    meta: dict[str, str] = {}
    for line in head.splitlines():
        if not line.strip():
            continue
        key, _, value = line.partition(":")
        meta[key.strip()] = value.strip()
    body = text[end + 4 :]
    return meta, body, head.count("\n") + 3


def parse_citations(body: str) -> tuple[dict[str, Citation], str]:
    """Pull citation definitions out of the body and return the prose."""
    citations: dict[str, Citation] = {}

    def take(match: re.Match[str]) -> str:
        key, raw = match.group(1), match.group(2)
        quote_match = re.search(r'"(.*)"', raw, re.DOTALL)
        if not quote_match:
            raise NoteError(
                f"Citation [{key}] has no quoted verbatim sentence. "
                'Add "..." with the sentence exactly as filed.'
            )
        quote = quote_match.group(1).strip()
        fields = [p.strip() for p in raw[: quote_match.start()].split("|") if p.strip()]
        if len(fields) < 3:
            raise NoteError(
                f"Citation [{key}] needs accession | form | filing-date "
                "before the quote."
            )
        cik = None
        for part in fields:
            if part.lower().startswith("cik="):
                cik = int(part.split("=", 1)[1])
        citations[key] = Citation(
            key=key,
            accession=fields[0],
            form=fields[1],
            filed=fields[2],
            cik=cik,
            quote=quote,
        )
        return ""

    prose = CITATION_DEF.sub(take, body)
    return citations, prose


def segments_for(text: str, citations: dict[str, Citation], line_no: int,
                 problems: Problems) -> list[dict]:
    """Split one paragraph into text and cited-figure segments."""
    out: list[dict] = []
    cursor = 0
    for match in FIGURE.finditer(text):
        before = text[cursor : match.start()]
        if before:
            out.append({"text": before})
        value, key = match.group(1).strip(), match.group(2).strip()
        citation = citations.get(key)
        if citation is None:
            problems.add(line_no, f"figure {{{{{value}|{key}}}}} has no [{key}] definition")
            out.append({"text": value})
        else:
            out.append({"figure": citation.as_dict(value)})
        cursor = match.end()
    tail = text[cursor:]
    if tail:
        out.append({"text": tail})
    return out


def check_bare_numerals(text: str, line_no: int, problems: Problems) -> None:
    """Fail on a digit in prose that is not a cited figure (METHOD.md §8.3)."""
    stripped = FIGURE.sub("", text)
    stripped = ALLOWED_BARE.sub("", stripped)
    for hit in re.finditer(r"\d[\d,.]*%?", stripped):
        problems.add(
            line_no,
            f"uncited numeral {hit.group(0)!r} - wrap it as "
            "{{value|key}} and define [key], or it cannot be published",
        )


def compile_note(source: Path = NOTE_SOURCE) -> dict:
    if not source.exists():
        raise NoteError(
            f"No note at {source}. Write it in Markdown - see the module "
            "docstring for the format."
        )

    text = source.read_text(encoding="utf-8")
    meta, body, offset = parse_front_matter(text)
    citations, prose = parse_citations(body)
    problems = Problems()

    sections: list[dict] = []
    current: dict | None = None
    words = 0

    for block_no, raw_block in enumerate(re.split(r"\n\s*\n", prose)):
        block = raw_block.strip()
        if not block:
            continue
        line_no = offset + prose[: prose.find(raw_block)].count("\n") + 1

        if block.startswith("#"):
            heading = block.lstrip("#").strip()
            current = {
                "id": re.sub(r"[^a-z0-9]+", "-", heading.lower()).strip("-"),
                "heading": heading,
                "blocks": [],
            }
            sections.append(current)
            continue

        if current is None:
            current = {"id": "opening", "heading": "", "blocks": []}
            sections.append(current)

        words += len(re.findall(r"\b\w+\b", FIGURE.sub("x", block)))
        check_bare_numerals(block, line_no, problems)

        struck = STRUCK.search(block)
        if struck:
            current["blocks"].append(
                {
                    "struck": {
                        "segments": segments_for(
                            struck.group(1).strip(), citations, line_no, problems
                        ),
                        "date": struck.group(2),
                        "note": struck.group(3).strip(),
                    }
                }
            )
            continue

        if block.startswith(">"):
            current["blocks"].append(
                {"pull": re.sub(r"^>\s?", "", block, flags=re.MULTILINE).strip()}
            )
            continue

        current["blocks"].append(
            {"segments": segments_for(block, citations, line_no, problems)}
        )

    problems.raise_if_any()

    if not sections:
        raise NoteError("The note has no content.")

    return {
        "title": meta.get("title", ""),
        "standfirst": meta.get("standfirst", ""),
        "author": meta.get("author", "Jex Lin"),
        "dated": meta.get("dated", config.AS_AT_DATE),
        "word_count": words,
        "sections": sections,
        "revisions": [],
    }


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    try:
        note = compile_note()
    except NoteError as exc:
        print(str(exc))
        print("\nNothing was written. A partial note is worse than none.")
        return 1

    NOTE_OUTPUT.write_text(json.dumps(note, indent=2), encoding="utf-8")
    cited = sum(
        1
        for section in note["sections"]
        for block in section["blocks"]
        for segment in block.get("segments", [])
        if "figure" in segment
    )
    print(f"Sections     : {len(note['sections'])}")
    print(f"Words        : {note['word_count']:,}")
    print(f"Cited figures: {cited}")
    print(f"Written      -> {NOTE_OUTPUT}")
    print()
    print("Every numeral in the note resolves to an accession number.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
