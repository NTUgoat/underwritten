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
