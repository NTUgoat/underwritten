"""The adverse-event outcome variable, with its two mandatory exclusions.

METHOD.md §7.2 plus Amendment 1. The design rule here is that nothing is ever
dropped quietly: every candidate event is returned, and excluded ones carry the
label and the evidence for why. The published counts come from filtering this
list, so the excluded population is always recoverable and always reportable.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, timedelta

from bs4 import BeautifulSoup

from . import config
from .edgar import EdgarClient
from .filings import Filing

# --- Amendment 1, E1: the April 2021 SPAC warrant restatement wave ---------

SPAC_WARRANT_STATEMENT_DATE = date(2021, 4, 12)

# Phrases that identify a non-reliance filing as caused by the SEC Staff
# Statement rather than by anything issuer-specific. Matched case-insensitively
# against the filing text.
WARRANT_RESTATEMENT_MARKERS = (
    r"staff statement on accounting and reporting considerations for warrants",
    r"warrants issued by special purpose acquisition compan",
    r"april 12,?\s*2021.{0,120}(statement|sec)",
    r"(statement|sec).{0,120}april 12,?\s*2021",
)

# --- Amendment 1, E2: mechanical predecessor delisting at de-SPAC close ----

MECHANICAL_DELISTING_WINDOW = timedelta(days=30)

EXCL_WARRANT = "SECTOR_WIDE_WARRANT_RESTATEMENT"
EXCL_MECHANICAL_DELISTING = "MECHANICAL_DESPAC_DELISTING"


@dataclass(frozen=True)
class AdverseEvent:
    """One candidate adverse event, kept whether or not it counts."""

    filing: Filing
    reason: str
    excluded_as: str | None = None
    exclusion_evidence: str = ""

    @property
    def counts(self) -> bool:
        return self.excluded_as is None


def normalise_whitespace(text: str) -> str:
    """Collapse every kind of whitespace to a single space.

    Not cosmetic. SEC HTML is full of `&nbsp;` (U+00A0), and it appears inside
    dates and phrases - "April&nbsp;12,&nbsp;2021". A pattern written with an
    ordinary space silently fails to match, which would make an exclusion look
    like it was applied when it never fired once. Python's `\\s` covers Unicode
    whitespace for str patterns, so this normalises U+00A0 along with newlines
    introduced by the HTML source.
    """
    return re.sub(r"\s+", " ", text)


def _filing_text(client: EdgarClient, filing: Filing) -> str:
    """Whitespace-normalised text of a filing's primary document.

    Returns '' if the document cannot be read. Callers must treat '' as
    "undetermined", never as "does not match".
    """
    try:
        raw = client.fetch_text(filing.url)
    except Exception:  # noqa: BLE001 - an unreadable filing must not halt a run
        return ""
    try:
        text = BeautifulSoup(raw, "lxml").get_text(" ", strip=True)
    except Exception:  # noqa: BLE001
        text = raw
    return normalise_whitespace(text)


def _warrant_restatement_evidence(text: str) -> str:
    """The matched sentence if this is the SPAC warrant wave, else ''."""
    lowered = text.lower()
    for pattern in WARRANT_RESTATEMENT_MARKERS:
        match = re.search(pattern, lowered, re.IGNORECASE | re.DOTALL)
        if not match:
            continue
        # Return the surrounding sentence from the original casing, as evidence.
        start = max(0, match.start() - 200)
        end = min(len(text), match.end() + 200)
        return " ".join(text[start:end].split())
    return ""


def _despac_completion_dates(filings: list[Filing]) -> list[date]:
    """Filing dates of 8-Ks reporting completion of an acquisition (Item 2.01)."""
    return [
        f.filing_date
        for f in filings
        if f.form.upper().startswith("8-K") and "2.01" in f.items
    ]


def classify(client: EdgarClient, filings: list[Filing]) -> list[AdverseEvent]:
    """Every candidate adverse event for an issuer, with exclusions labelled.

    Requires the *complete* filing index - a truncated one will both miss events
    and break the E2 window test.
    """
    completions = _despac_completion_dates(filings)
    events: list[AdverseEvent] = []

    for filing in filings:
        form = filing.form.upper()

        # -- delisting and late-filing forms -------------------------------
        if form in config.ADVERSE_FORMS:
            reason = config.ADVERSE_FORMS[form]

            if form in ("25", "25-NSE"):
                near = [
                    c
                    for c in completions
                    if abs((c - filing.filing_date).days)
                    <= MECHANICAL_DELISTING_WINDOW.days
                ]
                if near:
                    events.append(
                        AdverseEvent(
                            filing=filing,
                            reason=reason,
                            excluded_as=EXCL_MECHANICAL_DELISTING,
                            exclusion_evidence=(
                                f"Form {form} filed {filing.filing_date}; "
                                f"8-K Item 2.01 completion of acquisition filed "
                                f"{near[0]} ({abs((near[0] - filing.filing_date).days)} "
                                f"days apart)."
                            ),
                        )
                    )
                    continue

            events.append(AdverseEvent(filing=filing, reason=reason))
            continue

        # -- 8-K items 4.01 / 4.02 -----------------------------------------
        if form.startswith("8-K"):
            matched_item = next(
                (
                    item.split()[0]
                    for item in filing.items
                    if item and item.split()[0] in config.ADVERSE_8K_ITEMS
                ),
                None,
            )
            if matched_item is None:
                continue

            reason = config.ADVERSE_8K_ITEMS[matched_item]

            if matched_item == "4.02" and filing.filing_date >= SPAC_WARRANT_STATEMENT_DATE:
                evidence = _warrant_restatement_evidence(_filing_text(client, filing))
                if evidence:
                    events.append(
                        AdverseEvent(
                            filing=filing,
                            reason=reason,
                            excluded_as=EXCL_WARRANT,
                            exclusion_evidence=evidence[:400],
                        )
                    )
                    continue

            events.append(AdverseEvent(filing=filing, reason=reason))

    return events


def counted(events: list[AdverseEvent]) -> list[AdverseEvent]:
    return [e for e in events if e.counts]


def excluded(events: list[AdverseEvent]) -> list[AdverseEvent]:
    return [e for e in events if not e.counts]


def summarise(events: list[AdverseEvent]) -> dict[str, int]:
    """Counts by outcome, for the published exclusion table."""
    summary = {"candidates": len(events), "counted": len(counted(events))}
    for event in excluded(events):
        key = event.excluded_as or "UNKNOWN"
        summary[key] = summary.get(key, 0) + 1
    return summary
