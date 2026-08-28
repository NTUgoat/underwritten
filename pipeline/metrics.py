"""Locating company-defined operating metrics, and the §6 absence test.

Two jobs, and the boundary between them is the whole method.

**Locating (METHOD.md §4) is mechanical and decides nothing.** Pattern search
over definitional constructions - `we define … as`, `we calculate … as`, and the
section headings companies put above their own scoreboards - produces candidate
spans. Every candidate is written to `data/adjudication/metrics_candidates.csv`
with the verbatim defining sentence, the accession number, the form, the filing
date and the character offset, and with the ruling columns (`include`,
`reviewer`, `review_date`, `rationale`) **empty**. A human fills those in. No
code path in this module writes a ruling, and `write_candidates` goes out of its
way to carry existing human rulings forward so that re-running the locator can
never destroy adjudication work.

**The §6 absence test is mechanical too, and it is not a verdict.** It answers
one question - does this phrase appear anywhere in the issuer's filed corpus, in
each reporting period - and reports the per-period presence vector, the first
and last appearance with verbatim context, and whether the phrase is absent for
`config.DISCONTINUATION_PERIODS` consecutive periods. Meeting that test is a
*necessary condition* for the §5 terminal state `DISCONTINUED`; it is not that
state. The terminal state, the rename tracing, and the benign-cause label of
§7.4 are all human rulings recorded in the adjudication ledger.

Where the corpus is too thin to settle the question the answer is
`NOT_DETERMINABLE`, never `ABSENCE_TEST_MET`. METHOD.md §6 and §12 both turn on
that distinction, so every guard here fails in that direction: a coverage gap, a
document that would not parse, a corpus with too few periods, or a phrase that
never appears at all, all yield `NOT_DETERMINABLE`.
"""

from __future__ import annotations

import csv
import hashlib
import logging
import os
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Iterable, Sequence

from . import config
from .corpus import Corpus, Document
from .outcomes import normalise_whitespace

logger = logging.getLogger(__name__)

CANDIDATES_PATH = config.ADJUDICATION / "metrics_candidates.csv"


# ===========================================================================
# Part 1 - candidate location (METHOD.md §4). Mechanical. Decides nothing.
# ===========================================================================


@dataclass(frozen=True)
class Locator:
    """One pattern that finds candidate spans, and what it is looking for."""

    name: str
    kind: str  # "definition" | "heading"
    pattern: re.Pattern[str]
    description: str


@dataclass(frozen=True)
class MetricCandidate:
    """A located candidate, before any human has looked at it.

    Carries everything METHOD.md §4 requires the ledger to record. It carries no
    ruling, and there is no field on it that could hold one.
    """

    candidate_id: str
    cik: int
    accession: str
    form: str
    filing_date: str
    document: str
    doc_type: str
    locator: str
    metric_name: str
    defining_sentence: str
    char_offset: int
    sentence_char_offset: int
    url: str


# The name a definitional construction introduces. Bounded so a malformed
# sentence cannot swallow a page, and stopped at a sentence terminator or a
# semicolon so it cannot run across two sentences. Parentheses and quotes are
# deliberately *allowed*: companies write `we define Gross Booking Value ("GBV")
# as …`, and excluding them would drop the candidate entirely.
_NAME = r"(?P<name>[^.;]{2,120}?)"

# A run of capitalised words - how companies write their own metric names.
# Used where the name sits *before* the trigger phrase, because an unrestricted
# backwards capture would drag in eighty characters of the preceding clause.
# Case-sensitive on purpose; the trigger itself stays case-insensitive via a
# scoped `(?i:...)` group.
_TITLE_PHRASE = (
    r"(?P<name>(?:[A-Z][A-Za-z0-9&/'’\-]*\s+){0,5}[A-Z][A-Za-z0-9&/'’\-]*)"
)

_DEFINITION_LOCATORS: tuple[Locator, ...] = (
    Locator(
        "we_define",
        "definition",
        re.compile(r"\bwe\s+define\s+" + _NAME + r"\s+as\b", re.IGNORECASE),
        "we define X as",
    ),
    Locator(
        "we_calculate",
        "definition",
        re.compile(
            r"\bwe\s+(?:calculate|compute|measure)\s+" + _NAME + r"\s+as\b",
            re.IGNORECASE,
        ),
        "we calculate X as",
    ),
    Locator(
        "issuer_defines",
        "definition",
        re.compile(
            r"\b(?:the\s+)?(?:Company|Group|Partnership|Issuer)\s+"
            r"(?:defines|calculates|computes)\s+" + _NAME + r"\s+as\b",
            re.IGNORECASE,
        ),
        "the Company defines X as",
    ),
    Locator(
        "is_defined_as",
        "definition",
        re.compile(_TITLE_PHRASE + r"(?i:\s+is\s+defined\s+as)\b"),
        "X is defined as",
    ),
    Locator(
        "which_we_define",
        "definition",
        re.compile(
            _TITLE_PHRASE
            + r"(?i:\s*,?\s+which\s+we\s+(?:define|calculate|compute))\b"
        ),
        "X, which we define",
    ),
    Locator(
        "we_refer_to_as",
        "definition",
        re.compile(
            r"\bwe\s+refer\s+to\s+" + _NAME + r"\s+as\b",
            re.IGNORECASE,
        ),
        "we refer to X as",
    ),
)

# Section headings companies put above their own scoreboard. A heading is a
# locator for a *region* of the document, so the candidate it emits carries the
# heading plus the text that follows it, for the reviewer to read.
_HEADING_LOCATORS: tuple[Locator, ...] = (
    Locator(
        "heading_key_operating_metrics",
        "heading",
        re.compile(
            r"\bKey\s+(?:Operating|Business|Performance|Financial)"
            r"(?:\s+and\s+(?:Operating|Financial|Business))?\s+"
            r"(?:Metrics|Indicators|Measures)\b",
            re.IGNORECASE,
        ),
        "Key Operating Metrics / Key Performance Indicators heading",
    ),
    Locator(
        "heading_operating_metrics",
        "heading",
        re.compile(
            r"\b(?:Operating|Business)\s+(?:Metrics|Statistics)\b", re.IGNORECASE
        ),
        "Operating Metrics heading",
    ),
    Locator(
        "heading_key_metrics",
        "heading",
        re.compile(r"\bKey\s+Metrics\b", re.IGNORECASE),
        "Key Metrics heading",
    ),
    Locator(
        "heading_non_gaap",
        "heading",
        re.compile(
            r"\bNon[\s‐-―\-]?GAAP\s+(?:Financial\s+)?"
            r"(?:Measures|Metrics|Information)\b",
            re.IGNORECASE,
        ),
        "Non-GAAP Financial Measures heading",
    ),
)

LOCATORS: tuple[Locator, ...] = _DEFINITION_LOCATORS + _HEADING_LOCATORS

SENTENCE_MAX_CHARS = 1200
HEADING_CONTEXT_CHARS = 600

# --- Table rows under a KPI heading ---------------------------------------
#
# The definition locators find "we define X as" and miss everything presented
# as a TABLE, which is how a great many issuers actually publish their
# scoreboard. Super League's prospectus lists five named metrics - Always On
# Venues, Experiences, Conversion Registered Accounts, Engagement
# Participations, Gameplay Hours - under a heading that says "KPI", with no
# defining sentence anywhere near them. The definition locators found one
# candidate in that entire document.
#
# That asymmetry matters more than it looks: an over-eager candidate costs the
# reviewer one keystroke, while a metric that is never located is invisible in
# the §7.1 denominator forever. So this is deliberately tuned for recall, and
# the §4 human pass is what supplies the precision.
#
# Deterministic on purpose. A language model could read these sections too, but
# a table row label IS the metric name, the extraction is reproducible without
# one, and this study's entire claim is that its inputs are checkable by anyone
# re-running it.
TABLE_SCAN_CHARS = 2500

#: Numeric cells a ONE-WORD row label must be followed by before it is taken
#: as a table row rather than a capitalised word in a sentence.
MIN_ROW_CELLS = 3

#: A row label followed by the first cell of data. The label is 1-6 words,
#: begins with a capital, and is followed by something numeric - a digit, a
#: currency symbol, or the parenthesis of a negative.
TABLE_ROW = re.compile(
    r"(?<![A-Za-z])"
    r"((?:[A-Z][A-Za-z''’/&.-]*)(?:\s+(?:[a-z]{1,4}\s+)?[A-Za-z''’/&.()-]+){0,5})"
    r"\s+(?=[$(]?[\d])"
)

#: Words a table row label does not end on. A row label is a noun phrase -
#: "Gameplay Hours", "Recurring Revenue". A capture ending in a verb or a
#: preposition is a fragment of running prose that happened to precede a
#: number: "basic and diluted earnings per share WERE 1.42", "c corp
#: equivalent net income WAS". Without this the locator produced 40 candidates
#: for one bank, nearly all of them sentence fragments, and precision that bad
#: makes a reviewer stop reading the evidence.
_PROSE_TAIL = frozenset(
    {
        "was", "were", "is", "are", "be", "been", "had", "has", "have",
        "of", "in", "to", "at", "on", "for", "from", "by", "with", "as",
        "and", "or", "the", "a", "an", "than", "that", "which", "we",
        "contributed", "increased", "decreased", "totaled", "totalled",
        "represented", "included", "reflects", "reflected", "compared",
    }
)

#: Row labels that are structure, not metrics.
_TABLE_NOISE = frozenset(
    {
        "table of contents", "year", "years", "years ended", "year ended",
        "three months", "six months", "nine months", "twelve months",
        "as of", "december", "january", "february", "march", "april", "may",
        "june", "july", "august", "september", "october", "november",
        "quarter", "fiscal", "total", "note", "notes", "item", "page",
        "jan", "feb", "mar", "apr", "jun", "jul", "aug", "sep", "sept",
        "oct", "nov", "dec", "q1", "q2", "q3", "q4",
        "change", "increase", "decrease", "period", "periods", "actual",
        "estimated", "company", "peers", "high", "low", "and", "the",
    }
)

# A sentence terminator followed by whitespace. The lookahead reduces splits
# inside abbreviations ("Inc. and", "U.S. GAAP" still split, and that is
# accepted: the offset is exact either way and the reviewer reads the source).
_SENTENCE_BREAK = re.compile(r"[.!?][\"'’”)]?\s")

_QUOTE_STRIP = " \t“”‘’\"'“”‘’,:;-‐–—"


def _sentence_span(text: str, start: int, end: int) -> tuple[int, int]:
    """The span of the sentence containing `text[start:end]`."""
    left = max(0, start - SENTENCE_MAX_CHARS)
    begin = left
    for match in _SENTENCE_BREAK.finditer(text, left, start):
        begin = match.end()

    right = min(len(text), end + SENTENCE_MAX_CHARS)
    finish = right
    forward = _SENTENCE_BREAK.search(text, end, right)
    if forward is not None:
        finish = forward.end() - 1  # keep the terminator, drop the space

    return begin, max(finish, end)


def _heading_span(text: str, start: int) -> tuple[int, int]:
    """The heading plus the text that follows it, cut at a sentence boundary."""
    right = min(len(text), start + HEADING_CONTEXT_CHARS)
    finish = right
    for match in _SENTENCE_BREAK.finditer(text, start, right):
        finish = match.end() - 1
    return start, max(finish, min(len(text), start + 1))


def _collapse_immediate_repeat(name: str) -> str:
    """"Adjusted EBITDA Adjusted EBITDA" -> "Adjusted EBITDA".

    A heading sitting immediately above its own defining sentence makes a
    backwards capture read the name twice. Collapsing an exact doubling is a
    textual tidy, not a judgement: the verbatim sentence is untouched.
    """
    tokens = name.split()
    half = len(tokens) // 2
    if half and len(tokens) % 2 == 0 and tokens[:half] == tokens[half:]:
        return " ".join(tokens[:half])
    return name


_METRIC_KEY_NON_ALNUM = re.compile(r"[^0-9a-z]+")


def metric_key(raw: str) -> str:
    """The canonical identity of a metric name. One definition, used everywhere.

    Lossy on purpose: case, punctuation and whitespace are collapsed so that
    "Non-GAAP Financial Measures" and "non GAAP financial measures" are one
    metric rather than two.

    This lives here rather than in any one consumer because two modules
    independently deciding what "the same metric" means is a defect, not a
    detail. The adjudication tool grouped on punctuation-stripped names while
    the evidence stage keyed on hyphen-preserving ones, so every §6 lookup
    missed silently and the ruling page rendered an evidence panel with every
    field blank.
    """
    return _METRIC_KEY_NON_ALNUM.sub(" ", normalise_whitespace(raw).casefold()).strip()


def clean_metric_name(raw: str) -> str:
    """Whitespace-collapsed, quote-stripped name. The sentence stays verbatim."""
    return _collapse_immediate_repeat(
        normalise_whitespace(raw).strip(_QUOTE_STRIP).strip()
    )


def candidate_id(
    cik: int, accession: str, document: str, locator: str, char_offset: int
) -> str:
    """Stable identifier, so a human ruling survives a re-run of the locator."""
    key = f"{cik}|{accession}|{document}|{locator}|{char_offset}"
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]


def locate_in_document(
    document: Document, *, locators: Sequence[Locator] = LOCATORS
) -> tuple[MetricCandidate, ...]:
    """Every candidate span in one document. Emits; never judges."""
    text = document.text
    found: dict[tuple[str, str, str], MetricCandidate] = {}
    # Heading patterns overlap by construction - "Key Business Metrics" contains
    # "Business Metrics". Two patterns locating the same characters is one
    # candidate, not two, so a heading match wholly inside an accepted one is
    # dropped. Locator order decides which wins, and the specific patterns are
    # listed first. Definition locators are exempt: two different constructions
    # in one sentence really are two things to rule on.
    heading_spans: list[tuple[int, int]] = []

    for locator in locators:
        for match in locator.pattern.finditer(text):
            if locator.kind == "heading":
                span = (match.start(), match.end())
                if any(lo <= span[0] and span[1] <= hi for lo, hi in heading_spans):
                    continue
                heading_spans.append(span)
                begin, finish = _heading_span(text, match.start())
                name = clean_metric_name(match.group(0))
            else:
                begin, finish = _sentence_span(text, match.start(), match.end())
                raw_name = match.groupdict().get("name") or match.group(0)
                name = clean_metric_name(raw_name)

            sentence = text[begin:finish].strip()
            if not name or not sentence:
                continue

            key = (locator.name, name.casefold(), sentence.casefold())
            if key in found:
                continue

            found[key] = MetricCandidate(
                candidate_id=candidate_id(
                    document.cik,
                    document.accession,
                    document.filename,
                    locator.name,
                    match.start(),
                ),
                cik=document.cik,
                accession=document.accession,
                form=document.form,
                filing_date=document.filing_date.isoformat(),
                document=document.filename,
                doc_type=document.doc_type,
                locator=locator.name,
                metric_name=name,
                defining_sentence=sentence,
                char_offset=match.start(),
                sentence_char_offset=begin,
                url=document.url,
            )

    return tuple(
        sorted(found.values(), key=lambda c: (c.char_offset, c.locator))
    )


def locate_table_rows(
    document: Document, *, locators: Sequence[Locator] = _HEADING_LOCATORS
) -> tuple[MetricCandidate, ...]:
    """Candidate metric names taken from table rows beneath a KPI heading.

    Scoped to the region after a heading rather than run over the whole
    document, because "capitalised phrase followed by a number" matches half of
    any financial filing. Inside a KPI table it is a row label; outside one it
    is noise.
    """
    text = document.text
    found: dict[str, MetricCandidate] = {}

    for locator in locators:
        for heading in locator.pattern.finditer(text):
            window_start = heading.end()
            window = text[window_start : window_start + TABLE_SCAN_CHARS]

            for row in TABLE_ROW.finditer(window):
                raw = row.group(1).strip(" .,:;-")
                name = clean_metric_name(raw)
                key = metric_key(name)
                if not key or key in _TABLE_NOISE:
                    continue
                if len(key) > 60:
                    continue

                # Single-word labels are the hard case. Plenty of real metrics
                # are one word - Customers, Subscribers, and Super League's
                # "Experiences" - but so is every capitalised word that happens
                # to precede a number in running prose.
                #
                # The discriminator is the shape of a table row, not the label:
                # a row carries several periods of data, a sentence carries one
                # number. So a one-word label must be followed by at least
                # MIN_ROW_CELLS numeric cells; a phrase needs only one.
                if len(key.split()) < 2:
                    tail = window[row.end() : row.end() + 120]
                    cells = len(re.findall(r"[$(]?\d[\d,.]*%?\)?", tail))
                    if cells < MIN_ROW_CELLS:
                        continue
                words = key.split()
                if any(token in _TABLE_NOISE for token in (words[0], words[-1])):
                    continue
                # A row label is a noun phrase. A prose fragment ends in a verb
                # or a preposition ("... earnings per share WERE"), and one that
                # begins with a preposition is a fragment from the middle of a
                # sentence ("BY their own definition ...").
                if words[-1] in _PROSE_TAIL or words[0] in _PROSE_TAIL:
                    continue
                if len(key) < 4:
                    continue

                offset = window_start + row.start(1)
                begin, end = _sentence_span(text, offset, offset + len(raw))
                candidate = MetricCandidate(
                    candidate_id=candidate_id(
                        document.cik, document.accession, document.filename,
                        "table_row", offset,
                    ),
                    cik=document.cik,
                    accession=document.accession,
                    form=document.form,
                    filing_date=document.filing_date.isoformat(),
                    document=document.filename,
                    doc_type=document.doc_type,
                    locator="table_row",
                    metric_name=name,
                    defining_sentence=text[begin:end].strip(),
                    char_offset=offset,
                    sentence_char_offset=begin,
                    url=document.url,
                )
                found.setdefault(candidate.candidate_id, candidate)

    return tuple(sorted(found.values(), key=lambda c: c.char_offset))


def locate(
    documents: Iterable[Document],
    *,
    locators: Sequence[Locator] = LOCATORS,
    table_documents: Sequence[Document] = (),
) -> tuple[MetricCandidate, ...]:
    """Candidates across many documents, in a stable published order.

    `table_documents` names the subset that also gets table-row extraction -
    the listing document and the first annual report, per METHOD.md §4.
    """
    candidates: list[MetricCandidate] = []
    table_scope = {id(d) for d in (table_documents or ())}
    for document in documents:
        candidates.extend(locate_in_document(document, locators=locators))
        # Table extraction runs ONLY where METHOD.md §4 says candidates are
        # located: "in the listing document and the first annual report". Run
        # over the whole corpus it explodes - every 10-Q carries a non-GAAP
        # heading above a table, and the worklist went from 538 groups to 4,524.
        # It is also the wrong population: a metric introduced in a later 10-Q
        # was not a promise made at listing.
        if id(document) in table_scope:
            candidates.extend(locate_table_rows(document))
    return tuple(
        sorted(
            candidates,
            key=lambda c: (c.cik, c.filing_date, c.accession, c.document, c.char_offset),
        )
    )


# --- The adjudication ledger ----------------------------------------------

CANDIDATE_FIELDS: tuple[str, ...] = (
    "candidate_id",
    "cik",
    "accession",
    "form",
    "filing_date",
    "document",
    "doc_type",
    "locator",
    "metric_name",
    "defining_sentence",
    "char_offset",
    "sentence_char_offset",
    "url",
)

# Filled in by a human. Written empty by this module, always. METHOD.md §4:
# "Every candidate is then ruled on by hand."
RULING_FIELDS: tuple[str, ...] = ("include", "reviewer", "review_date", "rationale")

LEDGER_FIELDS: tuple[str, ...] = CANDIDATE_FIELDS + RULING_FIELDS


def read_rulings(path: Path = CANDIDATES_PATH) -> dict[str, dict[str, str]]:
    """Existing human rulings, keyed by candidate_id. Empty if there are none."""
    if not path.exists():
        return {}
    try:
        with path.open("r", newline="", encoding="utf-8") as handle:
            rows = list(csv.DictReader(handle))
    except (OSError, csv.Error) as exc:
        raise RuntimeError(f"Cannot read the adjudication ledger at {path}: {exc}") from exc

    rulings: dict[str, dict[str, str]] = {}
    for row in rows:
        key = (row.get("candidate_id") or "").strip()
        if not key:
            continue
        ruling = {field: (row.get(field) or "").strip() for field in RULING_FIELDS}
        if any(ruling.values()):
            rulings[key] = {**row, **ruling}
    return rulings


def write_candidates(
    candidates: Sequence[MetricCandidate], path: Path = CANDIDATES_PATH
) -> Path:
    """Write the candidate ledger with the ruling columns empty.

    Two properties this function must have, and is tested for:

    - It never writes a ruling. `include`, `reviewer`, `review_date` and
      `rationale` are emitted blank for every newly located candidate.
    - It never destroys one. Rulings already in the file are carried forward by
      `candidate_id`, and a ruled row whose candidate the locator no longer
      produces is preserved rather than dropped, so a change to a regex can
      never silently discard a human's reading work.
    """
    existing = read_rulings(path)
    written_ids: set[str] = set()
    rows: list[dict[str, str]] = []

    for candidate in candidates:
        carried = existing.get(candidate.candidate_id, {})
        row = {field: str(getattr(candidate, field)) for field in CANDIDATE_FIELDS}
        for field in RULING_FIELDS:
            row[field] = carried.get(field, "")
        rows.append(row)
        written_ids.add(candidate.candidate_id)

    # Orphans are carried forward WHOLE, not narrowed to LEDGER_FIELDS.
    #
    # The §5 pass writes terminal states into the same ledger under columns this
    # module does not know about (state, benign_label, renamed_to, and the rest).
    # Narrowing a row to the columns declared here would silently delete every
    # one of them - a human's reading work, destroyed by a function whose own
    # docstring promises it "never destroys one". Unknown columns are data, and
    # a writer that does not understand a column has no business dropping it.
    orphans = [
        dict(row) for key, row in sorted(existing.items()) if key not in written_ids
    ]
    if orphans:
        logger.warning(
            "%d ruled candidate(s) are no longer produced by the locator and have "
            "been preserved at the end of %s",
            len(orphans),
            path,
        )

    # Written to a sibling file and moved into place. This ledger holds human
    # rulings; a process that dies halfway through a direct write would leave a
    # truncated one, and METHOD.md §4 calls that ledger "the study".
    path.parent.mkdir(parents=True, exist_ok=True)
    staging = path.with_name(path.name + ".partial")
    # The header is LEDGER_FIELDS plus any column already present in the file
    # that this module does not define, in first-seen order. A file that only
    # ever held candidate columns therefore keeps a header byte-identical to
    # LEDGER_FIELDS; a file carrying §5 rulings keeps those columns too.
    extra: list[str] = []
    for row in (*rows, *orphans):
        for field in row:
            if field not in LEDGER_FIELDS and field not in extra:
                extra.append(field)
    fieldnames = [*LEDGER_FIELDS, *extra]

    with staging.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, restval="")
        writer.writeheader()
        writer.writerows(rows)
        writer.writerows(orphans)
    os.replace(staging, path)
    return path


# ===========================================================================
# Part 2 - the §6 absence test. Mechanical. Still not a verdict.
# ===========================================================================

# The mechanical outcomes of the test. ABSENCE_TEST_MET is the *necessary
# condition* for the §5 terminal state DISCONTINUED, not that state - which is a
# human ruling recorded with its rationale, alongside the §7.4 benign-cause
# label. NOT_DETERMINABLE is spelled exactly as METHOD.md §5 spells it because
# it is published under that name.
ABSENCE_TEST_MET = "ABSENCE_TEST_MET"
ABSENCE_TEST_NOT_MET = "ABSENCE_TEST_NOT_MET"
NOT_DETERMINABLE = "NOT_DETERMINABLE"

CONTEXT_CHARS = 180
MAX_RECORDED_APPEARANCES_PER_DOCUMENT = 2

# Whitespace *or* a hyphen may separate two tokens of a phrase. `\s` covers
# U+00A0 for str patterns, which is the whole reason `normalise_whitespace`
# exists - see outcomes.normalise_whitespace and tests/test_outcomes.py.
_SEPARATOR = r"[\s‐-―\-]+"
_TOKEN_SPLIT = re.compile(_SEPARATOR)

# A phrase must be this substantial before the split-tag-tolerant fallback is
# allowed to run, so the loose pattern can never match something short.
_LENIENT_MIN_TOKENS = 2
_LENIENT_MIN_ALPHA = 12


class PhraseError(ValueError):
    """Raised when a phrase cannot be turned into a searchable pattern."""


# The shortest stem a plural rule is allowed to produce. Below these lengths a
# truncation stops being a stem and becomes a different word - "Uses" -> "Us",
# "Cases" -> "Cas" - and matching that word is a false appearance.
_MIN_STEM_AFTER_ONE_CHAR = 3
_MIN_STEM_AFTER_TWO_CHARS = 4

_SIBILANT_PLURALS = ("ses", "xes", "zes", "ches", "shes")


def _surface_forms(token: str) -> set[str]:
    """The word forms a simple singular/plural alternation can reach.

    Only whole words, never a bare truncated stem. Emitting a stem was the
    earlier bug here: `Rates` became `Rat(?:es?)?`, whose optional group made
    the non-word "Rat" independently matchable - and `Uses` became `Us(?:es?)?`,
    which matches the pronoun "us". A false *appearance* suppresses a real
    absence run just as surely as a false absence invents one, so the forms are
    enumerated explicitly and each one has to be a plausible word.
    """
    forms = {token}
    lowered = token.lower()

    if lowered.endswith("ies") and len(token) > 4:
        forms.add(token[:-3] + "y")            # Deliveries -> Delivery
    elif lowered.endswith(_SIBILANT_PLURALS):
        # Ambiguous: "Losses" is Loss+es, "Cases" is Case+s. Take both readings,
        # subject to the stem still being long enough to be a word.
        if len(token) - 2 >= _MIN_STEM_AFTER_TWO_CHARS:
            forms.add(token[:-2])              # Losses -> Loss
        if len(token) - 1 >= _MIN_STEM_AFTER_ONE_CHAR:
            forms.add(token[:-1])              # Cases  -> Case
    elif lowered.endswith("ss"):
        forms.add(token + "es")                # Gross -> Grosses
    elif lowered.endswith("s"):
        if len(token) - 1 >= _MIN_STEM_AFTER_ONE_CHAR:
            forms.add(token[:-1])              # Nights -> Night, Rates -> Rate
    elif lowered.endswith("y") and token[-2].lower() not in "aeiou":
        forms.add(token[:-1] + "ies")          # Delivery -> Deliveries
    else:
        forms.add(token + "s")                 # Night -> Nights
        if lowered.endswith(("x", "z", "ch", "sh")):
            forms.add(token + "es")            # Box -> Boxes

    return forms


def _token_pattern(token: str) -> str:
    """A token, tolerant of the simple singular/plural alternation.

    Deliberately simple - `-s`, `-es`, `-ies` and their singulars. It does not
    attempt irregular plurals: over-reaching here would invent matches, and a
    false appearance is as damaging to the study as a false absence.
    """
    if not token.isalpha() or len(token) < 4:
        return re.escape(token)

    forms = sorted(_surface_forms(token), key=lambda f: (-len(f), f))
    if len(forms) == 1:
        return re.escape(forms[0])
    return "(?:" + "|".join(re.escape(form) for form in forms) + ")"


def tokenise(phrase: str) -> tuple[str, ...]:
    normalised = normalise_whitespace(phrase).strip()
    return tuple(token for token in _TOKEN_SPLIT.split(normalised) if token)


def phrase_pattern(phrase: str, *, plural_tolerant: bool = True) -> re.Pattern[str]:
    """Case-insensitive, whitespace-tolerant, plural-tolerant phrase matcher.

    Whitespace tolerance is not a nicety. The corpus text is normalised, but the
    phrase a reviewer types is not, and SEC HTML puts `&nbsp;`, newlines and
    hyphens between the words of a metric name.
    """
    tokens = tokenise(phrase)
    if not tokens:
        raise PhraseError(f"Not a searchable phrase: {phrase!r}")

    parts = [
        _token_pattern(token) if plural_tolerant else re.escape(token)
        for token in tokens
    ]
    body = _SEPARATOR.join(parts)
    return re.compile(
        r"(?<![0-9A-Za-z])" + body + r"(?![0-9A-Za-z])", re.IGNORECASE
    )


def is_lenient_eligible(phrase: str) -> bool:
    tokens = tokenise(phrase)
    alpha = sum(1 for ch in "".join(tokens) if ch.isalpha())
    return len(tokens) >= _LENIENT_MIN_TOKENS and alpha >= _LENIENT_MIN_ALPHA


def split_tolerant_pattern(phrase: str) -> re.Pattern[str]:
    """The same phrase, tolerant of whitespace *inside* a word.

    Filing agents split words across formatting tags - `<b>N</b>ights` - and
    text extraction with a separator then yields "N ights". This pattern allows
    optional whitespace between the characters of a token. It is only ever tried
    on a document where the strict pattern found nothing, and only for phrases
    long enough that an accidental match is not credible, because its purpose is
    to avoid a false absence rather than to find new matches.
    """
    tokens = tokenise(phrase)
    if not tokens:
        raise PhraseError(f"Not a searchable phrase: {phrase!r}")
    parts = [r"\s*".join(re.escape(ch) for ch in token) for token in tokens]
    body = _SEPARATOR.join(parts)
    return re.compile(
        r"(?<![0-9A-Za-z])" + body + r"(?![0-9A-Za-z])", re.IGNORECASE
    )


# --- Evidence records ------------------------------------------------------


@dataclass(frozen=True)
class Appearance:
    """One place the phrase appears, with enough to re-check it by hand."""

    accession: str
    form: str
    filing_date: date
    document: str
    doc_type: str
    url: str
    char_offset: int
    context: str
    match_mode: str  # "strict" | "split_tolerant"
    matched_text: str


@dataclass(frozen=True)
class PeriodPresence:
    """Whether the phrase appears anywhere in one reporting period."""

    index: int
    label: str
    anchor_form: str
    anchor_accession: str
    start: date
    end: date
    present: bool
    n_documents: int
    n_appearances: int
    n_failed_documents: int


def _appearance_columns(prefix: str, appearance: Appearance | None) -> dict[str, str]:
    """Flat columns for one appearance, blank when there is none."""
    if appearance is None:
        return {f"{prefix}_{k}": "" for k in ("accession", "date", "url", "context")}
    return {
        f"{prefix}_accession": appearance.accession,
        f"{prefix}_date": appearance.filing_date.isoformat(),
        f"{prefix}_url": appearance.url,
        f"{prefix}_context": appearance.context,
    }


@dataclass(frozen=True)
class AbsenceEvidence:
    """The full result of the §6 test. Rich by design - a bare boolean is not
    publishable and cannot be argued with."""

    cik: int
    phrase: str
    aliases: tuple[str, ...]
    status: str
    reason: str
    required_periods: int
    presence: tuple[PeriodPresence, ...]
    first_appearance: Appearance | None
    last_appearance: Appearance | None
    n_appearances: int
    n_documents_searched: int
    n_documents_failed: int
    trailing_absent_periods: int
    max_absent_run: int

    @property
    def presence_vector(self) -> tuple[bool, ...]:
        return tuple(p.present for p in self.presence)

    @property
    def n_periods(self) -> int:
        return len(self.presence)

    def as_row(self) -> dict[str, object]:
        """A flat, publishable summary. Carries no ruling and never will."""
        return {
            "cik": self.cik,
            "phrase": self.phrase,
            "aliases": "; ".join(self.aliases),
            "status": self.status,
            "reason": self.reason,
            "required_periods": self.required_periods,
            "n_periods": self.n_periods,
            "presence_vector": "".join("1" if p else "0" for p in self.presence_vector),
            "trailing_absent_periods": self.trailing_absent_periods,
            "max_absent_run": self.max_absent_run,
            "n_appearances": self.n_appearances,
            "n_documents_searched": self.n_documents_searched,
            "n_documents_failed": self.n_documents_failed,
            **_appearance_columns("first_appearance", self.first_appearance),
            **_appearance_columns("last_appearance", self.last_appearance),
        }


def _context(text: str, start: int, end: int) -> str:
    left = max(0, start - CONTEXT_CHARS)
    right = min(len(text), end + CONTEXT_CHARS)
    return text[left:right].strip()


def find_appearances(
    document: Document,
    patterns: Sequence[re.Pattern[str]],
    lenient_patterns: Sequence[re.Pattern[str]] = (),
) -> tuple[int, tuple[Appearance, ...]]:
    """`(total matches, recorded appearances)` for one document.

    Every match is counted; only the first and last are recorded in full, which
    is all the evidence a reader needs and keeps a 10-K with fifty mentions from
    dominating the output.
    """

    def scan(
        active: Sequence[re.Pattern[str]], mode: str
    ) -> tuple[int, list[Appearance]]:
        spans: list[tuple[int, int]] = []
        for pattern in active:
            spans.extend((m.start(), m.end()) for m in pattern.finditer(document.text))
        if not spans:
            return 0, []
        spans.sort()
        chosen = [spans[0]]
        if len(spans) > 1:
            chosen.append(spans[-1])
        return len(spans), [
            Appearance(
                accession=document.accession,
                form=document.form,
                filing_date=document.filing_date,
                document=document.filename,
                doc_type=document.doc_type,
                url=document.url,
                char_offset=start,
                context=_context(document.text, start, end),
                match_mode=mode,
                matched_text=document.text[start:end],
            )
            for start, end in chosen[:MAX_RECORDED_APPEARANCES_PER_DOCUMENT]
        ]

    count, appearances = scan(patterns, "strict")
    if count == 0 and lenient_patterns:
        count, appearances = scan(lenient_patterns, "split_tolerant")
    return count, tuple(appearances)


def absence_test(
    corpus: Corpus,
    phrase: str,
    *,
    aliases: Sequence[str] = (),
    required_periods: int = config.DISCONTINUATION_PERIODS,
    plural_tolerant: bool = True,
    split_tolerant: bool = True,
) -> AbsenceEvidence:
    """Run METHOD.md §6 for one phrase over one issuer's corpus.

    `aliases` carries the traced renames §6 requires: an appearance of any alias
    is an appearance of the metric, because §6 tests "its defining phrase, and
    every traced rename of it". Tracing a rename is a human ruling; running the
    test over the traced set is not.

    The result is never `ABSENCE_TEST_MET` when the corpus cannot support it.
    Four separate guards return `NOT_DETERMINABLE` instead: no reporting periods,
    no readable documents, a phrase that appears nowhere at all, and any coverage
    gap - an empty period or an unreadable document - inside the run of periods
    where the phrase is absent.
    """
    if required_periods < 1:
        raise ValueError("required_periods must be at least 1")

    all_phrases = (phrase, *aliases)
    patterns = tuple(
        phrase_pattern(p, plural_tolerant=plural_tolerant) for p in all_phrases
    )
    lenient = (
        tuple(split_tolerant_pattern(p) for p in all_phrases if is_lenient_eligible(p))
        if split_tolerant
        else ()
    )

    counts_by_accession: dict[str, int] = {}
    recorded: list[Appearance] = []
    for document in corpus.documents:
        count, appearances = find_appearances(document, patterns, lenient)
        if count:
            counts_by_accession[document.accession] = (
                counts_by_accession.get(document.accession, 0) + count
            )
            recorded.extend(appearances)

    documents_by_accession = corpus.documents_by_accession()
    failures_by_accession = corpus.failures_by_accession()

    presence = tuple(
        PeriodPresence(
            index=period.index,
            label=period.label,
            anchor_form=period.anchor_form,
            anchor_accession=period.anchor_accession,
            start=period.start,
            end=period.end,
            present=any(counts_by_accession.get(a, 0) for a in period.accessions),
            n_documents=sum(
                len(documents_by_accession.get(a, ())) for a in period.accessions
            ),
            n_appearances=sum(counts_by_accession.get(a, 0) for a in period.accessions),
            n_failed_documents=sum(
                len(failures_by_accession.get(a, ())) for a in period.accessions
            ),
        )
        for period in corpus.periods
    )

    ordered = sorted(recorded, key=lambda a: (a.filing_date, a.accession, a.char_offset))
    first = ordered[0] if ordered else None
    last = ordered[-1] if ordered else None

    flags = [p.present for p in presence]
    trailing = 0
    for flag in reversed(flags):
        if flag:
            break
        trailing += 1
    max_run = 0
    run = 0
    for flag in flags:
        run = 0 if flag else run + 1
        max_run = max(max_run, run)

    status, reason = _decide(
        presence=presence,
        corpus=corpus,
        n_appearances=sum(counts_by_accession.values()),
        trailing=trailing,
        required_periods=required_periods,
        phrase=phrase,
    )

    return AbsenceEvidence(
        cik=corpus.cik,
        phrase=phrase,
        aliases=tuple(aliases),
        status=status,
        reason=reason,
        required_periods=required_periods,
        presence=presence,
        first_appearance=first,
        last_appearance=last,
        n_appearances=sum(counts_by_accession.values()),
        n_documents_searched=corpus.n_documents,
        n_documents_failed=corpus.n_failures,
        trailing_absent_periods=trailing,
        max_absent_run=max_run,
    )


def _decide(
    *,
    presence: tuple[PeriodPresence, ...],
    corpus: Corpus,
    n_appearances: int,
    trailing: int,
    required_periods: int,
    phrase: str,
) -> tuple[str, str]:
    """The §6 ruling, and the sentence that explains it.

    Every branch that cannot be settled from the filed record returns
    NOT_DETERMINABLE. None of them returns DISCONTINUED, which is a state only a
    human assigns (METHOD.md §5, §6, §7.4).
    """
    if not presence:
        return (
            NOT_DETERMINABLE,
            (
                "The corpus contains no periodic report, so it defines no "
                "reporting periods and the four-period test cannot be run."
            ),
        )
    if corpus.n_documents == 0:
        return (
            NOT_DETERMINABLE,
            (
                "No document in this issuer's corpus could be read, so absence "
                "cannot be distinguished from a failure to retrieve."
            ),
        )
    if n_appearances == 0:
        return (
            NOT_DETERMINABLE,
            (
                f"The phrase {phrase!r} appears nowhere in the corpus, so there is "
                "no first appearance to measure a disappearance from. A phrase the "
                "issuer never used cannot be one it discontinued."
            ),
        )

    if trailing >= len(presence):
        return (
            NOT_DETERMINABLE,
            (
                "The phrase appears in the corpus but in no document that falls "
                "inside a reporting period, so the corpus and its period index "
                "disagree."
            ),
        )

    absent_run = presence[len(presence) - trailing :] if trailing else ()

    if trailing < required_periods:
        return (
            ABSENCE_TEST_NOT_MET,
            (
                f"Absent for {trailing} consecutive reporting period(s); "
                f"{required_periods} are required. Most recent appearance is in "
                f"period {presence[len(presence) - trailing - 1].label}."
            ),
        )

    empty = [p.label for p in absent_run if p.n_documents == 0]
    if empty:
        return (
            NOT_DETERMINABLE,
            (
                "The phrase is absent from every period searched, but "
                f"{len(empty)} period(s) in that run contain no readable document "
                f"({', '.join(empty[:4])}). Absence from an empty period is "
                "absence of evidence, not evidence of absence."
            ),
        )

    broken = [p.label for p in absent_run if p.n_failed_documents]
    if broken:
        return (
            NOT_DETERMINABLE,
            (
                "The phrase is absent from every period searched, but "
                f"{len(broken)} period(s) in that run contain a document that "
                f"could not be read ({', '.join(broken[:4])}). The metric may "
                "appear in a document this study failed to retrieve."
            ),
        )

    return (
        ABSENCE_TEST_MET,
        (
            f"Absent from every document in the corpus for {trailing} consecutive "
            f"reporting periods ({absent_run[0].label} to {absent_run[-1].label}), "
            f"against the {required_periods} required by METHOD.md §6. This is "
            "the mechanical condition for DISCONTINUED; the terminal state and its "
            "cause remain a human ruling."
        ),
    )
