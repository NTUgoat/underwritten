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
    "author": "Jex Lin",
    "preregistered": "28 August 2026",
    "preregistration_tag": "preregistration-v1",
    "listing_window": "2019–2021",
    "corpus": "SEC EDGAR only",
}

NAV: tuple[dict[str, str], ...] = (
    {"href": "/", "label": "Finding", "id": "finding"},
    {"href": "/note", "label": "Note", "id": "note"},
    {"href": "/cohort", "label": "Ledger", "id": "cohort"},
    {"href": "/positions", "label": "Positions", "id": "positions"},
    {"href": "/resolved", "label": "Resolved", "id": "resolved"},
    {"href": "/method", "label": "Method", "id": "method"},
    {"href": "/provenance", "label": "Provenance", "id": "provenance"},
    {"href": "/changelog", "label": "Changelog", "id": "changelog"},
)


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
        slug=views.slugify,
    )

    views.register(app, templates)
    return app


app = create_app()
