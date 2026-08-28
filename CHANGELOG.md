# Changelog

Every amendment to the pre-registered method, dated, with its reason and the commit it
superseded. Nothing in `METHOD.md` is edited silently. Entries are append-only.

---

## 2026-08-28 — Amendment 1: two mandatory exclusions from the outcome variable

**Supersedes:** `0a663cf` (tag `preregistration-v1`)
**Applies to:** METHOD.md §7.2
**Status:** made **before data collection**. No cohort had been frozen, no metric
extracted, and no statistic computed when this was written.

### What changed

Two classes of filing are now excluded from the primary adverse-event outcome, each
labelled and counted rather than dropped:

- **E1 `SECTOR_WIDE_WARRANT_RESTATEMENT`** — 8-K Item 4.02 non-reliance filings caused by
  the SEC's 12 April 2021 *Staff Statement on Accounting and Reporting Considerations for
  Warrants Issued by Special Purpose Acquisition Companies*.
- **E2 `MECHANICAL_DESPAC_DELISTING`** — Form 25 / 25-NSE filed within ±30 days of an 8-K
  carrying Item 2.01, i.e. the predecessor shell's routine delisting at merger close.

### Why

Both were found while testing the adverse-event extractor against three issuers
(Airbnb, Nikola, Lordstown) and both are confirmed in the filed record, not inferred:

**E1.** Nikola's 2021-05-04 Item 4.02 opens: *"On April 12, 2021, the Acting Director of
the Division of Corporation Finance and Acting Chief Accountant of the Securities and
Exchange Commission released a Staff Statement on Accounting and Reporting
Considerations for Warrants Issued by Special Purpose Acquisition Companies."*
Lordstown's 2021-05-11 Item 4.02 says the same thing in its own words. This restatement
wave hit essentially the entire SPAC universe simultaneously. An Item 4.02 caused by it
is evidence about a regulator's statement, not about the issuer's management. Left in,
the de-SPAC arm becomes almost entirely "adverse" by construction, the Keeper/Mover
contrast collapses, and the study measures the SEC's April 2021 calendar.

**E2.** Form 25-NSE dates sit *before* the completion 8-K in both test cases — Nikola
2020-06-03 against completion 2020-06-08, Lordstown 2020-10-23 against 2020-10-29. That
ordering is the signature of a mechanical shell delisting at merger close, not of a
failure. Counting it as an adverse event would score every de-SPAC in the cohort as
adverse on its listing date, which is the opposite of what the outcome is meant to
capture.

### Why this is an amendment rather than a silent fix

Both exclusions make the primary hypothesis **harder** to support, not easier: E1 removes
a large block of adverse events concentrated in the de-SPAC arm, and E2 removes an
adverse event from every de-SPAC in the cohort. Recording them here, before any result
exists, is what distinguishes a correction from tuning. The sensitivity in §7.4 reports
the primary with E1 restored, so a reader can see exactly what the exclusion cost.

### Reproducibility

Confirmed against these filings, all public:

| Issuer | CIK | Filing | Accession |
|---|---|---|---|
| Nikola | 1731289 | 8-K Item 4.02, 2021-05-04 | `0001731289-21-000050` |
| Lordstown | 1759546 | 8-K Item 4.02, 2021-05-11 | `0001104659-21-064530` |
| Nikola | 1731289 | Form 25-NSE, 2020-06-03 | `0001354457-20-000219` |
| Lordstown | 1759546 | Form 25-NSE, 2020-10-23 | `0001354457-20-000624` |

---

## 2026-08-28 — Amendment 2: C1b, listing documents only

**Supersedes:** `7fbcbc4`
**Applies to:** METHOD.md §3, inclusion criteria
**Status:** made **before data collection**. No cohort frozen, no metric extracted.

### What changed

New inclusion criterion **C1b**: the registration statement that brings an issuer into
the cohort must be a *listing* document, not a follow-on offering. Operationalised as —
the issuer filed **no periodic report** (10-K, 20-F, 40-F, 10-Q) before that
registration statement's filing date. The Form 8-A exchange-registration date is
recorded alongside each member as corroborating evidence.

### Why

Form S-1 registers *any* offering of securities, not only initial public offerings. It
covers secondary offerings, resale registrations, and shelf takedowns by companies that
have been public for years. The locator phrases in §4 ("Key Operating Metrics", "Key
Performance Indicators") appear in those documents too.

Found while smoke-testing candidate enumeration. **GrafTech International** (CIK 931148)
surfaced on an S-1 filed 2019-03-04 and would have entered the cohort. It had:

| Evidence | Value |
|---|---|
| Periodic reports filed before that S-1 | **87** |
| Earliest filing of any kind | 1996-04-10 |
| Form 8-A12B (exchange registration) | **1998-09-10** |
| Actual IPO | April 2018 |

Two genuine listings tested as controls behave the opposite way: **Affirm** (CIK
1820953) and **Rackspace** (CIK 1810019) each have **zero** periodic reports before
their S-1, their earliest filing is a draft registration statement months earlier, and
their Form 8-A falls after the S-1 (2021-01-13 and 2020-08-03 respectively).

Without C1b the study would measure "promises made at listing" against documents that
are not listing documents, for companies that listed outside the window entirely. That
is not a power problem or a noise problem — it is a validity problem, and it would have
invalidated the headline.

### Effect on the result

Unknown in direction and deliberately so: C1b is a validity fix applied before any
outcome was computed. It shrinks the candidate pool and will require walking further
down the CIK-ordered list to reach n=50. The exclusion funnel in
`data/cohort/exclusions.csv` records every C1b rejection with its evidence.

### Also fixed

EDGAR full-text search returns **100** hits per page, not 10. The pagination constant
was wrong, which would have re-requested overlapping pages and silently truncated
enumeration at the same time. Corrected to 100.

### Correction to this amendment, same day, before data collection

C1b as first written was applied to **both** arms, which was wrong and would have
emptied the de-SPAC arm entirely.

A SPAC is a reporting company from its own IPO, so it necessarily files periodic
reports before the S-4 that registers the business combination. Nikola's shell
(VectoIQ) filed **7 periodic reports before the S-4 of 2020-03-13**, the earliest a
10-Q on 2018-08-13. Under a universal C1b every de-SPAC fails, the arm carrying the
entire projection-realisation analysis (§7.5) is silently empty, and the cohort becomes
100% IPO without anything in the output saying so.

The listing test is therefore arm-specific:

- **IPO arm — C1b.** No periodic report (10-K, 20-F, 40-F, 10-Q) before the
  registration statement. The listing date is the registration statement date.
- **de-SPAC arm — C1c.** C1b does not apply. Instead the combination must have
  **completed**: an 8-K carrying Item 2.01 on or after the registration statement.
  C2 (SIC 6770) already removes shells that never completed one. The listing date is
  the **completion date**, not the S-4 date, and C3's three-annual-report count runs
  from it — annual reports the shell filed while still a shell are not reports about
  the operating business and must not count.

Both dates are recorded per member (`registration_date` and `listing_date`) so the
distinction is auditable rather than buried in code.

Verified after the fix: GrafTech rejected under C1b; Affirm accepted (IPO,
listing = registration = 2020-11-18); Nikola accepted (de-SPAC, registration
2020-03-13, listing 2020-06-08, 9 annual reports, SIC 3711 — correctly updated away
from blank-check on completion).
