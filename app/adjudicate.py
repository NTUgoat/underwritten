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

**Two passes over one ledger.** §4 rules on *inclusion*; §5 assigns the
*terminal state* to the metrics §4 included. They are separate readings and
``docs/ADJUDICATION.md`` §5 says so plainly — "Do not rule terminal states in
the same pass as inclusion" — so they are separate routes: ``/adjudicate`` for
the first, ``/adjudicate/state`` for the second. Both write into the same
``metrics.csv``, and **neither may blank the other's columns**: a commit merges
onto the row already on disk rather than rebuilding it, and the writer's header
is the union of what is in the file and what is being written. The §5 columns
are spelled exactly as ``pipeline.analysis.read_ledger`` reads them, because a
mismatch there is not an error — it is a ruling the analysis silently drops.

**The machine proposes nothing at §5.** There is a :func:`propose` for §4,
where four written criteria can be reasoned about mechanically. There is no
equivalent here and there must not be: "what happened to this metric" is the
study's whole finding. What the machine may do is *show* the §6 absence
evidence, which it computed, and *refuse* a ruling §6 forbids.

**The DISCONTINUED guard.** METHOD.md §6 makes the four-period absence test a
**necessary** condition. So ``DISCONTINUED`` cannot be committed unless the §6
status for that metric is ``ABSENCE_TEST_MET``; the refusal names the trailing
absent count and the count required. Meeting the test is still not sufficient —
Airbnb's *Nights and Experiences Booked* scores ``ABSENCE_TEST_MET`` and was
renamed to *Nights and Seats Booked* in an EX-99.1 (``docs/ADJUDICATION.md``) —
so a ``DISCONTINUED`` ruling additionally requires the reviewer to confirm they
looked for a rename and to write one line saying what they checked. That line is
stored in its own column, not folded into the rationale.

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
# METHOD.md §5 — the terminal state. The second pass, over §4's includes.
# ===========================================================================

ALIVE = "ALIVE"
REDEFINED = "REDEFINED"
RENAMED = "RENAMED"
ABSORBED = "ABSORBED"
DISCONTINUED = "DISCONTINUED"
# NOT_DETERMINABLE is the §4 constant above, reused deliberately: METHOD.md
# spells the deferral the same way in both sections, and it is published under
# that name in both.

#: METHOD.md §5, in the order §5 tabulates them. Kept identical to
#: ``pipeline.analysis.TERMINAL_STATES`` — a state this tool can write that the
#: analysis cannot read is a ruling thrown away in silence, so the equality is
#: asserted by a test rather than assumed.
TERMINAL_STATES: tuple[str, ...] = (
    ALIVE,
    REDEFINED,
    RENAMED,
    ABSORBED,
    DISCONTINUED,
    NOT_DETERMINABLE,
)

STATE_LABEL: dict[str, str] = {
    ALIVE: "still reported",
    REDEFINED: "still reported, definition changed",
    RENAMED: "same definition, new label",
    ABSORBED: "subsumed by a broader metric",
    DISCONTINUED: "gone — meets the §6 absence test",
    NOT_DETERMINABLE: "can’t tell from the filed record",
}

#: One letter each, chosen so none collides with the navigation keys the §4
#: page already owns (s skip, b back, r reviewer, t free text, ? help).
STATE_KEY: dict[str, str] = {
    ALIVE: "a",
    REDEFINED: "e",
    RENAMED: "m",
    ABSORBED: "o",
    DISCONTINUED: "d",
    NOT_DETERMINABLE: "n",
}

#: Rationale presets, written by hand against §5 and ``docs/ADJUDICATION.md``.
#: As at §4: selecting one is a human act, and the machine never pre-fills the
#: field. Unlike §4, picking one does **not** commit — a §5 ruling usually needs
#: a date, and often a name or a boolean, so the commit is always a second key.
STATE_RATIONALE_PRESETS: dict[str, tuple[str, ...]] = {
    ALIVE: (
        "Reported in the most recent annual or quarterly report (§5).",
        (
            "Still reported in the most recent earnings release furnished on "
            "8-K/6-K EX-99, which §6 counts as the filed corpus (§5)."
        ),
        "Reported throughout; the definition is unchanged from the listing document (§5).",
    ),
    REDEFINED: (
        "The population counted changed; the reported number is not comparable (§5).",
        "The time window changed; the reported number is not comparable (§5).",
        "The arithmetic changed — a line was added to or removed from the measure (§5).",
        (
            "Reworded only. The calculation is identical and the number would not "
            "change, so the change is cosmetic, not substantive (§5)."
        ),
    ),
    RENAMED: (
        "Same definition under a new label; traced and treated as continuous (§5).",
        (
            "The new label first appears in a furnished EX-99 earnings exhibit, "
            "not in an annual report (§5, §6)."
        ),
    ),
    ABSORBED: (
        (
            "The company states a broader metric now subsumes this one, and gives "
            "the successor by name (§5)."
        ),
    ),
    DISCONTINUED: (
        (
            "Absent from the entire filed corpus for the required consecutive "
            "periods; no rename, no successor, and no disposal disclosed (§6)."
        ),
        (
            "Absent for the required periods; the business the metric measured was "
            "disposed of and the disposal is disclosed (§6, §7.4)."
        ),
    ),
    NOT_DETERMINABLE: (
        "The filed record does not settle what happened to this metric (§5).",
        (
            "A similarly-shaped metric appears around when this one stopped, but "
            "the filings do not state that it is the same measure (§5)."
        ),
        (
            "The corpus boundary bites here: the measure may still be reported "
            "outside the SEC-filed record (§6)."
        ),
        (
            "A coverage gap in the filed corpus covers the run of absent periods, "
            "so absence cannot be distinguished from a retrieval failure (§6, §12)."
        ),
    ),
}

STATE_KEYBOARD_MAP: tuple[tuple[str, str], ...] = (
    ("a", "arm ALIVE"),
    ("e", "arm REDEFINED"),
    ("m", "arm RENAMED"),
    ("o", "arm ABSORBED"),
    ("d", "arm DISCONTINUED — refused unless §6 is met"),
    ("n", "arm NOT_DETERMINABLE"),
    ("1…9", "pick a rationale preset for the armed state (does not commit)"),
    ("Enter", "commit the armed state with everything filled in"),
    ("t", "type a free-text rationale"),
    ("Esc", "disarm / leave the text field"),
    ("s", "skip — write nothing, next metric"),
    ("b", "back to the previous metric"),
    ("r", "edit reviewer initials"),
    ("?", "show or hide this list"),
)

#: METHOD.md §7.4's four named benign causes, plus an explicit "other". These
#: are recorded as *labels* rather than folded into the rationale because §7.4
#: re-runs the primary with them removed, which needs a machine-readable field.
#: The spelling follows the schema documented in ``app/data.py``.
BENIGN_LABELS: tuple[tuple[str, str], ...] = (
    ("BENIGN_SEGMENT_RECLASSIFICATION", "Segment reclassification (ASC 280 / IFRS 8)"),
    ("BENIGN_ACCOUNTING_STANDARD", "Superseded by an accounting standard"),
    ("BENIGN_SEC_COMMENT_LETTER", "SEC comment-letter-driven non-GAAP change"),
    ("BENIGN_BUSINESS_DISPOSAL", "Disposal of the business the metric measured"),
    ("BENIGN_OTHER", "Other benign cause — described below"),
)

BENIGN_CODES: frozenset[str] = frozenset(code for code, _ in BENIGN_LABELS)

#: §7.4 only asks the question of the two states that can hide one.
BENIGN_APPLIES_TO: frozenset[str] = frozenset({DISCONTINUED, REDEFINED})

IMPROVING = "IMPROVING"
DETERIORATING = "DETERIORATING"
UNDETERMINED = "UNDETERMINED"

#: METHOD.md §7.3. Spelled as ``pipeline.analysis.DIRECTIONS`` spells them.
DIRECTIONS: tuple[str, ...] = (IMPROVING, DETERIORATING, UNDETERMINED)

DIRECTION_LABEL: dict[str, str] = {
    IMPROVING: "improving over its final two reported periods",
    DETERIORATING: "deteriorating over its final two reported periods",
    UNDETERMINED: "not determined from the filed record",
}

#: Columns the §5 pass adds to the ledger. Every name here that the analysis
#: reads is spelled exactly as ``pipeline.analysis.read_ledger`` reads it:
#: ``metric_id``, ``state``, ``substantive``, ``direction_at_last_report``,
#: ``state_change_date``, ``last_appearance_date``, ``first_appearance_date``,
#: ``benign``, ``benign_label``, ``benign_detail`` — plus ``absence_periods``,
#: which ``build_metrics_payload`` publishes. The remainder are this pass's own
#: provenance and are carried through in the row's ``raw`` mapping.
#:
#: They are NOT in ``pipeline.metrics.LEDGER_FIELDS``, which is the §4 locator's
#: schema and has no state column at all. They are appended to the header only
#: once something actually writes one, so a ledger carrying §4 rulings alone is
#: byte-identical to what the locator would have written.
STATE_FIELDS: tuple[str, ...] = (
    "metric_id",
    "state",
    "substantive",
    "direction_at_last_report",
    "state_change_date",
    "last_appearance_date",
    "first_appearance_date",
    "benign",
    "benign_label",
    "benign_detail",
    "absence_periods",
    "renamed_to",
    "state_checked",
    "absence_status_at_ruling",
    "state_reviewer",
    "state_review_date",
    "state_rationale",
)

LEDGER_FIELDS_EXTENDED: tuple[str, ...] = pipeline_metrics.LEDGER_FIELDS + STATE_FIELDS

MAX_NAME_CHARS = 200
MAX_DETAIL_CHARS = 400

_TRUE_TOKENS = frozenset({"true", "t", "yes", "y", "1", "on"})
_FALSE_TOKENS = frozenset({"false", "f", "no", "n", "0", "off", ""})

_SLUG_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def _slugify(value: str) -> str:
    """The slug half of a ``metric_id``. Must match ``analysis.slugify``.

    Copied rather than imported so that a local disk-writing tool does not pull
    SciPy into a request, which is what importing ``pipeline.analysis`` costs.
    The equality is pinned by a test, so the copy cannot drift unnoticed.
    """
    slug = _SLUG_NON_ALNUM.sub("-", (value or "").strip().lower()).strip("-")
    return slug or "unnamed"


def _boolean(raw: str) -> bool | None:
    """A submitted boolean, or None when nothing was submitted.

    None is not False. ``substantive`` on a ``REDEFINED`` ruling is a judgment
    the reviewer has to make (METHOD.md §5 distinguishes substantive from
    cosmetic and calls it "a human ruling"), so an unanswered field is refused
    rather than defaulted.
    """
    token = (raw or "").strip().casefold()
    if token in _TRUE_TOKENS:
        return True
    if token in _FALSE_TOKENS:
        return False
    return None


def _iso_or_blank(raw: str, *, field: str) -> str:
    """An ISO date, or "". Anything else is refused rather than stored."""
    token = (raw or "").strip()
    if not token:
        return ""
    try:
        return date.fromisoformat(token).isoformat()
    except ValueError:
        raise ValueError(
            f"{field} must be an ISO date (YYYY-MM-DD) or left empty; got {token!r}."
        ) from None


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
    """Every row currently in the ledger, in file order, **and every column**.

    ``pipeline.metrics.read_rulings`` is the right reader for *rulings* and is
    used for resume and progress below, but it drops rows that carry none. This
    reader keeps everything, so a rewrite can never lose a row somebody put
    there by hand.

    It also keeps every *column*, which is load-bearing rather than tidy. This
    ledger is filled in over two passes: §4 writes ``include``/``reviewer``/
    ``review_date``/``rationale``, §5 writes the terminal state beside them. An
    earlier version of this function narrowed each row to
    ``pipeline.metrics.LEDGER_FIELDS``, so re-reading before a write silently
    deleted every column the other pass had written. Rows are widened to the
    known schema so a caller can rely on those keys, and never narrowed.
    """
    target = path or ledger_path()
    if not target.is_file():
        return []
    try:
        with target.open("r", newline="", encoding="utf-8") as handle:
            rows = list(csv.DictReader(handle))
    except (OSError, UnicodeDecodeError, csv.Error) as exc:
        raise RuntimeError(f"Cannot read the ledger at {target}: {exc}") from exc

    out: list[dict[str, str]] = []
    for row in rows:
        if not (row.get("candidate_id") or "").strip():
            continue
        clean = {field: "" for field in pipeline_metrics.LEDGER_FIELDS}
        for key, value in row.items():
            # `None` is csv's restkey: columns beyond the header, which have no
            # name and therefore no meaning. Dropping them is not data loss.
            if not key:
                continue
            clean[key] = (value or "").strip()
        out.append(clean)
    return out


def ledger_fieldnames(rows: Sequence[dict[str, str]]) -> list[str]:
    """The header to write: the locator's schema, then whatever else is there.

    A ledger holding only §4 rulings keeps exactly
    ``pipeline.metrics.LEDGER_FIELDS`` — the same header the locator writes, so
    nothing changes shape until a §5 ruling actually needs a column. Once one
    does, the §5 columns follow in :data:`STATE_FIELDS` order, and any column a
    human added by hand follows those in sorted order rather than being dropped.
    """
    seen = {key for row in rows for key in row}
    known = list(pipeline_metrics.LEDGER_FIELDS) + [
        field for field in STATE_FIELDS if field in seen
    ]
    extra = sorted(seen - set(known))
    return known + extra


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

    The header is :func:`ledger_fieldnames` of the rows being written, never a
    fixed list. A fixed list is how one pass comes to delete the other's work:
    ``DictWriter`` writes only the columns it was told about, so a header
    narrower than the data is a silent column-wise truncation of a file that is
    supposed to be append-only in spirit.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    staging = path.with_name(path.name + ".partial")
    with staging.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle, fieldnames=ledger_fieldnames(rows), restval=""
        )
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
        position = index.get(occurrence.candidate_id)
        # Merge onto the row already on disk. Whatever the §5 pass wrote there —
        # the terminal state, its date, its benign label — is none of this
        # ruling's business and must survive it. Rebuilding the row from the
        # candidate columns instead, which is what this did once, silently
        # deleted a completed §5 ruling every time a §4 one was revised.
        row = dict(rows[position]) if position is not None else {}
        row.update(
            {
                field: occurrence.row.get(field, "")
                for field in pipeline_metrics.CANDIDATE_FIELDS
            }
        )
        # The identity the locator would compute for this span. Recomputed and
        # compared rather than trusted, so a candidate file edited by hand
        # cannot smuggle a row in under someone else's id.
        row["candidate_id"] = occurrence.candidate_id
        row.update(ruling)
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


# ---------------------------------------------------------------------------
# METHOD.md §5 — the terminal state ruling, and the §6 guard on DISCONTINUED
# ---------------------------------------------------------------------------


def discontinued_block_reason(absence: dict[str, Any] | None) -> str:
    """Why ``DISCONTINUED`` may not be committed here, or "" when §6 permits it.

    METHOD.md §6: *"A metric is DISCONTINUED only when its defining phrase, and
    every traced rename of it, is absent from the issuer's entire filed corpus
    for four consecutive reporting periods."* That is a **necessary** condition,
    so failing it is not a warning — it is a refusal, and the refusal names the
    trailing absent count against the count §6 requires so the reviewer can see
    exactly how far from the test the record is.

    Absent evidence is also a refusal. "The stage has not run" is not "the test
    passed", and a tool that let the second stand in for the first would publish
    a discontinuation on no evidence at all.
    """
    required = pipeline_config.DISCONTINUATION_PERIODS
    if not absence:
        return (
            f"{DISCONTINUED} cannot be committed: METHOD.md §6 requires that the "
            f"phrase be absent from the entire filed corpus for {required} "
            "consecutive reporting periods, and no §6 evidence has been computed "
            "for this metric — the trailing absent count is unknown against the "
            f"{required} required. Run `python -m pipeline.build_evidence` first. "
            f"Where the record does not settle it, the state is {NOT_DETERMINABLE}."
        )
    status = str(absence.get("status") or "").strip() or "not recorded"
    if status == pipeline_metrics.ABSENCE_TEST_MET:
        return ""
    try:
        trailing = int(absence.get("trailing_absent_periods") or 0)
    except (TypeError, ValueError):
        trailing = 0
    try:
        required = int(absence.get("required_periods") or required)
    except (TypeError, ValueError):
        pass
    return (
        f"{DISCONTINUED} cannot be committed: the METHOD.md §6 absence test for "
        f"this metric is {status}, not {pipeline_metrics.ABSENCE_TEST_MET}. The "
        f"phrase is absent for {trailing} trailing reporting period(s) against the "
        f"{required} consecutive periods §6 requires, so the necessary condition "
        f"is not met. Where the filed record does not settle it, §5 says the state "
        f"is {NOT_DETERMINABLE} — which is published, not a failure."
    )


#: Shown beside the confirmation, and quoted in the refusal. This is the exact
#: case ``docs/ADJUDICATION.md`` records as "the single most likely way to
#: publish a false finding", and the guard exists because of it.
RENAME_TRAP = (
    "Airbnb’s “Nights and Experiences Booked” scores ABSENCE_TEST_MET and was "
    "not discontinued — it was renamed to “Nights and Seats Booked”, first "
    "appearing in an EX-99.1 earnings exhibit rather than an annual report. "
    "Meeting the §6 test is necessary, never sufficient."
)


@dataclass(frozen=True)
class StateCommit:
    """What one §5 ruling wrote. Returned so the page can show it back."""

    group_id: str
    state: str
    reviewer: str
    review_date: str
    rationale: str
    n_rows: int
    ledger: str


def commit_state_ruling(
    # Long, and deliberately so: every argument is a column METHOD.md asks the
    # ledger to carry, and collapsing them into a dict would lose the signature
    # as the record of what a §5 ruling consists of.
    *,
    group: Group,
    state: str,
    reviewer: str,
    rationale: str,
    rationale_source: str = "free_text",
    absence: dict[str, Any] | None = None,
    substantive: str = "",
    renamed_to: str = "",
    checked: str = "",
    rename_confirmed: str = "",
    direction: str = "",
    state_change_date: str = "",
    last_appearance_date: str = "",
    first_appearance_date: str = "",
    benign_label: str = "",
    benign_detail: str = "",
    review_date: str | None = None,
) -> StateCommit:
    """Write one human §5 terminal state through to every occurrence row.

    The §4 ruling on those rows is left exactly as it was: this writes only the
    :data:`STATE_FIELDS` columns, onto whatever is already on disk.
    """
    state = (state or "").strip().upper()
    if state not in TERMINAL_STATES:
        raise ValueError(
            f"Not a METHOD.md §5 terminal state: {state!r}. One of "
            f"{', '.join(TERMINAL_STATES)} is required."
        )

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
            "and §5 is a ruling, so the terminal state carries one too."
        )
    if len(rationale) > MAX_RATIONALE_CHARS:
        raise ValueError(f"The rationale must be {MAX_RATIONALE_CHARS} characters or fewer.")
    if not _SOURCE_OK.match(rationale_source or ""):
        rationale_source = "free_text"

    checked = normalise_whitespace(checked).strip()
    renamed_to = normalise_whitespace(renamed_to).strip()
    benign_detail = normalise_whitespace(benign_detail).strip()
    benign_label = (benign_label or "").strip().upper()

    # --- the §6 guard, and it is the point of this pass --------------------
    if state == DISCONTINUED:
        blocked = discontinued_block_reason(absence)
        if blocked:
            raise ValueError(blocked)
        if _boolean(rename_confirmed) is not True:
            raise ValueError(
                "Confirm that you checked for a rename before committing "
                f"{DISCONTINUED}. " + RENAME_TRAP
            )
        if not checked:
            raise ValueError(
                f"A one-line record of what you checked is required for "
                f"{DISCONTINUED}: the most recent earnings release including "
                "EX-99 exhibits, any similarly-shaped metric that appeared when "
                "this one stopped, a disposal of the measured business, and a "
                "segment or accounting-standard change (§6, docs/ADJUDICATION.md)."
            )
        if len(checked) > MAX_DETAIL_CHARS:
            raise ValueError(
                f"What you checked must be {MAX_DETAIL_CHARS} characters or fewer."
            )

    if state == RENAMED and not renamed_to:
        raise ValueError(
            "RENAMED needs the new name. §6 tests the defining phrase *and every "
            "traced rename of it*, so the alias has to be recorded or the test "
            "cannot be re-run over the traced set."
        )
    if len(renamed_to) > MAX_NAME_CHARS:
        raise ValueError(f"The new name must be {MAX_NAME_CHARS} characters or fewer.")

    is_substantive = _boolean(substantive) if substantive.strip() else None
    if state == REDEFINED and is_substantive is None:
        raise ValueError(
            "REDEFINED needs the substantive-or-cosmetic call. METHOD.md §5: "
            "cosmetic rewording, rounding and unit changes are not REDEFINED, and "
            "the distinction is a human ruling — it is never inferred here."
        )

    stamped_change = _iso_or_blank(state_change_date, field="state_change_date")
    stamped_last = _iso_or_blank(last_appearance_date, field="last_appearance_date")
    stamped_first = _iso_or_blank(first_appearance_date, field="first_appearance_date")

    # METHOD.md §7.2 counts adverse filing events *after* the move, so a Mover
    # with no date at all takes no part in the primary. Refusing here is better
    # than writing a ruling the analysis has to drop.
    if (state == DISCONTINUED or (state == REDEFINED and is_substantive)) and not (
        stamped_change or stamped_last
    ):
        raise ValueError(
            "A date is required for this state: METHOD.md §7.2 counts adverse "
            "filing events only after the first discontinuation or redefinition, "
            "so give state_change_date, or last_appearance_date if that is the "
            "only date the filed record settles."
        )

    direction = (direction or "").strip().upper() or UNDETERMINED
    if direction not in DIRECTIONS:
        raise ValueError(
            f"direction_at_last_report must be one of {', '.join(DIRECTIONS)} (§7.3)."
        )

    if benign_label and benign_label not in BENIGN_CODES:
        raise ValueError(
            f"Not a METHOD.md §7.4 benign-cause label: {benign_label!r}."
        )
    if benign_label and state not in BENIGN_APPLIES_TO:
        raise ValueError(
            "A benign-cause label is only recorded against "
            f"{' or '.join(sorted(BENIGN_APPLIES_TO))} (§7.4)."
        )
    if benign_label == "BENIGN_OTHER" and not benign_detail:
        raise ValueError(
            "An 'other' benign cause has to say what it was: §7.4 re-runs the "
            "primary with these removed, and an unnamed label cannot be checked."
        )
    if len(benign_detail) > MAX_DETAIL_CHARS:
        raise ValueError(
            f"The benign-cause detail must be {MAX_DETAIL_CHARS} characters or fewer."
        )

    stamped = review_date or date.today().isoformat()  # noqa: DTZ011
    absence_status = str((absence or {}).get("status") or "")
    absence_periods = str((absence or {}).get("trailing_absent_periods") or "")

    written = {
        # Metric identity, written explicitly. `pipeline.analysis` falls back to
        # "<cik>-<slugified metric_name>", and the occurrence rows of one metric
        # carry the issuer's different spellings of it — so the fallback would
        # split one metric into several and inflate the §7.1 denominator.
        "metric_id": f"{group.cik}-{_slugify(group.normalised)}",
        "state": state,
        # Blank where the question does not arise. `analysis` defaults a blank
        # to TRUE and reads it only for REDEFINED, so this cannot misfire.
        "substantive": ("true" if is_substantive else "false") if state == REDEFINED else "",
        "direction_at_last_report": direction,
        "state_change_date": stamped_change,
        "last_appearance_date": stamped_last,
        "first_appearance_date": stamped_first,
        "benign": "true" if benign_label else "false",
        "benign_label": benign_label,
        "benign_detail": benign_detail,
        "absence_periods": absence_periods,
        "renamed_to": renamed_to,
        "state_checked": checked,
        "absence_status_at_ruling": absence_status,
        "state_reviewer": reviewer,
        "state_review_date": stamped,
        "state_rationale": rationale,
    }

    path = ledger_path()
    rows = read_ledger_rows(path)
    index = {row["candidate_id"]: position for position, row in enumerate(rows)}

    for occurrence in group.occurrences:
        position = index.get(occurrence.candidate_id)
        # Merge, never rebuild: the §4 ruling on this row is not this pass's to
        # touch, and the candidate columns beneath it are the locator's.
        row = dict(rows[position]) if position is not None else {}
        for field in pipeline_metrics.CANDIDATE_FIELDS:
            row.setdefault(field, occurrence.row.get(field, ""))
        row["candidate_id"] = occurrence.candidate_id
        row.update(written)
        if position is None:
            index[occurrence.candidate_id] = len(rows)
            rows.append(row)
        else:
            rows[position] = row

    _atomic_write_rows(path, rows)
    _append_log(
        {
            "ts": datetime.now(UTC).isoformat(timespec="seconds"),
            "pass": "state",
            "group_id": group.gid,
            "cik": group.cik,
            "metric_name": group.display_name,
            "normalised_name": group.normalised,
            "state": state,
            "reviewer": reviewer,
            "review_date": stamped,
            "rationale": rationale,
            "rationale_source": rationale_source,
            "n_occurrences": group.n,
            "candidate_ids": [o.candidate_id for o in group.occurrences],
            # No proposal is recorded because none was made: §5 has no machine
            # proposal, by design. What the machine contributed is the §6
            # evidence the ruling was made against, so that is what is kept.
            "absence_evidence": {
                "status": absence_status or None,
                "trailing_absent_periods": absence_periods or None,
                "required_periods": (absence or {}).get("required_periods"),
                "presence_vector": (absence or {}).get("vector"),
            },
            "fields": {key: value for key, value in written.items() if value},
        }
    )
    return StateCommit(
        group_id=group.gid,
        state=state,
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


# ---------------------------------------------------------------------------
# The §5 board. The same groups, narrowed to the ones §4 let through.
# ---------------------------------------------------------------------------


def group_state(group: Group, rows: dict[str, dict[str, str]]) -> dict[str, str] | None:
    """The §5 ruling on this group, or None. Disagreement inside it is shown.

    The same discipline as :func:`group_ruling`, one section later: a ruling is
    written through to every occurrence at once, so a group whose occurrences
    disagree has been edited by hand or has changed underneath a ruling. It
    reads as ``MIXED`` and is offered again rather than counted as done.
    """
    found = [
        rows[o.candidate_id]
        for o in group.occurrences
        if (rows.get(o.candidate_id) or {}).get("state")
    ]
    if not found:
        return None
    states = {row.get("state", "") for row in found}
    row = dict(found[0])
    row["_state"] = (
        "MIXED"
        if len(states) > 1 or len(found) != len(group.occurrences)
        else found[0].get("state", "")
    )
    return row


@dataclass(frozen=True)
class StateBoard:
    """The §5 pass: §4's includes, and whatever terminal state they carry.

    Composed from a :class:`Board` rather than replacing it, so the grouping,
    the ordering and the §4 rulings are read by exactly one implementation. What
    is different here is the population — only metrics ruled ``INCLUDE`` at §4
    have a terminal state to assign — and the column the progress is counted in.
    """

    board: Board
    groups: tuple[Group, ...]
    rows: dict[str, dict[str, str]]

    @property
    def order(self) -> str:
        return self.board.order

    @property
    def total(self) -> int:
        return len(self.groups)

    @property
    def n_included(self) -> int:
        return len(self.groups)

    @property
    def n_awaiting_inclusion(self) -> int:
        """Groups §4 has not ruled yet, and so cannot reach this pass."""
        return sum(1 for g in self.board.groups if not self.board.status(g))

    def status(self, group: Group) -> str:
        row = group_state(group, self.rows)
        return row.get("_state", "") if row else ""

    @property
    def ruled_ids(self) -> set[str]:
        return {g.gid for g in self.groups if self.status(g)}

    @property
    def n_ruled(self) -> int:
        return len(self.ruled_ids)

    def tally(self) -> list[dict[str, Any]]:
        counts = {state: 0 for state in TERMINAL_STATES}
        mixed = 0
        for group in self.groups:
            state = self.status(group)
            if state in counts:
                counts[state] += 1
            elif state:
                mixed += 1
        out = [
            {"state": state, "label": STATE_LABEL[state], "n": counts[state]}
            for state in TERMINAL_STATES
        ]
        if mixed:
            out.append({"state": "MIXED", "label": "mixed — needs re-ruling", "n": mixed})
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


def load_state_board(order: str = "issuer") -> StateBoard:
    """The §5 board for this request.

    Reads the ledger once for the whole row (the §5 columns live there) and
    reuses :func:`load_board` for the grouping and the §4 verdicts. A group is
    in this pass only when §4 ruled it ``INCLUDE`` outright: an ``EXCLUDE`` has
    no life history to trace, a ``NOT_DETERMINABLE`` at §4 was never established
    as a metric of this study, and a ``MIXED`` §4 ruling has to be settled at §4
    before it can be built on.
    """
    board = load_board(order)
    rows = {
        row["candidate_id"]: row for row in read_ledger_rows() if row.get("candidate_id")
    }
    included = tuple(group for group in board.groups if board.status(group) == INCLUDE)
    return StateBoard(board=board, groups=included, rows=rows)


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
            # METHOD.md §5 vocabulary, available to both templates so the two
            # passes cannot drift apart in what they call the same thing.
            "state_keyboard_map": STATE_KEYBOARD_MAP,
            "state_presets": STATE_RATIONALE_PRESETS,
            "states": TERMINAL_STATES,
            "state_label": STATE_LABEL,
            "state_key": STATE_KEY,
            "benign_labels": BENIGN_LABELS,
            "directions": DIRECTIONS,
            "direction_label": DIRECTION_LABEL,
            "required_periods": pipeline_config.DISCONTINUATION_PERIODS,
            "rename_trap": RENAME_TRAP,
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

    # -- METHOD.md §5, the second pass ------------------------------------
    #
    # Registered BEFORE "/adjudicate/{gid}" on purpose. Starlette matches in
    # registration order, so a literal two-segment path declared after the
    # parametrised one would be swallowed by it and 404 as an unknown group.

    def _state_board(request: Request) -> StateBoard:
        try:
            return load_state_board(_order(request))
        except RuntimeError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    @app.get("/adjudicate/state", response_class=HTMLResponse)
    def adjudicate_state_entry(request: Request) -> Response:
        """Resume the §5 pass: the first included metric with no state."""
        board = _state_board(request)
        if not board.total:
            return render(
                "adjudicate_state_done.html",
                request,
                phase="nothing_included",
                board=board,
                order=board.order,
                suffix=_suffix(board.order),
            )
        target = board.next_unruled()
        if target is None:
            return RedirectResponse(
                f"/adjudicate/state/done{_suffix(board.order)}", status_code=303
            )
        return RedirectResponse(
            f"/adjudicate/state/{target.gid}{_suffix(board.order)}", status_code=303
        )

    @app.get("/adjudicate/state/done", response_class=HTMLResponse)
    def adjudicate_state_done(request: Request) -> HTMLResponse:
        board = _state_board(request)
        unruled = [g for g in board.groups if not board.status(g)]
        recent = [
            row for row in reversed(read_ledger_rows()) if row.get("state_reviewer")
        ][:25]
        return render(
            "adjudicate_state_done.html",
            request,
            phase=(
                "nothing_included"
                if not board.total
                else ("complete" if not unruled else "in_progress")
            ),
            board=board,
            order=board.order,
            suffix=_suffix(board.order),
            unruled=unruled[:200],
            n_unruled=len(unruled),
            recent=recent,
        )

    @app.get("/adjudicate/state/{gid}", response_class=HTMLResponse)
    def adjudicate_state_group(request: Request, gid: str) -> HTMLResponse:
        board = _state_board(request)
        group, position = _locate_state(board, gid)
        return render(
            "adjudicate_state.html", request, **_state_context(board, group, position)
        )

    @app.post("/adjudicate/state/{gid}")
    async def adjudicate_state_commit(request: Request, gid: str) -> Response:
        if not _same_origin(request):
            raise HTTPException(status_code=403, detail="Cross-origin post refused.")
        board = _state_board(request)
        group, position = _locate_state(board, gid)
        fields, wants_json = await _payload(request)

        try:
            commit = commit_state_ruling(
                group=group,
                state=(fields.get("state") or "").strip().upper(),
                reviewer=fields.get("reviewer") or "",
                rationale=fields.get("rationale") or "",
                rationale_source=(fields.get("rationale_source") or "").strip(),
                # Read here, never from the request: the §6 evidence is the
                # machine's own computation and a posted status could otherwise
                # walk straight past the guard.
                absence=absence_evidence_for(group),
                substantive=fields.get("substantive") or "",
                renamed_to=fields.get("renamed_to") or "",
                checked=fields.get("state_checked") or "",
                rename_confirmed=fields.get("rename_confirmed") or "",
                direction=fields.get("direction_at_last_report") or "",
                state_change_date=fields.get("state_change_date") or "",
                last_appearance_date=fields.get("last_appearance_date") or "",
                first_appearance_date=fields.get("first_appearance_date") or "",
                benign_label=fields.get("benign_label") or "",
                benign_detail=fields.get("benign_detail") or "",
            )
        except ValueError as exc:
            if wants_json:
                return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
            return render(
                "adjudicate_state.html",
                request,
                **_state_context(board, group, position, error=str(exc), draft=fields),
            )

        after = load_state_board(board.order)
        nxt = after.next_unruled(position)
        next_url = (
            f"/adjudicate/state/{nxt.gid}{_suffix(board.order)}"
            if nxt is not None
            else f"/adjudicate/state/done{_suffix(board.order)}"
        )
        if wants_json:
            return JSONResponse(
                {
                    "ok": True,
                    "next_url": next_url,
                    "group_id": commit.group_id,
                    "state": commit.state,
                    "rows_written": commit.n_rows,
                    "n_ruled": after.n_ruled,
                    "total": after.total,
                    "ledger": _relative(Path(commit.ledger)),
                }
            )
        return RedirectResponse(next_url, status_code=303)

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


def _locate_state(board: StateBoard, gid: str) -> tuple[Group, int]:
    """The included group this URL names, or a 404.

    A group that exists but was not ruled ``INCLUDE`` at §4 is a 404 here rather
    than a page with the form disabled: §5 applies to included metrics, and
    offering the second ruling on something the first pass excluded would invite
    a state to be recorded for a metric that is not in the study.
    """
    try:
        position = board.index_of(gid)
    except KeyError:
        raise HTTPException(
            status_code=404,
            detail=(
                "No such metric in the §5 pass. Only groups ruled INCLUDE under "
                "METHOD.md §4 are assigned a terminal state."
            ),
        ) from None
    return board.groups[position], position


def _state_context(
    board: StateBoard,
    group: Group,
    position: int,
    *,
    error: str = "",
    draft: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Everything the §5 page renders. It shows evidence and proposes nothing."""
    suffix = _suffix(board.order)
    previous = board.groups[position - 1] if position > 0 else None
    following = board.groups[position + 1] if position + 1 < board.total else None
    absence = absence_evidence_for(group)
    blocked = discontinued_block_reason(absence)
    return {
        "group": group,
        "position": position,
        "human_position": position + 1,
        "total": board.total,
        "n_ruled": board.n_ruled,
        "absence": absence,
        "discontinued_blocked": blocked,
        "order": board.order,
        "suffix": suffix,
        # The §4 ruling this metric got, shown so the §5 reviewer can see what
        # was decided and why before ruling on what happened to it.
        "inclusion": group_ruling(group, board.board.rulings),
        "existing": group_state(group, board.rows),
        "variants": group.variants[:MAX_VARIANTS_SHOWN],
        "n_variants": len(group.variants),
        "hidden_variants": max(0, len(group.variants) - MAX_VARIANTS_SHOWN),
        "prev_url": f"/adjudicate/state/{previous.gid}{suffix}" if previous else "",
        "next_url": f"/adjudicate/state/{following.gid}{suffix}" if following else "",
        "done_url": f"/adjudicate/state/done{suffix}",
        "post_url": f"/adjudicate/state/{group.gid}{suffix}",
        "inclusion_url": f"/adjudicate/{group.gid}{suffix}",
        "error": error,
        "draft": draft or {},
        "statuses": [
            {"gid": g.gid, "state": board.status(g)}
            for g in board.groups[max(0, position - 40) : position + 40]
        ],
    }


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
    "StateBoard",
    "StateCommit",
    "Variant",
    "absence_evidence_for",
    "build_groups",
    "commit_ruling",
    "commit_state_ruling",
    "discontinued_block_reason",
    "group_id",
    "group_ruling",
    "group_state",
    "is_enabled",
    "ledger_fieldnames",
    "ledger_path",
    "load_board",
    "load_state_board",
    "normalise_name",
    "propose",
    "read_candidates",
    "read_ledger_rows",
    "read_rulings",
    "register",
]
