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

---

## 2026-08-28 — Amendment 3: C0, and a de-SPAC locator that identifies de-SPACs

**Supersedes:** `5b876de`
**Applies to:** METHOD.md §3 and §4 (candidate location)
**Status:** made **before any metric was extracted or any statistic computed**. A cohort
file had been produced and was **discarded in full**; it is superseded, not amended.

### What went wrong

The first cohort build returned n=50 and reported success. Inspecting it showed
**Advanced Micro Devices, Analog Devices, Bristol Myers Squibb, Canadian Pacific, M&T
Bank and S&P Global** — 49 of 50 members labelled de-SPAC, 31 of them banks. Not one is
a 2019-2021 listing. Two independent errors compounded:

**1. The de-SPAC locator did not identify de-SPACs.** The phrase used was *"Certain
Unaudited Prospective Financial Information"*. That is the standard caption for
management projections in **any** merger proxy or S-4 — AMD/Xilinx, Bristol
Myers/Celgene, S&P Global/IHS Markit all carry it. Form S-4 registers any business
combination, not a de-SPAC. The locator was selecting large-cap acquirers.

**2. Nothing tested whether a de-SPAC-arm issuer was newly listed.** C1b (Amendment 2)
was correctly restricted to the IPO arm, which left the de-SPAC arm with no
already-public test at all. AMD's first EDGAR filing is 1994-01-27; it entered a cohort
of 2019-2021 listings unchallenged.

These interacted with the pre-registered CIK-ascending walk order. CIK is issued
sequentially, so the walk begins at the **oldest** registrants on EDGAR. With the filter
broken, all 50 slots filled with 1990s registrants before the walk ever reached a
genuine 2019 listing. The ordering is still outcome-blind — a company's CIK cannot know
whether it will later restate — but it is strongly **age**-correlated, which is exactly
why the age rule must be explicit rather than assumed. The ordering rule is unchanged.

### What changed

- **New C0, both arms.** The issuer's earliest EDGAR filing must be no more than
  `MAX_PRE_LISTING_HISTORY_YEARS = 5` before the registration statement. The observed
  separation is not marginal: 26.8 years (AMD), 25.1 (Bristol Myers), 22.9 (GrafTech)
  against 0.1 (Affirm) and 2.3 (Nikola).
- **De-SPAC locator replaced** with `"blank check company"`, how a SPAC describes itself
  in its own registration statement. In S-4/F-4 over the window this returns 1,151
  filings, and the issuers are SPACs (Bird Global, Property Solutions Acquisition,
  Tortoise Acquisition II, RMG Acquisition, ACON S2 Acquisition). The projection table
  is now verified at document level in the §7.5 stage rather than used to select the
  cohort — selection and measurement should not share an instrument.
- **De-SPAC completion window.** Completion must fall within the listing window plus
  `DESPAC_COMPLETION_GRACE_MONTHS = 12`, so a late-2021 registration that closed in 2022
  qualifies while AMD's 2022 close on a 2020 registration does not.

Also noted: EDGAR full-text search does **not** AND multiple quoted phrases. Adding a
second phrase *raised* the hit count (1,151 to 1,346), so multi-phrase queries cannot be
used to narrow a search and were not.

### Why this is recorded rather than quietly fixed

No result had been computed, so nothing here is tuning. But the failure is worth keeping
on the record for its own sake: the build reported `ACCEPTED: 50` and met its
pre-registered target while being entirely invalid. A green count is not a finding. The
check that caught it was reading the fifty company names, and that is the only reason
this study did not publish a cohort of 2019-2021 listings containing Bristol Myers
Squibb.

Verified after the fix: AMD, Bristol Myers and GrafTech all rejected under C0; Affirm
(IPO) and Nikola (de-SPAC) both accepted with correct arm-specific listing dates.

---

## 2026-08-28 — Amendment 4: arm assignment, and annual periods vs annual filings

**Supersedes:** `5b876de`
**Applies to:** METHOD.md §3 (C3) and candidate deduplication
**Status:** before any metric was extracted or statistic computed.

### 1. A SPAC's own IPO registration is not the operating company's listing

A CIK can match both locators: the shell files an S-1 for its own SPAC IPO, and years
later an S-4 registers the combination. Deduplication kept the **earlier** filing, so
the shell's IPO became the listing. **Celularity** (CIK 1752828) was recorded as an IPO
listing on 2019-04-26 — that is the GX Acquisition shell's S-1. Celularity listed on
completion, **2021-07-22**.

The de-SPAC reading now wins whenever both exist, carrying its own registration date.
Within a single arm the earliest match still wins, since registration statements are
amended repeatedly and the first is closest to the listing.

This is deliberately **not** implemented as "has an 8-K Item 2.01". Operating companies
file Item 2.01 for any material acquisition: **Super League Enterprise** (CIK 1621672)
is a genuine January 2019 IPO with three of them. Arm is decided by which registration
statement the issuer filed, never by later acquisition activity.

After the fix: Celularity is de-SPAC, registration 2021-01-25, listing 2021-07-22;
Super League remains an IPO listing on 2019-01-04.

### 2. C3 counts annual periods, not annual filings

`ANNUAL_FORMS` includes `10-K/A` and `20-F/A`, so amendments were inflating the count.
Super League showed **11** annual reports (7 10-K plus 4 10-K/A) and FORUM MARKETS
**14** (9 plus 5) — more than the years either had been listed. An issuer could have
satisfied "three subsequent annual reports" with two years and an amendment. Amended
forms are now excluded from the C3 count. The cohort maximum falls to 8, consistent
with a January 2019 listing measured to August 2026.

### Resulting cohort

n = 50 (target met), 26 de-SPAC and 24 IPO, listing dates 2019-01-04 to 2021-11-01.
Funnel: 116 candidates rejected — C0 87, C1b 15, C3 9, C1c 4, C2 1.

---

## 2026-08-28 — Correction: the study was not reading its own listing documents

**Applies to:** METHOD.md §4 (candidate location). No amendment to the method —
the method was right and the code did not implement it.
**Status:** before any ruling. The candidate worklist is regenerated.

### What was wrong

METHOD.md §4 states that "Candidate spans are located in the listing document and
the first annual report." The pipeline located candidates over `corpus.documents`,
and `config.CORPUS_FORMS` contains no S-1, F-1, S-4, F-4 or 424B4 — it is the §6
absence corpus, which is deliberately restricted to the filed record *after*
listing so that a registration statement can never appear inside a reporting
period.

The two uses were conflated, so the listing document was invisible to the
locator. Lemonade's corpus is **109 documents — 82 8-Ks, 19 10-Qs, 6 10-Ks, and
not one S-1**. The study was grading promises made in listing documents while
never reading a listing document.

### Why it matters more than it sounds

The candidate population was "metrics defined in post-listing filings", not
"metrics defined at listing". A metric that a company defined in its prospectus
and then dropped **before its first annual report** could not be located at all —
and that is the most complete form of the behaviour this study exists to measure.
The bias runs in one direction: it removes the strongest cases from the
numerator.

### The fix

The two corpora stay separate, because merging them would break §6. Listing
documents are fetched by a new stage (`pipeline.fetch_listings`, roughly one
request per issuer) and handed to the locator only; they never enter the absence
corpus. `pipeline.build_candidates` now locates over
`(listing documents, corpus documents)`.

### Also corrected

`build_candidates` was still grouping on a third normalisation of its own
(`clean_metric_name(...).lower().strip()`) while the evidence stage and the
adjudication tool had been unified onto `metrics.metric_key`. Three definitions
of "the same metric" is how a denominator quietly differs from a numerator; it
now uses `metric_key` like everything else.

### Measured effect

Run both ways over the 31 issuers whose corpus was complete at the time:

| | groups |
|---|---|
| Located over the §6 corpus only | 467 |
| Located over listing documents **and** the §6 corpus | **542** |
| Findable **only** in the listing document | **75 (14% of the population)** |

Not all 75 survive §4 — some are prospectus boilerplate ("in this prospectus",
"vision") that a human will exclude. But several are unambiguously the thing the
study measures: Lemonade's *adjusted gross margin*, *operating revenue* and
*adjusted EBITDA margin*; Confluent's *contribution margin* and *contribution
margin percentage*. Those are company-defined non-GAAP metrics that appeared in
the prospectus and that the study, before this fix, could not see at all.

### How this was found

Not by review, and not by a test. A recall check — reading the "Key Operating
Metrics" section of real listing documents to see whether the locator was missing
metrics presented in tables rather than in "we define X as" sentences — returned
**zero issuers examined**, because there was no listing document in the cache to
read. The question that found it was about something else entirely.
