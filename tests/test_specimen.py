"""The front page's worked example must stay true to the committed corpus.

`app.main.SPECIMEN` is the one place on the site where a filing is quoted from a
constant rather than read out of a data file at request time. That is deliberate
— `data/adjudication/metrics_candidates.csv` is fifteen megabytes and has no
business being parsed on every page load — but a hardcoded quotation is exactly
the kind of claim that goes quietly stale when the corpus is rebuilt.

So every field is checked here against the committed candidate rows. If the
locator stops producing that sentence, or the occurrence counts move, this fails
and names the field rather than letting the home page keep asserting it.

The tests skip when the candidate file is absent, so a fresh clone that has not
run `build_candidates` still has a green suite.
"""

from __future__ import annotations

import csv
import re
from collections import Counter
from pathlib import Path

import pytest

from app.main import SPECIMEN

ROOT = Path(__file__).resolve().parent.parent
CANDIDATES = ROOT / "data" / "adjudication" / "metrics_candidates.csv"
COVERAGE = ROOT / "data" / "derived" / "corpus_coverage.json"


def _normalise(text: str) -> str:
    """Collapse whitespace, including the U+00A0 that SEC HTML is full of."""
    return re.sub(r"\s+", " ", (text or "").replace(" ", " ")).strip()


@pytest.fixture(scope="module")
def rows() -> list[dict[str, str]]:
    if not CANDIDATES.is_file():
        pytest.skip(f"{CANDIDATES.name} not built; run pipeline.build_candidates")
    cik = str(SPECIMEN["cik"])
    phrase = SPECIMEN["phrase"].lower()
    with CANDIDATES.open(newline="", encoding="utf-8") as handle:
        return [
            row
            for row in csv.DictReader(handle)
            if row["cik"] == cik and phrase in (row["metric_name"] or "").lower()
        ]


def test_the_quoted_sentence_is_in_the_corpus(rows: list[dict[str, str]]) -> None:
    """The sentence on the home page is one a filing actually contains."""
    wanted = _normalise(SPECIMEN["quote"])
    matches = [r for r in rows if wanted in _normalise(r["defining_sentence"])]
    assert matches, (
        f"SPECIMEN['quote'] appears in no candidate row for CIK {SPECIMEN['cik']}. "
        "The home page is quoting a sentence the corpus no longer supports."
    )


def test_the_citation_points_at_the_listing_document(rows: list[dict[str, str]]) -> None:
    """Accession, form, date and URL all belong to the same real row."""
    row = next(
        (r for r in rows if r["candidate_id"] == SPECIMEN["candidate_id"]),
        None,
    )
    assert row is not None, "SPECIMEN['candidate_id'] is not in the candidate file"
    assert row["accession"] == SPECIMEN["accession"]
    assert row["form"] == SPECIMEN["form"]
    assert row["filing_date"] == SPECIMEN["filed"]
    assert row["url"] == SPECIMEN["url"]


def test_occurrence_counts_match_the_candidate_rows(rows: list[dict[str, str]]) -> None:
    """The bar chart is a count of rows, not an impression of one."""
    assert len(rows) == SPECIMEN["occurrences"]
    assert len({r["document"] for r in rows}) == SPECIMEN["distinct_documents"]


def test_the_breakdown_sums_to_the_total(rows: list[dict[str, str]]) -> None:
    """Every occurrence is in exactly one bar, so the bars can be read as a whole."""
    assert sum(row["n"] for row in SPECIMEN["where"]) == SPECIMEN["occurrences"]


def test_the_annual_report_share_is_the_one_the_page_claims(
    rows: list[dict[str, str]],
) -> None:
    """The page's whole argument is this number against the total.

    "A study that read only annual reports would have found N of these" is the
    single claim the worked example exists to make. If N drifts, the argument
    for including furnished exhibits in the corpus drifts with it.
    """
    by_type = Counter(row["doc_type"] for row in rows)
    assert by_type["10-K"] == SPECIMEN["annual_report_occurrences"]
    assert SPECIMEN["annual_report_occurrences"] < SPECIMEN["occurrences"] / 2, (
        "The example only illustrates METHOD.md §6 while most occurrences sit "
        "outside the annual report."
    )


def test_the_issuer_is_in_the_frozen_cohort() -> None:
    """A worked example drawn from outside the cohort would misrepresent it."""
    frozen = ROOT / "data" / "cohort" / "cohort_frozen.csv"
    if not frozen.is_file():
        pytest.skip("cohort not frozen")
    with frozen.open(newline="", encoding="utf-8") as handle:
        ciks = {row["cik"] for row in csv.DictReader(handle)}
    assert str(SPECIMEN["cik"]) in ciks


def test_no_terminal_state_is_asserted() -> None:
    """METHOD.md §4 and §5 reserve every state for a human.

    The home page shows this candidate before any ruling exists, so it must not
    carry a state, a reviewer or a verdict in any form.
    """
    forbidden = {"state", "terminal_state", "include", "reviewer", "verdict", "ruling"}
    assert not forbidden & set(SPECIMEN)
