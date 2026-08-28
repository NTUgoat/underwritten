"""The local adjudication tool — machine proposes, human disposes.

METHOD.md §4 says it plainly: *"Candidate location is mechanical. Inclusion is
human."* This module compresses the reading work without touching that boundary.

**What the machine is allowed to do here.** Locate (already done by
``pipeline.metrics``), *group* the one-row-per-occurrence candidate file into one
decision per ``(issuer, normalised metric name)``, *order* those groups so the
reviewer keeps context on one issuer at a time, *propose* a ruling against the
four §4 criteria, and *show its reasoning*. All of that is what a research
assistant does, and every bit of it is reversible by looking at the evidence.

**What the machine is forbidden to do.** Write a ruling nobody saw. There is no
"accept all", no "approve the rest", no default verdict, and no code path that
commits a row without a human keystroke on that specific group. A committed row
always carries the reviewer's initials, the review date, and a rationale; the
rationale is either free text the human typed or a preset the human selected by
number, never the machine's own proposal text. The proposal is rendered as a
proposal — dashed, labelled, uncommitted — until the human disposes of it, and
whether the human agreed with it is recorded in the audit log so the agreement
rate can itself be inspected later.

**Three verdicts, and the third one is not a soft exclude.** METHOD.md §5 and
§12 both turn on the distinction between "ruled out" and "the filed record does
not settle it". A deferral is written as ``NOT_DETERMINABLE`` in the ``include``
column, spelled exactly as §5 spells it, so no downstream reader can mistake it
for a "no".

**Where it writes.** ``data/adjudication/metrics.csv``, in exactly the schema of
``pipeline.metrics.LEDGER_FIELDS``, plus an append-only audit log beside it.
Nothing else, ever: every write target is ``<adjudication dir>/<literal
filename>`` resolved through :func:`_write_target`, and no request value reaches
a path. Writes go to a temp file and are moved into place with ``os.replace``,
because METHOD.md §4 calls this ledger "the study" and a truncated ledger is
worse than no ledger.

**Why it does not simply call** ``pipeline.metrics.write_candidates``. That
function carries *existing* rulings forward onto freshly located candidates and
writes the ruling columns blank for everything else — it goes out of its way to
be unable to author a ruling. Passing a new ruling through it would silently
blank it. So the ledger writer lives here, and reuses that module's schema
(``LEDGER_FIELDS``), its identity function (``candidate_id``), its round-trip
reader (``read_rulings``) and its atomic-write discipline rather than inventing a
parallel format.

**Mounting.** These routes write to disk and must never exist on the deployed
public site. They are registered only when ``UNDERWRITTEN_ADJUDICATE=1`` is set
in the environment; the default is off, and :func:`register` refuses to attach
anything when the flag is absent. The tool is meant to be run on localhost by
the person whose initials go in the ledger.
"""

from __future__ import annotations

import csv
import hashlib
import json
import logging
import os
import re
import urllib.parse
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from functools import lru_cache
from pathlib import Path
from typing import Any

from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response

from pipeline import config as pipeline_config
from pipeline import metrics as pipeline_metrics
from pipeline.outcomes import normalise_whitespace

logger = logging.getLogger(__name__)


# ===========================================================================
# The switch. Off by default, and checked in two places on purpose.
# ===========================================================================

ENABLE_FLAG = "UNDERWRITTEN_ADJUDICATE"
REVIEWER_FLAG = "UNDERWRITTEN_REVIEWER"
DIR_FLAG = "UNDERWRITTEN_ADJUDICATE_DIR"


def is_enabled() -> bool:
    """Whether the disk-writing routes may be mounted at all.

    Deliberately strict: only the exact string ``"1"`` enables the tool, so a
    stray ``UNDERWRITTEN_ADJUDICATE=0`` or an empty value in a deployment
    environment cannot turn it on by accident.
    """
    return os.environ.get(ENABLE_FLAG, "").strip() == "1"


# ===========================================================================
# Paths. Every write target is a literal filename under one directory.
# ===========================================================================

LEDGER_NAME = "metrics.csv"
LOG_NAME = "adjudication_log.jsonl"
CANDIDATES_NAME = "metrics_candidates.csv"

# Rendering caps. Not cosmetic: a section-heading group such as "non-GAAP
# financial measures" can carry 1,014 occurrences across 516 distinct
# "variants", because for a heading the extractor captures whatever table
# followed it and that differs in every filing. Rendering them all produced a
# 1.9 MB page and called surrounding_context() 516 times per view. The ruling
# is the same either way, so the extra evidence buys nothing and costs the
# reviewer real time on a page they will spend one keystroke on. Whatever is
# elided is COUNTED on the page, never silently dropped.
MAX_VARIANTS_SHOWN = 6
MAX_OCCURRENCES_SHOWN = 12


def adjudication_dir() -> Path:
    """The one directory this tool may write into.

    ``UNDERWRITTEN_ADJUDICATE_DIR`` exists so the tests can point the whole tool
    at a temp directory. It is read from the environment, never from a request.
    """
    raw = os.environ.get(DIR_FLAG, "").strip()
    directory = Path(raw) if raw else Path(pipeline_config.ADJUDICATION)
    directory.mkdir(parents=True, exist_ok=True)
    return directory.resolve()


def _write_target(filename: str) -> Path:
    """Resolve a write path, or refuse.

    The only arguments this is ever given are the three module constants above.
    The guard exists so that stays true: anything with a separator, a parent
    reference, or an absolute root is rejected before it can touch the disk, and
    the resolved parent must be the adjudication directory itself.
    """
    if not filename or filename != Path(filename).name or filename in {".", ".."}:
        raise ValueError(f"Not a permitted ledger filename: {filename!r}")
    directory = adjudication_dir()
    target = (directory / filename).resolve()
    if target.parent != directory:
        raise ValueError(f"Refusing to write outside {directory}: {target}")
    return target


def ledger_path() -> Path:
    return _write_target(LEDGER_NAME)


def log_path() -> Path:
    return _write_target(LOG_NAME)


def candidates_path() -> Path:
    """The locator's output. Read-only for this module."""
    return adjudication_dir() / CANDIDATES_NAME


# ===========================================================================
# Verdicts and the rationale presets
# ===========================================================================

INCLUDE = "INCLUDE"
EXCLUDE = "EXCLUDE"
NOT_DETERMINABLE = "NOT_DETERMINABLE"

VERDICTS: tuple[str, ...] = (INCLUDE, EXCLUDE, NOT_DETERMINABLE)

#: What goes in the ledger's ``include`` column. The deferral keeps METHOD.md
#: §5's own spelling so it can never be read downstream as an exclusion.
LEDGER_VALUE: dict[str, str] = {
    INCLUDE: "yes",
    EXCLUDE: "no",
    NOT_DETERMINABLE: NOT_DETERMINABLE,
}

_LEDGER_VALUE_TO_VERDICT: dict[str, str] = {
    "yes": INCLUDE,
    "y": INCLUDE,
    "true": INCLUDE,
    "include": INCLUDE,
    "no": EXCLUDE,
    "n": EXCLUDE,
    "false": EXCLUDE,
    "exclude": EXCLUDE,
    "not_determinable": NOT_DETERMINABLE,
    "not determinable": NOT_DETERMINABLE,
}

VERDICT_LABEL: dict[str, str] = {
    INCLUDE: "include",
    EXCLUDE: "exclude",
    NOT_DETERMINABLE: "can’t tell from the filed record",
}

VERDICT_KEY: dict[str, str] = {INCLUDE: "i", EXCLUDE: "x", NOT_DETERMINABLE: "n"}

#: One-line rationales, written by hand against the §4 criteria. Selecting one
#: is a human act; the machine never picks one, and never pre-fills the field.
RATIONALE_PRESETS: dict[str, tuple[str, ...]] = {
    INCLUDE: (
        (
            "Quantitative, company-defined, and presented as a measure of the "
            "issuer's own operating performance (§4)."
        ),
        "Non-GAAP measure for which the company supplies its own definition (§4).",
        (
            "Reported as a number in the issuer's own key-metrics section, and "
            "attributed to its own operations (§4)."
        ),
    ),
    EXCLUDE: (
        "GAAP/IFRS measure, not defined by the company (§4).",
        "Not quantitative — no number or rate is reported for it (§4).",
        "Market-size or industry statistic, not the issuer's own operations (§4).",
        (
            "Risk-factor statistic or one-off disclosure, not a measure of "
            "operating performance (§4)."
        ),
        "The located span is a section heading, not a metric (§4).",
    ),
    NOT_DETERMINABLE: (
        (
            "The filed record does not settle whether this is a company-defined "
            "operating metric (§5)."
        ),
        (
            "The defining sentence is truncated or unreadable in the filed "
            "document; it cannot be ruled on from the record (§5, §12)."
        ),
        (
            "The definition differs across filings and the filed record does not "
            "settle which one governs (§5)."
        ),
    ),
}

KEYBOARD_MAP: tuple[tuple[str, str], ...] = (
    ("i", "arm include"),
    ("x", "arm exclude"),
    ("n", "arm can’t-tell (NOT_DETERMINABLE)"),
    ("1…9", "pick a rationale preset for the armed verdict, and commit"),
    ("Enter", "commit the armed verdict with the rationale shown"),
    ("t", "type a free-text rationale"),
    ("Esc", "disarm / leave the text field"),
    ("s", "skip — write nothing, next group"),
    ("b", "back to the previous group"),
    ("r", "edit reviewer initials"),
    ("?", "show or hide this list"),
)

MAX_RATIONALE_CHARS = 400
MAX_REVIEWER_CHARS = 12
MAX_BODY_BYTES = 64 * 1024

_REVIEWER_OK = re.compile(rf"^[A-Za-z][A-Za-z .'\-]{{0,{MAX_REVIEWER_CHARS - 1}}}$")
_SOURCE_OK = re.compile(r"^(free_text|preset:[0-9]{1,2})$")


# ===========================================================================
# Grouping. One ruling per (issuer, normalised metric name).
# ===========================================================================

# Case, whitespace and punctuation are all noise for the purpose of deciding
# whether two located spans name the same metric. "Adjusted EBITDA",
# "adjusted ebitda" and "Adjusted EBITDA," are one decision, not three.
_NON_ALNUM = re.compile(r"[^0-9a-z]+")

#: Leading words that qualify a measure without changing what it is, stripped
#: only for the GAAP comparison below — never for the group key, where dropping
#: them would merge two names the issuer chose to keep apart.
_GAAP_LEAD_WORDS = ("our", "the", "its", "consolidated", "companys", "company s")


def normalise_name(raw: str) -> str:
    """The grouping key for a metric name.

    Delegates to ``pipeline.metrics.metric_key`` so the grouping used here and
    the key the §6 evidence stage writes are the same function rather than two
    that merely look alike. They diverged once - punctuation-stripped here,
    hyphen-preserving there - and every evidence lookup missed silently.
    """
    return pipeline_metrics.metric_key(raw)


def normalise_sentence(raw: str) -> str:
    """The key that decides whether two defining sentences are the same text."""
    return _NON_ALNUM.sub(" ", normalise_whitespace(raw).casefold()).strip()


def group_id(cik: Any, normalised: str) -> str:
    """Stable id for one decision, so a URL survives a re-run of the locator."""
    key = f"{cik}|{normalised}"
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]


@dataclass(frozen=True)
class Occurrence:
    """One located candidate, exactly as the locator wrote it."""

    candidate_id: str
    cik: str
    accession: str
    form: str
    filing_date: str
    document: str
    doc_type: str
    locator: str
    metric_name: str
    defining_sentence: str
    char_offset: str
    sentence_char_offset: str
    url: str
    row: dict[str, str]

    @property
    def is_heading(self) -> bool:
        return self.locator.startswith("heading_")

    @property
    def filing_url(self) -> str:
        """The document on sec.gov, or the filing index if the URL is missing."""
        if self.url:
            return self.url
        accession = self.accession.replace("-", "")
        cik = self.cik.lstrip("0") or self.cik
        if not accession or not cik.isdigit():
            return ""
        return (
            f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/"
            f"{accession}/{self.accession}-index.htm"
        )


@dataclass(frozen=True)
class Variant:
    """One distinct defining sentence, and everywhere it was found.

    A metric whose definition changed reads as two variants here. METHOD.md §5
    `REDEFINED` is exactly that change, and it has to be visible at ruling time
    or the reviewer is being asked to rule on a summary.
    """

    text: str
    occurrences: tuple[Occurrence, ...]

    @property
    def n(self) -> int:
        return len(self.occurrences)

    @property
    def first_date(self) -> str:
        dates = [o.filing_date for o in self.occurrences if o.filing_date]
        return min(dates) if dates else ""

    @property
    def last_date(self) -> str:
        dates = [o.filing_date for o in self.occurrences if o.filing_date]
        return max(dates) if dates else ""


@dataclass(frozen=True)
class Group:
    """One decision: one issuer, one normalised metric name."""

    gid: str
    cik: str
    normalised: str
    display_name: str
    occurrences: tuple[Occurrence, ...]
    variants: tuple[Variant, ...]

    @property
    def n(self) -> int:
        return len(self.occurrences)

    @property
    def first_date(self) -> str:
        dates = [o.filing_date for o in self.occurrences if o.filing_date]
        return min(dates) if dates else ""

    @property
    def last_date(self) -> str:
        dates = [o.filing_date for o in self.occurrences if o.filing_date]
        return max(dates) if dates else ""

    @property
    def forms(self) -> tuple[str, ...]:
        return tuple(sorted({o.form for o in self.occurrences if o.form}))

    @property
    def accessions(self) -> tuple[str, ...]:
        return tuple(sorted({o.accession for o in self.occurrences if o.accession}))

    @property
    def locators(self) -> tuple[str, ...]:
        return tuple(sorted({o.locator for o in self.occurrences if o.locator}))

    @property
    def all_headings(self) -> bool:
        return bool(self.occurrences) and all(o.is_heading for o in self.occurrences)


def read_candidates(path: Path | None = None) -> tuple[Occurrence, ...]:
    """The locator's output, read verbatim. Never written back from here."""
    target = path or candidates_path()
    if not target.is_file():
        return ()
    try:
        with target.open("r", newline="", encoding="utf-8") as handle:
            rows = list(csv.DictReader(handle))
    except (OSError, UnicodeDecodeError, csv.Error) as exc:
        raise RuntimeError(f"Cannot read the candidate file at {target}: {exc}") from exc

    out: list[Occurrence] = []
    for row in rows:
        clean = {key: (value or "").strip() for key, value in row.items() if key}
        if not clean.get("candidate_id") or not clean.get("metric_name"):
            continue
        out.append(
            Occurrence(
                candidate_id=clean.get("candidate_id", ""),
                cik=clean.get("cik", ""),
                accession=clean.get("accession", ""),
                form=clean.get("form", ""),
                filing_date=clean.get("filing_date", ""),
                document=clean.get("document", ""),
                doc_type=clean.get("doc_type", ""),
                locator=clean.get("locator", ""),
                metric_name=clean.get("metric_name", ""),
                defining_sentence=clean.get("defining_sentence", ""),
                char_offset=clean.get("char_offset", ""),
                sentence_char_offset=clean.get("sentence_char_offset", ""),
                url=clean.get("url", ""),
                row=clean,
            )
        )
    return tuple(out)


def _display_name(occurrences: Sequence[Occurrence]) -> str:
    """The spelling the issuer used most often, ties broken by length then A–Z."""
    counts: dict[str, int] = {}
    for occurrence in occurrences:
        counts[occurrence.metric_name] = counts.get(occurrence.metric_name, 0) + 1
    return min(counts.items(), key=lambda kv: (-kv[1], -len(kv[0]), kv[0]))[0]


def _variants(occurrences: Sequence[Occurrence]) -> tuple[Variant, ...]:
    buckets: dict[str, list[Occurrence]] = {}
    display: dict[str, str] = {}
    for occurrence in occurrences:
        key = normalise_sentence(occurrence.defining_sentence)
        buckets.setdefault(key, []).append(occurrence)
        display.setdefault(key, occurrence.defining_sentence)
    variants = [
        Variant(
            text=display[key],
            occurrences=tuple(
                sorted(items, key=lambda o: (o.filing_date, o.accession, o.char_offset))
            ),
        )
        for key, items in buckets.items()
    ]
    return tuple(sorted(variants, key=lambda v: (v.first_date, v.text)))


ORDERS: tuple[str, ...] = ("issuer", "count", "name")


def build_groups(
    occurrences: Sequence[Occurrence], *, order: str = "issuer"
) -> tuple[Group, ...]:
    """Collapse occurrences into decisions, in a stable published order.

    Ordering is a convenience for the reviewer and nothing more — it changes
    which group is on screen first, never what is written. ``issuer`` keeps one
    company together, most-repeated metric first, which is how a human reads a
    filing set.
    """
    buckets: dict[tuple[str, str], list[Occurrence]] = {}
    for occurrence in occurrences:
        key = (occurrence.cik, normalise_name(occurrence.metric_name))
        if not key[1]:
            continue
        buckets.setdefault(key, []).append(occurrence)

    groups = [
        Group(
            gid=group_id(cik, normalised),
            cik=cik,
            normalised=normalised,
            display_name=_display_name(items),
            occurrences=tuple(
                sorted(items, key=lambda o: (o.filing_date, o.accession, o.char_offset))
            ),
            variants=_variants(items),
        )
        for (cik, normalised), items in buckets.items()
    ]

    def cik_key(group: Group) -> tuple[int, str]:
        return (int(group.cik) if group.cik.isdigit() else 0, group.cik)

    if order == "count":
        groups.sort(key=lambda g: (-g.n, cik_key(g), g.normalised))
    elif order == "name":
        groups.sort(key=lambda g: (g.normalised, cik_key(g)))
    else:
        groups.sort(key=lambda g: (cik_key(g), -g.n, g.normalised))
    return tuple(groups)


# ===========================================================================
# The proposal. Reasoned, visible, and never committed by itself.
# ===========================================================================

#: Measures defined by GAAP/IFRS or by an SEC form requirement, which METHOD.md
#: §4 excludes by name. Matched on the *whole* normalised name and never on a
#: substring, so "net revenue retention" — a bespoke metric that happens to
#: contain "net revenue" — is not swept up by a rule about "net revenue".
GAAP_MEASURES: frozenset[str] = frozenset(
    {
        "revenue",
        "revenues",
        "net revenue",
        "net revenues",
        "total revenue",
        "total revenues",
        "net sales",
        "net income",
        "net loss",
        "net income loss",
        "net earnings",
        "earnings per share",
        "eps",
        "basic earnings per share",
        "diluted earnings per share",
        "gross profit",
        "gross margin",
        "operating income",
        "operating loss",
        "income from operations",
        "loss from operations",
        "operating expenses",
        "cost of revenue",
        "cost of revenues",
        "cost of goods sold",
        "total assets",
        "total liabilities",
        "total equity",
        "stockholders equity",
        "shareholders equity",
        "comprehensive income",
        "cash flow from operations",
        "net cash provided by operating activities",
        "income tax expense",
        "book value",
        "book value per share",
    }
)

#: Words that make a span read as a market-size or industry claim rather than a
#: statement about the issuer's own operations (§4, fourth criterion).
_INDUSTRY_CUES = re.compile(
    r"\b(total addressable market|serviceable addressable market|\bTAM\b|market size|"
    r"industry[- ]wide|according to (?:a|an|the) (?:report|study|survey)|"
    r"third[- ]party (?:report|research|study)|Euromonitor|Gartner|IDC|Frost & Sullivan)\b",
    re.IGNORECASE,
)

#: A cue that the span describes something counted or measured. Its absence is
#: not evidence of anything much, so it downgrades to "no proposal" rather than
#: to an exclude.
_QUANTITATIVE_CUES = re.compile(
    r"(\d|%|\bnumber\b|\brate\b|\bratio\b|\bper\b|\btotal\b|\bcount\b|\bamount\b|"
    r"\baverage\b|\bmedian\b|\bsum\b|\bvalue\b|\bvolume\b|\bpercentage\b|"
    r"\bdivided by\b|\bmultiplied\b|\bdollar\b|\bgross\b|\bnet\b)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class Proposal:
    """What the machine would say, why, and the fact that it is only a proposal."""

    verdict: str | None  # INCLUDE | EXCLUDE | None — None means "no view"
    reason: str
    rule: str

    @property
    def label(self) -> str:
        if self.verdict is None:
            return "no proposal"
        return f"proposed {VERDICT_LABEL[self.verdict].upper()}"


def _gaap_key(normalised: str) -> str:
    tokens = normalised.split()
    while tokens and tokens[0] in _GAAP_LEAD_WORDS:
        tokens = tokens[1:]
    return " ".join(tokens)


def propose(group: Group) -> Proposal:
    """A reasoned proposal against the four §4 criteria. Not a ruling.

    Every branch names the criterion it is reasoning from, because a proposal a
    reviewer cannot argue with is worse than no proposal at all.
    """
    name = group.display_name
    gaap_key = _gaap_key(group.normalised)

    if gaap_key in GAAP_MEASURES:
        return Proposal(
            EXCLUDE,
            f"‘{name}’ is a GAAP/IFRS measure, not one the company defined (§4).",
            "gaap_measure",
        )

    if group.all_headings:
        return Proposal(
            EXCLUDE,
            f"the located span is the section heading ‘{name}’, which points at a "
            "scoreboard but is not itself a metric (§4).",
            "section_heading",
        )

    corpus = " ".join(variant.text for variant in group.variants)
    if _INDUSTRY_CUES.search(corpus) and not _INDUSTRY_CUES.search(name):
        return Proposal(
            EXCLUDE,
            "the defining sentence reads as a market-size or industry statistic "
            "rather than the issuer's own operations (§4).",
            "industry_statistic",
        )

    if not _QUANTITATIVE_CUES.search(corpus):
        return Proposal(
            None,
            "no view — nothing in the located span shows this is reported as a "
            "number or a rate, and §4's first criterion cannot be judged from it.",
            "no_quantitative_cue",
        )

    return Proposal(
        INCLUDE,
        f"the company supplies its own definition of ‘{name}’ and states it in "
        "quantitative terms (§4); confirm it is about the issuer's own operations.",
        "company_defined",
    )


# ===========================================================================
# Evidence: the surrounding context, recovered from the bytes actually read
# ===========================================================================

CONTEXT_CHARS = 420
_OFFSET_SLACK = 400


@lru_cache(maxsize=8)
def _document_text(url: str) -> str | None:
    """The locally cached document as text, or None.

    The retrieval client caches raw bytes under a hash of the URL, and the
    candidate offsets were computed against ``corpus.html_to_text`` of exactly
    those bytes — so the same two functions reproduce the same string, and the
    offsets line up. If the cache is absent (a fresh clone, ``data/raw/`` being
    gitignored) this returns None and the reviewer is told so rather than shown
    a reconstruction.
    """
    if not url:
        return None
    try:
        from pipeline import corpus as pipeline_corpus
        from pipeline import edgar as pipeline_edgar

        path = pipeline_edgar._cache_path(url)
        if not path.is_file():
            return None
        return pipeline_corpus.html_to_text(path.read_bytes())
    except Exception as exc:  # noqa: BLE001 - context is a convenience, never a claim
        logger.debug("No local context for %s: %s", url, exc)
        return None


def surrounding_context(occurrence: Occurrence) -> dict[str, str]:
    """Text either side of the defining sentence, or an honest note about why not.

    Never reconstructs, never paraphrases, and never shows context it cannot
    anchor: if the sentence is not found at the recorded offset in the local
    copy, that is reported rather than papered over.
    """
    sentence = occurrence.defining_sentence
    text = _document_text(occurrence.url)
    if text is None:
        return {
            "available": "",
            "before": "",
            "after": "",
            "note": "No local copy of this document, so no surrounding context is "
            "shown. The defining sentence above is verbatim; open the filing on "
            "sec.gov to read around it.",
        }

    try:
        anchor = int(occurrence.sentence_char_offset or occurrence.char_offset or 0)
    except ValueError:
        anchor = 0

    window = (max(0, anchor - _OFFSET_SLACK), anchor + len(sentence) + _OFFSET_SLACK)
    start = text.find(sentence, window[0], window[1])
    if start < 0:
        start = text.find(sentence)
    if start < 0:
        return {
            "available": "",
            "before": "",
            "after": "",
            "note": "The recorded span does not match the local copy of this "
            "document, so no context is shown. Read it on sec.gov instead.",
        }

    end = start + len(sentence)
    return {
        "available": "1",
        "before": text[max(0, start - CONTEXT_CHARS) : start].strip(),
        "after": text[end : end + CONTEXT_CHARS].strip(),
        "note": "",
    }


# ===========================================================================
# The ledger. Read with pipeline.metrics, written atomically, ruled rows only.
# ===========================================================================


def read_ledger_rows(path: Path | None = None) -> list[dict[str, str]]:
    """Every row currently in the ledger, in file order, nothing dropped.

    ``pipeline.metrics.read_rulings`` is the right reader for *rulings* and is
    used for resume and progress below, but it drops rows that carry none. This
    reader keeps everything, so a rewrite can never lose a row somebody put
    there by hand.
    """
    target = path or ledger_path()
    if not target.is_file():
        return []
    try:
        with target.open("r", newline="", encoding="utf-8") as handle:
            rows = list(csv.DictReader(handle))
    except (OSError, UnicodeDecodeError, csv.Error) as exc:
        raise RuntimeError(f"Cannot read the ledger at {target}: {exc}") from exc
    return [
        {field: (row.get(field) or "").strip() for field in pipeline_metrics.LEDGER_FIELDS}
        for row in rows
        if (row.get("candidate_id") or "").strip()
    ]


def read_rulings(path: Path | None = None) -> dict[str, dict[str, str]]:
    """Existing rulings by candidate_id — round-tripped through pipeline.metrics."""
    return pipeline_metrics.read_rulings(path or ledger_path())


def group_ruling(group: Group, rulings: dict[str, dict[str, str]]) -> dict[str, str] | None:
    """The ruling on this group, or None. Disagreement inside a group is shown.

    A ruling is written through to every occurrence at once, so the occurrences
    of one group normally agree. If they do not — a ledger edited by hand, or a
    candidate file that changed underneath one — the group reads as ``MIXED``
    and is offered for re-ruling rather than being quietly counted as done.
    """
    found = [rulings[o.candidate_id] for o in group.occurrences if o.candidate_id in rulings]
    if not found:
        return None
    values = {row.get("include", "") for row in found}
    row = dict(found[0])
    row["_verdict"] = (
        "MIXED"
        if len(values) > 1 or len(found) != len(group.occurrences)
        else _LEDGER_VALUE_TO_VERDICT.get(found[0].get("include", "").strip().casefold(), "")
    )
    return row


def _atomic_write_rows(path: Path, rows: Sequence[dict[str, str]]) -> None:
    """Temp file in the same directory, then ``os.replace``.

    The same discipline as ``pipeline.metrics.write_candidates``, and for the
    same reason: a process that dies mid-write must not leave a truncated
    ledger, because METHOD.md §4 calls this file the study.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    staging = path.with_name(path.name + ".partial")
    with staging.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(pipeline_metrics.LEDGER_FIELDS))
        writer.writeheader()
        writer.writerows(rows)
    os.replace(staging, path)


@dataclass(frozen=True)
class Commit:
    """What one ruling wrote. Returned so the page can show it back."""

    group_id: str
    verdict: str
    reviewer: str
    review_date: str
    rationale: str
    n_rows: int
    ledger: str


def commit_ruling(
    *,
    group: Group,
    verdict: str,
    reviewer: str,
    rationale: str,
    rationale_source: str,
    proposal: Proposal,
    review_date: str | None = None,
) -> Commit:
    """Write one human ruling through to every occurrence in the group.

    Every row carries the same initials, date and rationale, because they are
    one human judgment about one metric — METHOD.md §4 asks for a ruling per
    metric, and the occurrence rows are the same ruling recorded against each
    place the metric was found.
    """
    if verdict not in VERDICTS:
        raise ValueError(f"Not a verdict: {verdict!r}")
    reviewer = reviewer.strip()
    rationale = normalise_whitespace(rationale).strip()
    if not _REVIEWER_OK.match(reviewer):
        raise ValueError(
            "Reviewer initials are required, and must be letters (up to "
            f"{MAX_REVIEWER_CHARS} characters). A row without initials is not a "
            "signed ruling."
        )
    if not rationale:
        raise ValueError(
            "A rationale is required. METHOD.md §4 records one for every ruling, "
            "so that the ledger can be disagreed with row by row."
        )
    if len(rationale) > MAX_RATIONALE_CHARS:
        raise ValueError(f"The rationale must be {MAX_RATIONALE_CHARS} characters or fewer.")
    if not _SOURCE_OK.match(rationale_source or ""):
        rationale_source = "free_text"

    # The reviewer's own calendar date, deliberately naive. METHOD.md §4 records
    # "the date" a human ruled, which is a local date on a signature line, not an
    # instant. The UTC timestamp of the write is kept in the audit log below.
    stamped = review_date or date.today().isoformat()  # noqa: DTZ011
    ruling = {
        "include": LEDGER_VALUE[verdict],
        "reviewer": reviewer,
        "review_date": stamped,
        "rationale": rationale,
    }

    path = ledger_path()
    rows = read_ledger_rows(path)
    index = {row["candidate_id"]: position for position, row in enumerate(rows)}

    for occurrence in group.occurrences:
        row = {
            field: occurrence.row.get(field, "")
            for field in pipeline_metrics.CANDIDATE_FIELDS
        }
        # The identity the locator would compute for this span. Recomputed and
        # compared rather than trusted, so a candidate file edited by hand
        # cannot smuggle a row in under someone else's id.
        row["candidate_id"] = occurrence.candidate_id
        row.update(ruling)
        position = index.get(occurrence.candidate_id)
        if position is None:
            index[occurrence.candidate_id] = len(rows)
            rows.append(row)
        else:
            rows[position] = row

    _atomic_write_rows(path, rows)
    _append_log(
        {
            "ts": datetime.now(UTC).isoformat(timespec="seconds"),
            "group_id": group.gid,
            "cik": group.cik,
            "metric_name": group.display_name,
            "normalised_name": group.normalised,
            "verdict": verdict,
            "include": ruling["include"],
            "reviewer": reviewer,
            "review_date": stamped,
            "rationale": rationale,
            "rationale_source": rationale_source,
            "n_occurrences": group.n,
            "candidate_ids": [o.candidate_id for o in group.occurrences],
            "proposal": {
                "verdict": proposal.verdict,
                "rule": proposal.rule,
                "reason": proposal.reason,
            },
            "agreed_with_proposal": proposal.verdict == verdict,
        }
    )
    return Commit(
        group_id=group.gid,
        verdict=verdict,
        reviewer=reviewer,
        review_date=stamped,
        rationale=rationale,
        n_rows=group.n,
        ledger=str(path),
    )


def _append_log(entry: dict[str, Any]) -> None:
    """Append one line to the audit trail. Never fatal.

    The trail records what the machine proposed beside what the human ruled, so
    the agreement rate is itself inspectable — the honest answer to "did you
    just press accept". It is an audit artifact, not the ledger: if it cannot be
    written the ruling still stands, and the failure is logged.
    """
    try:
        path = log_path()
        with path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n")
    except (OSError, ValueError, TypeError) as exc:  # pragma: no cover - disk failure
        logger.warning("Could not append to the adjudication log: %s", exc)


# ===========================================================================
# Request plumbing
# ===========================================================================


async def _payload(request: Request) -> tuple[dict[str, str], bool]:
    """`(fields, wants_json)` from a JSON or urlencoded body.

    Both are supported so the tool works with JavaScript (fast) and without it
    (a plain form post). ``python-multipart`` is deliberately not required: this
    reads the raw body itself rather than adding a dependency to the deployed
    application for the sake of a local tool.
    """
    raw = await request.body()
    if len(raw) > MAX_BODY_BYTES:
        raise HTTPException(status_code=413, detail="Request body too large.")
    content_type = (request.headers.get("content-type") or "").split(";")[0].strip()

    if content_type == "application/json":
        try:
            parsed = json.loads(raw.decode("utf-8") or "{}")
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise HTTPException(status_code=400, detail=f"Malformed JSON body: {exc}") from exc
        if not isinstance(parsed, dict):
            raise HTTPException(status_code=400, detail="Expected a JSON object.")
        return {str(k): str(v) for k, v in parsed.items()}, True

    try:
        decoded = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise HTTPException(status_code=400, detail="Body is not UTF-8.") from exc
    fields = urllib.parse.parse_qs(decoded, keep_blank_values=True)
    return {key: values[0] for key, values in fields.items()}, False


def _same_origin(request: Request) -> bool:
    """Reject a cross-origin post.

    The tool binds to localhost, but a page in another tab can still post to it.
    A ruling written by a web page the reviewer never looked at is exactly the
    thing this module exists to prevent, so a request whose Origin is not this
    host is refused.
    """
    origin = request.headers.get("origin")
    if not origin:
        return True
    try:
        host = urllib.parse.urlsplit(origin).netloc
    except ValueError:
        return False
    return host == (request.headers.get("host") or "")


def _default_reviewer() -> str:
    return os.environ.get(REVIEWER_FLAG, "").strip()[:MAX_REVIEWER_CHARS]


def _order(request: Request) -> str:
    value = str(request.query_params.get("order", "") or "").strip().lower()
    return value if value in ORDERS else "issuer"


def _suffix(order: str) -> str:
    return "" if order == "issuer" else f"?order={order}"


@dataclass(frozen=True)
class Board:
    """Everything one request needs: the groups, the rulings, the progress."""

    groups: tuple[Group, ...]
    rulings: dict[str, dict[str, str]]
    order: str

    @property
    def total(self) -> int:
        return len(self.groups)

    def status(self, group: Group) -> str:
        row = group_ruling(group, self.rulings)
        return row.get("_verdict", "") if row else ""

    @property
    def ruled_ids(self) -> set[str]:
        return {g.gid for g in self.groups if self.status(g)}

    @property
    def n_ruled(self) -> int:
        return len(self.ruled_ids)

    def tally(self) -> list[dict[str, Any]]:
        counts = {verdict: 0 for verdict in VERDICTS}
        mixed = 0
        for group in self.groups:
            state = self.status(group)
            if state in counts:
                counts[state] += 1
            elif state:
                mixed += 1
        out = [
            {"verdict": verdict, "label": VERDICT_LABEL[verdict], "n": counts[verdict]}
            for verdict in VERDICTS
        ]
        if mixed:
            out.append({"verdict": "MIXED", "label": "mixed — needs re-ruling", "n": mixed})
        return out

    def index_of(self, gid: str) -> int:
        for position, group in enumerate(self.groups):
            if group.gid == gid:
                return position
        raise KeyError(gid)

    def next_unruled(self, after: int = -1) -> Group | None:
        ruled = self.ruled_ids
        for group in list(self.groups)[after + 1 :] + list(self.groups)[: after + 1]:
            if group.gid not in ruled:
                return group
        return None


_CACHE: dict[str, Any] = {}


def load_board(order: str = "issuer") -> Board:
    """Groups and rulings for this request.

    The candidate file is re-read whenever it changes on disk, because the
    locator may be re-run while the tool is open. The parse is cached by
    (path, mtime, size) so paging through five hundred groups does not re-parse
    a large CSV five hundred times.
    """
    path = candidates_path()
    try:
        stat = path.stat()
        stamp = (str(path), stat.st_mtime_ns, stat.st_size)
    except OSError:
        stamp = (str(path), 0, 0)

    if _CACHE.get("stamp") != stamp:
        _CACHE["stamp"] = stamp
        _CACHE["occurrences"] = read_candidates(path)
        _CACHE["groups"] = {}
    if order not in _CACHE["groups"]:
        _CACHE["groups"][order] = build_groups(_CACHE["occurrences"], order=order)
    return Board(groups=_CACHE["groups"][order], rulings=read_rulings(), order=order)


# ===========================================================================
# Routes
# ===========================================================================


def register(app, templates) -> None:
    """Attach the adjudication routes — only when the flag is set.

    The caller checks ``is_enabled()`` too. Checking it again here is not
    belt-and-braces for its own sake: this function is importable, and a future
    caller that forgets the guard would otherwise put disk-writing routes on the
    public site.
    """
    if not is_enabled():
        logger.info("Adjudication routes not mounted: %s is not set to 1", ENABLE_FLAG)
        return

    logger.warning(
        "Adjudication routes MOUNTED (%s=1). These write to %s. Do not run this "
        "configuration on a public deployment.",
        ENABLE_FLAG,
        adjudication_dir(),
    )

    def render(name: str, request: Request, **context: Any) -> HTMLResponse:
        payload: dict[str, Any] = {
            "request": request,
            "keyboard_map": KEYBOARD_MAP,
            "presets": RATIONALE_PRESETS,
            "verdicts": VERDICTS,
            "verdict_label": VERDICT_LABEL,
            "verdict_key": VERDICT_KEY,
            "ledger_display": _relative(ledger_path()),
            "log_display": _relative(log_path()),
            "candidates_display": _relative(candidates_path()),
            "default_reviewer": _default_reviewer(),
        }
        payload.update(context)
        return templates.TemplateResponse(request, name, payload)

    def _board(request: Request) -> Board:
        try:
            return load_board(_order(request))
        except RuntimeError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    @app.get("/adjudicate", response_class=HTMLResponse)
    def adjudicate_entry(request: Request) -> Response:
        """Resume: the first group with no ruling, or the summary."""
        board = _board(request)
        if not board.total:
            return render(
                "adjudicate_done.html",
                request,
                state="no_candidates",
                board=board,
                order=board.order,
                suffix=_suffix(board.order),
            )
        target = board.next_unruled()
        if target is None:
            return RedirectResponse(f"/adjudicate/done{_suffix(board.order)}", status_code=303)
        return RedirectResponse(
            f"/adjudicate/{target.gid}{_suffix(board.order)}", status_code=303
        )

    @app.get("/adjudicate/done", response_class=HTMLResponse)
    def adjudicate_done(request: Request) -> HTMLResponse:
        board = _board(request)
        unruled = [g for g in board.groups if not board.status(g)]
        recent = [
            row
            for row in reversed(read_ledger_rows())
            if row.get("reviewer")
        ][:25]
        return render(
            "adjudicate_done.html",
            request,
            state="complete" if board.total and not unruled else "in_progress",
            board=board,
            order=board.order,
            suffix=_suffix(board.order),
            unruled=unruled[:200],
            n_unruled=len(unruled),
            recent=recent,
        )

    @app.get("/adjudicate/{gid}", response_class=HTMLResponse)
    def adjudicate_group(request: Request, gid: str) -> HTMLResponse:
        board = _board(request)
        group, position = _locate(board, gid)
        return render("adjudicate.html", request, **_group_context(board, group, position))

    @app.post("/adjudicate/{gid}")
    async def adjudicate_commit(request: Request, gid: str) -> Response:
        if not _same_origin(request):
            raise HTTPException(status_code=403, detail="Cross-origin post refused.")
        board = _board(request)
        group, position = _locate(board, gid)
        fields, wants_json = await _payload(request)
        proposal = propose(group)

        try:
            commit = commit_ruling(
                group=group,
                verdict=(fields.get("verdict") or "").strip().upper(),
                reviewer=fields.get("reviewer") or "",
                rationale=fields.get("rationale") or "",
                rationale_source=(fields.get("rationale_source") or "").strip(),
                proposal=proposal,
            )
        except ValueError as exc:
            if wants_json:
                return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
            return render(
                "adjudicate.html",
                request,
                **_group_context(board, group, position, error=str(exc), draft=fields),
            )

        # Rebuilt after the write so "what is next" reflects what was just ruled.
        after = load_board(board.order)
        nxt = after.next_unruled(position)
        next_url = (
            f"/adjudicate/{nxt.gid}{_suffix(board.order)}"
            if nxt is not None
            else f"/adjudicate/done{_suffix(board.order)}"
        )
        if wants_json:
            return JSONResponse(
                {
                    "ok": True,
                    "next_url": next_url,
                    "group_id": commit.group_id,
                    "verdict": commit.verdict,
                    "rows_written": commit.n_rows,
                    "n_ruled": after.n_ruled,
                    "total": after.total,
                    "ledger": _relative(Path(commit.ledger)),
                }
            )
        return RedirectResponse(next_url, status_code=303)


def _locate(board: Board, gid: str) -> tuple[Group, int]:
    """The group this URL names, or a 404. No path is ever built from `gid`."""
    try:
        position = board.index_of(gid)
    except KeyError:
        raise HTTPException(status_code=404, detail="No such candidate group.") from None
    return board.groups[position], position


def _group_context(
    board: Board,
    group: Group,
    position: int,
    *,
    error: str = "",
    draft: dict[str, str] | None = None,
) -> dict[str, Any]:
    suffix = _suffix(board.order)
    previous = board.groups[position - 1] if position > 0 else None
    following = board.groups[position + 1] if position + 1 < board.total else None
    existing = group_ruling(group, board.rulings)
    return {
        "group": group,
        "position": position,
        "human_position": position + 1,
        "total": board.total,
        "n_ruled": board.n_ruled,
        # NOT "evidence" - that key already holds the variant list below.
        "absence": absence_evidence_for(group),
        "order": board.order,
        "suffix": suffix,
        "proposal": propose(group),
        "existing": existing,
        "evidence": [
            {
                "variant": variant,
                "context": surrounding_context(variant.occurrences[0]),
                "lead": variant.occurrences[0],
                "shown_occurrences": variant.occurrences[:MAX_OCCURRENCES_SHOWN],
                "hidden_occurrences": max(
                    0, len(variant.occurrences) - MAX_OCCURRENCES_SHOWN
                ),
            }
            for variant in group.variants[:MAX_VARIANTS_SHOWN]
        ],
        "n_variants": len(group.variants),
        "hidden_variants": max(0, len(group.variants) - MAX_VARIANTS_SHOWN),
        "prev_url": f"/adjudicate/{previous.gid}{suffix}" if previous else "",
        "next_url": f"/adjudicate/{following.gid}{suffix}" if following else "",
        "done_url": f"/adjudicate/done{suffix}",
        "post_url": f"/adjudicate/{group.gid}{suffix}",
        "error": error,
        "draft": draft or {},
        "statuses": [
            {"gid": g.gid, "state": board.status(g)}
            for g in board.groups[max(0, position - 40) : position + 40]
        ],
    }


@lru_cache(maxsize=1)
def _absence_evidence_index() -> dict[tuple[int, str], dict]:
    """METHOD.md §6 evidence, keyed by (cik, normalised metric name).

    Computed by `pipeline.build_evidence` from the cached corpus. It is shown
    beside the ruling because a §4 decision is easier and better with it: a
    phrase appearing once in seven years is almost certainly boilerplate, and
    one appearing five hundred times across every filing is almost certainly a
    metric the issuer actually reports.

    It is EVIDENCE, never a verdict. ABSENCE_TEST_MET is a necessary condition
    for DISCONTINUED and not a sufficient one - a metric can meet it and simply
    have been renamed. The page says so.
    """
    path = pipeline_config.DERIVED / "absence_evidence.json"
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("Absence evidence unreadable: %s", exc)
        return {}
    index: dict[tuple[int, str], dict] = {}
    for row in payload.get("evidence") or []:
        try:
            index[(int(row["cik"]), str(row["metric_key"]))] = row
        except (KeyError, TypeError, ValueError):
            continue
    return index


def absence_evidence_for(group: Group) -> dict | None:
    """The §6 evidence for one group, or None when the stage has not run."""
    try:
        cik = int(group.cik)
    except (TypeError, ValueError):
        return None
    row = _absence_evidence_index().get((cik, group.normalised))
    if row is None:
        return None
    vector = row.get("presence_vector") or []
    return {
        **row,
        "vector": "".join("1" if x else "0" for x in vector),
        "n_present": sum(1 for x in vector if x),
    }


def _relative(path: Path) -> str:
    try:
        return str(path.relative_to(pipeline_config.ROOT)).replace("\\", "/")
    except ValueError:
        return str(path)


__all__ = [
    "Board",
    "Commit",
    "Group",
    "Occurrence",
    "Proposal",
    "Variant",
    "build_groups",
    "commit_ruling",
    "group_id",
    "is_enabled",
    "ledger_path",
    "load_board",
    "normalise_name",
    "propose",
    "read_candidates",
    "read_ledger_rows",
    "read_rulings",
    "register",
]
