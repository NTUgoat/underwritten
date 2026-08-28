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
# A de-SPAC locator must identify a SPAC, not a merger. "Certain Unaudited
# Prospective Financial Information" is the standard caption for management
# projections in ANY merger proxy - it matched AMD/Xilinx, Bristol Myers/Celgene
# and S&P Global/IHS Markit. "blank check company" is how a SPAC describes
# itself in its own registration statement. The projection table is verified at
# document level in the §7.5 stage, not used to select the cohort.
DESPAC_SPAC_PHRASE = '"blank check company"'

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
        (DESPAC_SPAC_PHRASE, forms, "DESPAC")
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
                if existing is None or _prefer(candidate, existing):
                    by_cik[candidate.cik] = candidate

            offset += page_size
            # EDGAR full-text search will not page beyond 10,000 results.
            if offset >= 9_990:
                break

    return sorted(by_cik.values(), key=lambda c: c.cik)


def _prefer(new: Candidate, existing: Candidate) -> bool:
    """Which of two matches for the same CIK describes the actual listing.

    A SPAC files its own IPO registration statement years before the S-4 that
    registers the combination, and both can match a locator. Taking the earlier
    document makes the SHELL's IPO the listing: Celularity (CIK 1752828) was
    labelled IPO with a 2019-04-26 listing, when that is the GX Acquisition
    shell's own S-1 and Celularity actually listed on completion, 2021-07-22.

    So the de-SPAC reading wins whenever it exists, carrying its own
    registration date. Within one arm, the earliest match wins, since an issuer
    amends a registration statement several times and the first is closest to
    the listing.

    Note this is NOT "has an 8-K Item 2.01". Operating companies file Item 2.01
    for any material acquisition - Super League (CIK 1621672) is a genuine 2019
    IPO with three of them. The arm is decided by which registration statement
    the issuer filed, not by its later acquisition activity.
    """
    if new.arm == existing.arm:
        return new.listing_filing_date < existing.listing_filing_date
    return new.arm == "DESPAC"


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

    # C0 - the issuer must actually be newly listed, in either arm.
    #
    # This is the catch-all that the arm-specific tests below cannot provide.
    # C1b protects the IPO arm only, so without C0 a long-public company reached
    # the de-SPAC arm unchallenged: AMD (first filing 1994), Bristol Myers
    # Squibb (1994) and S&P Global (1994) all entered a cohort of "2019-2021
    # listings" on the strength of ordinary M&A S-4s.
    #
    # It also matters because the walk order is CIK ascending, and CIK is issued
    # sequentially - so the walk starts at the OLDEST registrants on EDGAR and
    # would fill every slot with them before reaching a genuine 2019 listing.
    # The ordering is still outcome-blind; it is age-correlated, which is
    # precisely why the age rule has to be explicit rather than assumed.
    earliest = index[0].filing_date
    history_years = (candidate.listing_filing_date - earliest).days / 365.25
    if history_years > config.MAX_PRE_LISTING_HISTORY_YEARS:
        return reject(
            "C0",
            f"{history_years:.1f} years of EDGAR history before the registration "
            f"statement (earliest filing {earliest}, {index[0].form}) - not a new "
            f"listing",
        )

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

        grace_end = date.fromisoformat(config.LISTING_WINDOW_END).replace(
            year=date.fromisoformat(config.LISTING_WINDOW_END).year
            + config.DESPAC_COMPLETION_GRACE_MONTHS // 12
        )
        if listing_date > grace_end:
            return reject(
                "C1c",
                f"combination completed {listing_date}, beyond the window end "
                f"plus {config.DESPAC_COMPLETION_GRACE_MONTHS} months ({grace_end})",
            )

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
    # Amendments must not count toward C3. Super League (CIK 1621672) files
    # 7 10-Ks and 4 10-K/As; counting filings rather than periods would let an
    # issuer satisfy "three annual reports" with two years and an amendment.
    annuals = [
        f
        for f in annual_reports(index)
        if f.filing_date > listing_date
        and f.filing_date <= as_at
        and not f.form.upper().endswith("/A")
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
