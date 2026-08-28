"""Regression tests for the adverse-event outcome variable.

These run offline. Every fixture is a reduction of a real filing observed on
EDGAR, and the accession numbers are recorded in CHANGELOG.md so each case can
be re-checked against the source.
"""

from __future__ import annotations

from datetime import date

import pytest

from pipeline.filings import Filing
from pipeline.outcomes import (
    EXCL_MECHANICAL_DELISTING,
    EXCL_WARRANT,
    AdverseEvent,
    _despac_completion_dates,
    _warrant_restatement_evidence,
    counted,
    excluded,
    normalise_whitespace,
    summarise,
)


def make_filing(
    form: str,
    filing_date: date,
    items: tuple[str, ...] = (),
    accession: str = "0000000000-00-000000",
) -> Filing:
    return Filing(
        cik=1,
        accession=accession,
        form=form,
        filing_date=filing_date,
        report_date=None,
        primary_document="doc.htm",
        items=items,
        size=0,
        is_xbrl=False,
    )


# -- whitespace normalisation ----------------------------------------------
# The bug this guards against: SEC HTML writes dates with &nbsp; (U+00A0), so a
# pattern containing an ordinary space never matches and the exclusion silently
# never fires. That failure is invisible - it looks like "no warrant filings
# found" rather than like an error.


def test_nbsp_between_date_parts_is_normalised():
    raw = "On April\xa012,\xa02021, the SEC issued a statement"
    assert "april 12" in normalise_whitespace(raw).lower()


@pytest.mark.parametrize(
    "raw",
    [
        "April\xa012,\xa02021",       # non-breaking spaces
        "April 12,\n2021",            # newline from the HTML source
        "April  12,   2021",          # runs of ordinary spaces
        "April\t12,\r\n2021",         # tabs and CRLF
    ],
)
def test_whitespace_variants_all_normalise_to_one_form(raw):
    assert normalise_whitespace(raw) == "April 12, 2021"


def test_normalisation_does_not_join_separate_words():
    assert normalise_whitespace("warrants  issued") == "warrants issued"


# -- E1: the April 2021 SPAC warrant restatement wave ----------------------


def test_detects_sec_staff_statement_by_title():
    text = normalise_whitespace(
        "The Company concluded that its financial statements should no longer be "
        "relied upon. Staff Statement on Accounting and Reporting Considerations "
        "for Warrants Issued by Special Purpose Acquisition Companies."
    )
    assert _warrant_restatement_evidence(text)


def test_detects_lordstown_phrasing_with_nbsp():
    """Reduction of accession 0001104659-21-064530, which used &nbsp; in the date."""
    text = normalise_whitespace(
        "Non-Reliance on Previously Issued Financial Statements. On April\xa012, "
        "\xa02021, the SEC issued a statement (the “Statement”) discussing "
        "the accounting implications of certain terms that are common in warrants "
        "issued by special purpose acquisition companies."
    )
    assert _warrant_restatement_evidence(text)


def test_ordinary_restatement_is_not_excluded():
    """An issuer-specific restatement must survive: it is the real signal."""
    text = normalise_whitespace(
        "The Audit Committee concluded that the previously issued financial "
        "statements for fiscal 2022 should no longer be relied upon because of "
        "errors in revenue recognition relating to distributor arrangements."
    )
    assert not _warrant_restatement_evidence(text)


def test_unrelated_mention_of_warrants_is_not_enough():
    """Merely having warrants outstanding is not the SEC statement."""
    text = normalise_whitespace(
        "The Company has warrants outstanding that are exercisable at $11.50 per "
        "share. The restatement relates to inventory obsolescence."
    )
    assert not _warrant_restatement_evidence(text)


# -- E2: mechanical predecessor delisting at de-SPAC close ------------------


def test_completion_dates_read_item_201():
    filings = [
        make_filing("8-K", date(2020, 6, 8), items=("2.01", "9.01")),
        make_filing("8-K", date(2020, 7, 1), items=("2.02",)),
    ]
    assert _despac_completion_dates(filings) == [date(2020, 6, 8)]


def test_form_25_before_completion_is_within_window():
    """Nikola: Form 25-NSE 2020-06-03, completion 8-K 2020-06-08, 5 days apart."""
    completion = date(2020, 6, 8)
    form25 = date(2020, 6, 3)
    assert abs((completion - form25).days) <= 30


def test_later_genuine_delisting_is_outside_the_window():
    """Nikola's 2025 delisting is real and must still count."""
    completion = date(2020, 6, 8)
    form25 = date(2025, 4, 3)
    assert abs((completion - form25).days) > 30


# -- accounting ------------------------------------------------------------
# Excluded events are never dropped, so the counts always reconcile.


def test_summary_reconciles():
    events = [
        AdverseEvent(make_filing("NT 10-K", date(2025, 3, 31)), "Late annual report"),
        AdverseEvent(
            make_filing("25-NSE", date(2020, 6, 3)),
            "Delisting",
            excluded_as=EXCL_MECHANICAL_DELISTING,
            exclusion_evidence="5 days apart",
        ),
        AdverseEvent(
            make_filing("8-K", date(2021, 5, 4), items=("4.02",)),
            "Non-reliance",
            excluded_as=EXCL_WARRANT,
            exclusion_evidence="April 12, 2021 statement",
        ),
    ]
    summary = summarise(events)

    assert summary["candidates"] == 3
    assert summary["counted"] == 1
    assert summary[EXCL_MECHANICAL_DELISTING] == 1
    assert summary[EXCL_WARRANT] == 1
    assert len(counted(events)) + len(excluded(events)) == len(events)


def test_excluded_events_retain_their_evidence():
    """An exclusion without evidence is unpublishable, so it must be carried."""
    event = AdverseEvent(
        make_filing("25-NSE", date(2020, 6, 3)),
        "Delisting",
        excluded_as=EXCL_MECHANICAL_DELISTING,
        exclusion_evidence="Form 25-NSE filed 2020-06-03; completion 2020-06-08",
    )
    assert not event.counts
    assert "2020-06-08" in event.exclusion_evidence
