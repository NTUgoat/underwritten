"""Underwritten - the FastAPI application.

Server-rendered Jinja2, no build step, no runtime CDN dependency beyond the two
Google font faces. The app reads the committed outputs of the pipeline and
renders them. It computes nothing, and where a file is missing it says so in
plain words rather than showing a number that is not there.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from jinja2 import ChainableUndefined, Undefined
from jinja2.exceptions import UndefinedError

from . import data, views

BASE_DIR = Path(__file__).resolve().parent

#: Facts about the publication itself, not about any company. Safe to hardcode:
#: they are properties of this repository, and every one of them is checkable
#: against METHOD.md.
SITE: dict[str, Any] = {
    "name": "Underwritten",
    "tagline": "Grading listing-document promises against the SEC-filed record",
    #: The same sentence with no jargon in it. A reader who knows what a
    #: listing document is does not need this; a reader who does not know
    #: cannot use the site without it, and that reader arrives first.
    "plain": (
        "When a company goes public it invents its own performance measures. "
        "This study checks, filing by filing, whether it still reports them."
    ),
    "question": (
        "Do companies keep reporting the performance measures "
        "they invented for themselves when they listed?"
    ),
    "author": "Jex Lin",
    "preregistered": "28 August 2026",
    "preregistration_tag": "preregistration-v1",
    "listing_window": "2019–2021",
    "corpus": "SEC EDGAR only",
}

#: Two tiers, because they answer different questions. The first is what the
#: study found; the second is why anyone should believe it. Flattening them into
#: one row of eight nouns is what made the site unreadable to a first-time
#: visitor: every destination looked equally likely to be the place to start.
NAV: tuple[dict[str, str], ...] = (
    {"href": "/", "label": "Finding", "id": "finding", "tier": "study",
     "blurb": "The base rate and the one confirmatory test"},
    {"href": "/note", "label": "Note", "id": "note", "tier": "study",
     "blurb": "The written argument, signed and dated"},
    {"href": "/cohort", "label": "Ledger", "id": "cohort", "tier": "study",
     "blurb": "Every metric and the ruling made on it"},
    {"href": "/positions", "label": "Positions", "id": "positions", "tier": "study",
     "blurb": "Three live names, priced as a spread over a hurdle"},
    {"href": "/resolved", "label": "Resolved", "id": "resolved", "tier": "study",
     "blurb": "Cases the public record has already settled"},
    {"href": "/method", "label": "Method", "id": "method", "tier": "receipts",
     "blurb": "The pre-registration in full"},
    {"href": "/provenance", "label": "Provenance", "id": "provenance", "tier": "receipts",
     "blurb": "A SHA-256 for every document read"},
    {"href": "/changelog", "label": "Changelog", "id": "changelog", "tier": "receipts",
     "blurb": "Every amendment, dated and reasoned"},
)


#: One real candidate, carried through the instrument on the front page.
#:
#: A first-time reader cannot picture "a company-defined operating metric" or
#: "the four-period absence test" from the definitions alone, and the site was
#: unusable to them for exactly that reason. One worked example does what a
#: paragraph of method cannot.
#:
#: It is a candidate, not a ruling. Nothing here asserts a terminal state: the
#: quote is a verbatim sentence from a filing and the counts are occurrences the
#: locator found, both of which are facts about documents (METHOD.md 8.2). The
#: page says so where it shows them.
#:
#: Every field is checked against data/adjudication/metrics_candidates.csv by
#: tests/test_specimen.py, so it cannot drift away from the corpus silently.
SPECIMEN: dict[str, Any] = {
    "candidate_id": "2f506563fd002a7a",
    "issuer": "Lemonade, Inc.",
    "ticker": "LMND",
    "cik": 1691421,
    "metric": 'in force premium ("IFP")',
    #: The phrase the occurrence counts below were taken on. The issuer writes
    #: the measure both with and without its parenthetical abbreviation, so the
    #: count is on the name itself; naming it here keeps the figure on the page
    #: and the figure the test checks the same figure.
    "phrase": "in force premium",
    "quote": (
        'We define in force premium ("IFP") as the aggregate annualized premium '
        "for Customers as of the period end date."
    ),
    "form": "S-1",
    "filed": "2020-06-08",
    "accession": "0001047469-20-003416",
    "url": (
        "https://www.sec.gov/Archives/edgar/data/1691421/"
        "000104746920003416/a2241721zs-1.htm"
    ),
    "documents": 109,
    "periods": 25,
    "occurrences": 59,
    "distinct_documents": 46,
    #: How many of those occurrences a study reading only annual reports would
    #: have seen. Named explicitly rather than derived in the template, so the
    #: figure on the page is the figure the test checks.
    "annual_report_occurrences": 6,
    #: The whole argument for the corpus boundary, in four rows: most of the
    #: times this issuer reported its own measure, it did so in an exhibit
    #: furnished with an 8-K. A study that read annual reports would have found
    #: six of these and called the rest absence.
    "where": (
        {"label": "8-K earnings releases (EX-99.1)", "n": 33, "note": "furnished, not filed"},
        {"label": "Quarterly reports (10-Q)", "n": 19, "note": ""},
        {"label": "Annual reports (10-K)", "n": 6, "note": ""},
        {"label": "The listing document (S-1)", "n": 1, "note": "where it was promised"},
    ),
}


def _as_at() -> str:
    """The study as-at date, taken from the pipeline so there is one source."""
    try:
        from pipeline import config as pipeline_config

        return str(pipeline_config.AS_AT_DATE)
    except Exception:  # noqa: BLE001 - the site must render without the pipeline
        return "2026-08-28"


# --------------------------------------------------------------------------
# template filters - every one of them refuses to invent a value
# --------------------------------------------------------------------------

#: A missing key reaches a filter as jinja2.Undefined, whose __int__ and
#: __float__ raise UndefinedError rather than TypeError. Coercing it inside a
#: try/except that catches only TypeError/ValueError is exactly how an absent
#: figure turns into a 500 instead of a visible pending mark, so every numeric
#: filter goes through _number() and nothing coerces directly.
_MISSING = (Undefined, type(None))


def _number(value: Any) -> float | None:
    """The value as a float, or None if it is absent or not a number."""
    if isinstance(value, _MISSING):
        return None
    if isinstance(value, str) and not value.strip():
        return None
    try:
        return float(value)
    except (TypeError, ValueError, UndefinedError):
        return None


def _pct(value: Any, places: int = 1) -> str:
    """A share in [0, 1] as a percentage. A missing share stays missing."""
    number = _number(value)
    return data.PENDING if number is None else f"{number * 100:.{places}f}%"


def _pp(value: Any, places: int = 1) -> str:
    """An already-in-percentage-points figure, with an explicit sign."""
    number = _number(value)
    return data.PENDING if number is None else f"{number:+.{places}f} pp"


def _num(value: Any) -> str:
    number = _number(value)
    return data.PENDING if number is None else f"{int(number):,}"


def _bps(value: Any) -> str:
    number = _number(value)
    return data.PENDING if number is None else f"{round(number):,} bps"


def _plain(value: Any) -> str:
    """A string that may be absent. Absence is shown, never smoothed over."""
    if isinstance(value, _MISSING):
        return data.PENDING
    return str(value).strip() or data.PENDING


def _state_label(value: Any) -> str:
    if isinstance(value, _MISSING):
        return data.PENDING
    return str(value).replace("_", " ").lower() or data.PENDING


def _state_class(value: Any) -> str:
    if isinstance(value, _MISSING) or not str(value).strip():
        return "s-unknown"
    return "s-" + str(value).lower().replace("_", "-")


def _bytes(value: Any) -> str:
    size = _number(value)
    if size is None:
        return data.PENDING
    for unit in ("B", "kB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return data.PENDING


_MONTHS = (
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
)


def _longdate(value: Any) -> str:
    """An ISO filing date as a reader says it. Anything else passes through.

    Filing dates are facts about documents and are never pending: if a date is
    absent the caller has no date, and printing the raw value is more honest
    than printing a month that was guessed.
    """
    if isinstance(value, _MISSING):
        return data.PENDING
    text = str(value).strip()
    parts = text.split("-")
    if len(parts) != 3:
        return text or data.PENDING
    try:
        year, month, day = int(parts[0]), int(parts[1]), int(parts[2])
    except ValueError:
        return text
    if not 1 <= month <= 12:
        return text
    return f"{day} {_MONTHS[month - 1]} {year}"


def _short_hash(value: Any) -> str:
    if isinstance(value, _MISSING):
        return data.PENDING
    text = str(value).strip()
    if len(text) < 16:
        return text or data.PENDING
    return f"{text[:12]}…{text[-8:]}"


def create_app() -> FastAPI:
    app = FastAPI(
        title=SITE["name"],
        description=SITE["tagline"],
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )

    app.mount(
        "/static",
        StaticFiles(directory=str(BASE_DIR / "static")),
        name="static",
    )

    templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
    env = templates.env
    env.trim_blocks = True
    env.lstrip_blocks = True
    # A half-written data file must not 500 the page. A missing key renders as an
    # empty string and every numeric filter turns it into the pending marker, so
    # absence stays visible instead of becoming an outage.
    env.undefined = ChainableUndefined

    env.globals.update(
        site=SITE,
        nav=NAV,
        specimen=SPECIMEN,
        as_at=_as_at(),
        PENDING=data.PENDING,
        NOT_YET_RUN=data.NOT_YET_RUN,
        CELL_STATES=data.CELL_STATES,
        CELL_LEGEND=data.CELL_LEGEND,
        METRIC_STATES=data.METRIC_STATES,
        sec_url=data.sec_url,
        citation_is_complete=data.citation_is_complete,
    )
    env.filters.update(
        pct=_pct,
        pp=_pp,
        num=_num,
        bps=_bps,
        plain=_plain,
        state_label=_state_label,
        state_class=_state_class,
        filesize=_bytes,
        short_hash=_short_hash,
        longdate=_longdate,
        slug=views.slugify,
    )

    views.register(app, templates)

    # The local adjudication tool writes rulings to disk, so it is mounted only
    # when UNDERWRITTEN_ADJUDICATE=1 is set in the environment. Default off, and
    # adjudicate.register() checks the same flag again before attaching a route,
    # so the deployed public site never carries a write path.
    from . import adjudicate

    if adjudicate.is_enabled():
        adjudicate.register(app, templates)

    return app


app = create_app()
