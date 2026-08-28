"""Cohort enumeration and the frozen inclusion decision (METHOD.md §3).

The ordering rule matters more than it looks. Candidates are sorted by CIK
ascending and taken in that order until the target is reached. CIK is issued
sequentially by the SEC at registration, so it is arbitrary with respect to
every outcome this study measures - a company's CIK cannot know whether it will
later restate. Sorting by anything the study measures, or by relevance rank,
would select on the dependent variable.

Every candidate that is examined is recorded, whether or not it enters the
cohort, so the funnel from candidates to cohort is fully reportable.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass, asdict
from datetime import date
from typing import Iterable

from . import config
from .edgar import EdgarClient
from .filings import Filing, annual_reports, complete_index

# Phrases that identify a listing document defining its own operating metrics.
# These are locators for candidate issuers, not evidence about any metric - §4
# inclusion is decided by hand, later, on the document text.
IPO_METRIC_PHRASES = (
    '"Key Operating Metrics"',
    '"Key Performance Indicators"',
)
DESPAC_PROJECTION_PHRASE = '"Certain Unaudited Prospective Financial Information"'

IPO_FORMS = "S-1"
IPO_FORMS_FOREIGN = "F-1"
DESPAC_FORMS = "S-4"
DESPAC_FORMS_FOREIGN = "F-4"

# SIC 6770 is "Blank Checks". An entity still carrying it has not completed a
# business combination and fails C2.
BLANK_CHECK_SIC = "6770"

# Entity types that are not operating companies at listing (C2).
EXCLUDED_ENTITY_TYPES = {"investment company"}


@dataclass(frozen=True)
class Candidate:
    """An issuer surfaced by full-text search, before any inclusion test."""

    cik: int
    name: str
    arm: str  # "IPO" or "DESPAC"
    listing_form: str
    listing_filing_date: date
    listing_accession: str


@dataclass(frozen=True)
class CohortMember:
    """A candidate that passed every inclusion test, with the evidence."""

    cik: int
    name: str
    arm: str
    ticker: str
    sic: str
    sic_description: str
    listing_form: str
    listing_date: str            # IPO: registration statement; de-SPAC: completion
    registration_date: str       # the registration statement in both arms
    listing_accession: str
    exchange_registration: str  # Form 8-A date; corroborates C1b
    n_annual_reports_after_listing: int
    first_annual_report: str
    last_annual_report: str
    n_corpus_filings: int


@dataclass(frozen=True)
class Exclusion:
    """A candidate that failed a test. Recorded so the funnel is reportable."""

    cik: int
    name: str
    arm: str
    failed: str
    detail: str


def _hit_to_candidate(hit: dict, arm: str) -> Candidate | None:
    source = hit.get("_source", {})
    ciks = source.get("ciks") or []
    if not ciks:
        return None
    try:
        cik = int(str(ciks[0]).lstrip("0") or "0")
        filed = date.fromisoformat(source.get("file_date", ""))
    except (ValueError, TypeError):
        return None

    names = source.get("display_names") or ["(unknown)"]
    forms = source.get("root_forms") or [""]
    # EDGAR FTS ids look like "0001104659-21-064530:doc.htm"
    accession = str(hit.get("_id", "")).split(":")[0]

    return Candidate(
        cik=cik,
        name=str(names[0]),
        arm=arm,
        listing_form=str(forms[0]),
        listing_filing_date=filed,
        listing_accession=accession,
    )


def enumerate_candidates(client: EdgarClient, *, page_size: int = 100) -> list[Candidate]:
    """Every issuer whose listing document matches a locator phrase.

    Deduplicated by CIK, keeping the *earliest* matching filing, since a company
    typically files several amendments and the first is closest to the listing.
    """
    searches = [
        (phrase, forms, arm)
        for phrase in IPO_METRIC_PHRASES
        for forms, arm in ((IPO_FORMS, "IPO"), (IPO_FORMS_FOREIGN, "IPO"))
    ] + [
        (DESPAC_PROJECTION_PHRASE, forms, "DESPAC")
        for forms in (DESPAC_FORMS, DESPAC_FORMS_FOREIGN)
    ]

    by_cik: dict[int, Candidate] = {}

    for phrase, forms, arm in searches:
        offset = 0
        while True:
            response = client.full_text_search(
                phrase,
                forms=forms,
                start=config.LISTING_WINDOW_START,
                end=config.LISTING_WINDOW_END,
                offset=offset,
            )
            hits = response.get("hits", {}).get("hits", [])
            if not hits:
                break

            for hit in hits:
                candidate = _hit_to_candidate(hit, arm)
                if candidate is None:
                    continue
                existing = by_cik.get(candidate.cik)
                if existing is None or (
                    candidate.listing_filing_date < existing.listing_filing_date
                ):
                    by_cik[candidate.cik] = candidate

            offset += page_size
            # EDGAR full-text search will not page beyond 10,000 results.
            if offset >= 9_990:
                break

    return sorted(by_cik.values(), key=lambda c: c.cik)


def _listing_window_contains(when: date) -> bool:
    return (
        date.fromisoformat(config.LISTING_WINDOW_START)
        <= when
        <= date.fromisoformat(config.LISTING_WINDOW_END)
    )


def assess(
    client: EdgarClient, candidate: Candidate
) -> tuple[CohortMember | None, Exclusion | None]:
    """Apply C1-C5 to one candidate. Returns exactly one of member/exclusion."""

    def reject(failed: str, detail: str) -> tuple[None, Exclusion]:
        return None, Exclusion(
            cik=candidate.cik,
            name=candidate.name,
            arm=candidate.arm,
            failed=failed,
            detail=detail,
        )

    # C1 - listing document filed inside the window
    if not _listing_window_contains(candidate.listing_filing_date):
        return reject("C1", f"listing document filed {candidate.listing_filing_date}")

    # C5 - filings must be retrievable
    try:
        submissions = client.submissions(candidate.cik)
        index = complete_index(client, candidate.cik)
    except Exception as exc:  # noqa: BLE001
        return reject("C5", f"filings not retrievable: {type(exc).__name__}")

    if not index:
        return reject("C5", "empty filing index")

    # The listing test is arm-specific, because the two arms have structurally
    # different filing histories and one rule cannot serve both.
    if candidate.arm == "IPO":
        # C1b - the document must be a LISTING document, not a follow-on offering.
        # An S-1 registers any offering, including secondary and resale offerings
        # by companies public for decades. GrafTech (CIK 931148) filed a 2019 S-1
        # having listed in 2018, with periodic reports back to 1996 - it matches
        # the locator phrase and would otherwise enter the cohort, making
        # "promises made at listing" a claim about a document that is not one.
        #
        # Test: an already-reporting issuer has periodic filings predating its
        # registration statement. A genuine first-time listing has none.
        prior_periodic = [
            f
            for f in index
            if f.form.upper() in {"10-K", "20-F", "40-F", "10-Q"}
            and f.filing_date < candidate.listing_filing_date
        ]
        if prior_periodic:
            return reject(
                "C1b",
                f"{len(prior_periodic)} periodic reports predate the registration "
                f"statement (earliest {prior_periodic[0].form} "
                f"{prior_periodic[0].filing_date}) - already public, not a listing",
            )
        listing_date = candidate.listing_filing_date

    else:
        # C1c - the de-SPAC arm. C1b must NOT be applied here: a SPAC is a
        # reporting company from its own IPO, so it necessarily files 10-Qs
        # before the S-4. Nikola's shell (VectoIQ) filed 7 periodic reports
        # before the S-4 of 2020-03-13. Applying C1b would reject every de-SPAC
        # and silently empty the arm that carries §7.5 entirely.
        #
        # The right test is that the combination actually COMPLETED: an 8-K
        # carrying Item 2.01 on or after the registration statement. C2 above
        # already removes shells that never completed one (still SIC 6770).
        completions = [
            f
            for f in index
            if f.form.upper().startswith("8-K")
            and "2.01" in f.items
            and f.filing_date >= candidate.listing_filing_date
        ]
        if not completions:
            return reject(
                "C1c",
                "no 8-K Item 2.01 on or after the registration statement - "
                "business combination did not complete",
            )
        # For a de-SPAC the operating company's listing IS the completion.
        listing_date = completions[0].filing_date

    # C2 - operating company, not a blank-check shell or a fund
    sic = str(submissions.get("sic") or "")
    entity_type = str(submissions.get("entityType") or "").lower()
    if sic == BLANK_CHECK_SIC:
        return reject("C2", f"SIC {sic} (blank check) - no completed combination")
    if any(bad in entity_type for bad in EXCLUDED_ENTITY_TYPES):
        return reject("C2", f"entityType={entity_type!r}")

    # C3 - at least three annual reports after the listing, on or before as-at.
    # Counted from `listing_date`, which for a de-SPAC is the completion of the
    # combination rather than the registration statement - annual reports the
    # SPAC shell filed while it was still a shell are not reports about the
    # operating business and must not count toward C3.
    as_at = date.fromisoformat(config.AS_AT_DATE)
    annuals = [
        f
        for f in annual_reports(index)
        if f.filing_date > listing_date and f.filing_date <= as_at
    ]
    if len(annuals) < 3:
        return reject("C3", f"{len(annuals)} annual reports after listing (need 3)")

    eight_a = [f for f in index if f.form.upper().startswith("8-A")]
    exchange_registration = eight_a[0].filing_date.isoformat() if eight_a else ""

    tickers = submissions.get("tickers") or []
    corpus_count = sum(
        1 for f in index if f.form.upper() in {x.upper() for x in config.CORPUS_FORMS}
    )

    member = CohortMember(
        cik=candidate.cik,
        name=str(submissions.get("name") or candidate.name),
        arm=candidate.arm,
        ticker=str(tickers[0]) if tickers else "",
        sic=sic,
        sic_description=str(submissions.get("sicDescription") or ""),
        listing_form=candidate.listing_form,
        listing_date=listing_date.isoformat(),
        registration_date=candidate.listing_filing_date.isoformat(),
        listing_accession=candidate.listing_accession,
        exchange_registration=exchange_registration,
        n_annual_reports_after_listing=len(annuals),
        first_annual_report=annuals[0].filing_date.isoformat(),
        last_annual_report=annuals[-1].filing_date.isoformat(),
        n_corpus_filings=corpus_count,
    )
    return member, None


def build(
    client: EdgarClient,
    candidates: Iterable[Candidate],
    *,
    target: int = config.COHORT_TARGET_N,
) -> tuple[list[CohortMember], list[Exclusion]]:
    """Walk candidates in CIK order until `target` members are accepted.

    Stopping at the target rather than assessing everything is deliberate and
    pre-registered: the walk order is fixed and outcome-blind, so where it stops
    cannot bias the result. Every candidate actually examined is recorded.
    """
    members: list[CohortMember] = []
    exclusions: list[Exclusion] = []

    for candidate in candidates:
        if len(members) >= target:
            break
        member, exclusion = assess(client, candidate)
        if member is not None:
            members.append(member)
        elif exclusion is not None:
            exclusions.append(exclusion)

    return members, exclusions


def _write_csv(path, rows: list) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(asdict(rows[0]).keys()))
        writer.writeheader()
        for row in rows:
            writer.writerow(asdict(row))


def freeze(members: list[CohortMember], exclusions: list[Exclusion]) -> tuple:
    """Write the frozen cohort and the exclusion funnel. Both are committed."""
    cohort_path = config.COHORT / "cohort_frozen.csv"
    exclusions_path = config.COHORT / "exclusions.csv"
    _write_csv(cohort_path, members)
    _write_csv(exclusions_path, exclusions)
    return cohort_path, exclusions_path
