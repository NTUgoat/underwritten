"""The complete filing index for an issuer.

The submissions endpoint returns only the most recent 1,000 filings inline and
pushes the rest into overflow files. Reading only the inline block silently
truncates the corpus - and a truncated corpus manufactures false DISCONTINUED
verdicts, which is the exact failure METHOD.md §6 exists to prevent. So this
module always follows the overflow files, and `complete_index` is the only
supported way to obtain an issuer's filings.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any, Iterable, Sequence

from . import config
from .edgar import EdgarClient, document_url, filing_index_url


@dataclass(frozen=True)
class Filing:
    """One filing. Immutable; every field comes straight from EDGAR."""

    cik: int
    accession: str
    form: str
    filing_date: date
    report_date: date | None
    primary_document: str
    items: tuple[str, ...]
    size: int
    is_xbrl: bool

    @property
    def url(self) -> str:
        return document_url(self.cik, self.accession, self.primary_document)

    @property
    def index_url(self) -> str:
        return filing_index_url(self.cik, self.accession)


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _rows_from_block(cik: int, block: dict[str, Any]) -> list[Filing]:
    """Convert one columnar submissions block into Filing records."""
    forms: Sequence[str] = block.get("form", [])
    n = len(forms)
    if n == 0:
        return []

    def col(name: str) -> Sequence[Any]:
        values = block.get(name, [])
        # EDGAR occasionally omits a trailing column; pad rather than crash.
        if len(values) < n:
            return list(values) + [None] * (n - len(values))
        return values

    accessions = col("accessionNumber")
    filing_dates = col("filingDate")
    report_dates = col("reportDate")
    primary_docs = col("primaryDocument")
    items_col = col("items")
    sizes = col("size")
    xbrl_col = col("isXBRL")

    out: list[Filing] = []
    for i in range(n):
        filed = _parse_date(filing_dates[i])
        if filed is None:
            continue  # a filing with no usable date cannot be placed in time
        raw_items = items_col[i] or ""
        out.append(
            Filing(
                cik=cik,
                accession=accessions[i] or "",
                form=(forms[i] or "").strip(),
                filing_date=filed,
                report_date=_parse_date(report_dates[i]),
                primary_document=primary_docs[i] or "",
                items=tuple(s.strip() for s in raw_items.split(",") if s.strip()),
                size=int(sizes[i] or 0),
                is_xbrl=bool(xbrl_col[i]),
            )
        )
    return out


def complete_index(client: EdgarClient, cik: int | str) -> list[Filing]:
    """Every filing EDGAR holds for this issuer, oldest first.

    Follows the overflow files. Never returns a truncated view.
    """
    cik_int = int(str(cik).lstrip("0") or "0")
    submissions = client.submissions(cik_int)

    filings = _rows_from_block(cik_int, submissions.get("filings", {}).get("recent", {}))

    for overflow in submissions.get("filings", {}).get("files", []):
        name = overflow.get("name")
        if not name:
            continue
        block = client.fetch_json(f"{config.SEC_DATA}/submissions/{name}")
        # Overflow files are already a bare columnar block.
        filings.extend(_rows_from_block(cik_int, block))

    filings.sort(key=lambda f: (f.filing_date, f.accession))
    return filings


# -- pure selectors ---------------------------------------------------------
# Each returns a new list. Nothing mutates the index it is given.


def of_forms(filings: Iterable[Filing], forms: Sequence[str]) -> list[Filing]:
    wanted = {f.upper() for f in forms}
    return [f for f in filings if f.form.upper() in wanted]


def between(filings: Iterable[Filing], start: date, end: date) -> list[Filing]:
    return [f for f in filings if start <= f.filing_date <= end]


def after(filings: Iterable[Filing], when: date) -> list[Filing]:
    return [f for f in filings if f.filing_date > when]


def annual_reports(filings: Iterable[Filing]) -> list[Filing]:
    return of_forms(filings, config.ANNUAL_FORMS)


def corpus(filings: Iterable[Filing]) -> list[Filing]:
    """Every form the §6 absence test must search."""
    return of_forms(filings, config.CORPUS_FORMS)


# Adverse-event classification lives in outcomes.py, because it needs filing
# text (to identify the April 2021 SPAC warrant restatement wave) and therefore
# an EdgarClient. This module stays a pure view over the filing index.
