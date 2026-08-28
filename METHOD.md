# Method — Pre-Registration

**Status:** PRE-REGISTERED. Written and committed before any filing was retrieved, any
metric extracted, or any statistic computed.

**Date of pre-registration:** 28 August 2026
**Author:** Jex Lin
**Amendments:** any change after the tag `preregistration-v1` is recorded in
`CHANGELOG.md` with its date, its reason, and the git hash it superseded. Nothing in
this file is edited silently.

---

## 0. Honesty statement

This publication began on **28 August 2026**. The documents it reads go back to 2019.
Those are two different claims and this site never conflates them. Where a figure is
described as historical, it refers to the age of the underlying filed record, not to
the age of this research.

Every specification this study runs is counted and published in `/method`, including
the ones that produced nothing. The pre-registered primary specification is #1. If the
finding is weak or null, that is the publication.

---

## 1. Question

When a company lists, it publishes two kinds of promise that nobody is obliged to keep:

1. **A scoreboard.** Bespoke operating metrics the company invented and defined for
   itself — "we define Adjusted Active Consumers as…" — chosen because management
   believed they were the right way to be judged.
2. **Numbers.** In a de-SPAC, management files multi-year revenue and EBITDA
   projections in the S-4/F-4 under *Certain Unaudited Prospective Financial
   Information*.

Both are abandonable at will. This study asks:

> **Across US companies that listed between 2019 and 2021, what proportion of the
> operating metrics they defined for themselves in their own listing documents were
> later discontinued or substantively redefined — and is discontinuation associated
> with subsequent adverse filing events?**

A secondary question, on the de-SPAC arm only: **how did management's own filed
projections compare with what was later reported?**

## 2. Why this question

The 2019–2021 US listing cohort was underwritten on projections and bespoke metrics to
an unusual degree, and the subsequent filed record is now long enough to grade it —
three to five annual reports per issuer. The population is large, entirely public, and
has not, to the author's knowledge, been systematically graded on *metric survival* as
distinct from share-price performance.

The study deliberately measures something other than returns. Price performance for
this cohort is well documented and adds nothing. Whether a company still reports the
scoreboard it invented is not documented, is fully determinable from the filed record,
and is a question about disclosure behaviour rather than about the market.

---

## 3. Cohort — inclusion rule

Frozen before extraction. A company enters the cohort if **all** hold:

| # | Criterion |
|---|---|
| C1 | Completed an IPO, direct listing, or de-SPAC business combination with an effective registration statement dated between **2019-01-01 and 2021-12-31** inclusive. |
| C2 | Is an **operating company** at listing. Blank-check shells that had not yet completed a business combination, closed-end funds, and pure holding vehicles are excluded. |
| C3 | Filed at least **three subsequent annual reports** (10-K or 20-F) with a filing date after the listing date and on or before **2026-08-28**. |
| C4 | The listing document contains at least one **company-defined operating metric** as adjudicated under §4. Companies passing C1–C3 but failing C4 are recorded as `NO_METRICS_DEFINED` and retained in the denominator for §7.1 only. |
| C5 | Filings are retrievable from EDGAR in machine-readable form. |

**Sampling.** Candidates are enumerated from EDGAR full-text search and the bulk
submissions archive, ordered by CIK ascending, and taken in that order until the target
is reached. Ordering by CIK is arbitrary with respect to every outcome variable and is
fixed before enumeration. **The cohort is not selected on the outcome.** No company is
added or removed after freeze because of what was found in it.

**Target n = 50. Floor n = 25.** The realised n is published. If the floor is not
reached the study is published as a null result with the shortfall stated.

**Freeze.** The company list is committed to `data/cohort/cohort_frozen.csv` with a git
tag before any metric is extracted. Companies later found to fail C1–C3 on inspection
are removed with the reason recorded in `data/cohort/exclusions.csv`; **no company is
ever removed for its result.**

**Sector.** Each issuer is tagged with the **SIC code EDGAR itself assigns**, grouped
into plain-English sectors. No proprietary or hand-built taxonomy is used.

---

## 4. What counts as a company-defined operating metric

**Candidate location is mechanical. Inclusion is human.**

Candidate spans are located in the listing document and the first annual report by
pattern search over definitional constructions (`we define … as`, `Key Operating
Metrics`, `Key Performance Indicators`, `we calculate … as`, and the surrounding
section headings). Pattern search is a *locator*. It decides nothing.

Every candidate is then ruled on by hand. A candidate is **included** when all hold:

- It is **quantitative** and reported as a number or a rate.
- It is **defined by the company**, not by GAAP/IFRS or by an SEC form requirement.
  Revenue, net income, and EPS are excluded. Non-GAAP measures (Adjusted EBITDA) are
  included **only** where the company supplies its own definition.
- It is **presented as a measure of operating performance**, not a risk-factor
  statistic, a market-size estimate, or a one-off disclosure.
- It is **attributed to the issuer's own operations**, not to its industry.

Each ruling is written to `data/adjudication/metrics.csv` with the reviewer's initials,
the date, the verbatim defining sentence, the accession number, the character offset,
and a one-line rationale. **That ledger is the study.** It is committed, browsable, and
every published row links to its row in it.

---

## 5. Metric life history — event taxonomy

Each included metric is tracked across the issuer's entire filed corpus and assigned
exactly one terminal state as at 2026-08-28:

| State | Definition |
|---|---|
| `ALIVE` | Reported in the most recent annual or quarterly report. |
| `REDEFINED` | Still reported, but the definition changed **substantively** — the population counted, the time window, or the arithmetic. Both definitions are published side by side. |
| `RENAMED` | Same underlying definition, new label. Traced and treated as continuous. |
| `ABSORBED` | Superseded by a broader metric that the company explicitly states subsumes it. |
| `DISCONTINUED` | Meets the four-period absence test in §6. |
| `NOT_DETERMINABLE` | The filed record does not settle the question. Used, and published. |

Cosmetic rewording, rounding changes, and unit changes are **not** `REDEFINED`. The
distinction between substantive and cosmetic is a human ruling and is recorded with its
rationale.

---

## 6. The discontinuation test

> A metric is `DISCONTINUED` only when its defining phrase, and every traced rename of
> it, is **absent from the issuer's entire filed corpus for four consecutive reporting
> periods**.

**Corpus** means every document the issuer filed or furnished to EDGAR: 10-K, 10-Q,
20-F, 40-F, 8-K and 6-K **including furnished earnings-release exhibits (EX-99.x)**.

This test exists to defeat one specific objection: that a metric did not disappear but
merely **moved** — most often out of the annual report and into a quarterly earnings
release, which foreign private issuers furnish on 6-K rather than filing on 10-Q.
Absence from the annual report alone is **not** evidence of discontinuation and is
never treated as such.

**Corpus boundary, stated plainly.** Investor decks, earnings-call transcripts,
websites, and press materials not furnished to EDGAR are **outside the corpus**. Every
claim this study makes is about the SEC-filed record and nothing more. A metric this
study calls `DISCONTINUED` may still appear in an investor deck. Where the boundary is
material to a specific company, the state is `NOT_DETERMINABLE`, not `DISCONTINUED`.

**A discontinuation is not proof of intent.** Benign causes exist: segment
reclassification under ASC 280, supersession by an accounting standard, an SEC
comment-letter-driven non-GAAP change, or a disposal of the business the metric
measured. These are adjudicated by hand, labelled, and the primary result is published
both with and without them (§7.4).

---

## 7. Analysis — pre-registered specifications

### 7.1 Primary descriptive result

The share of included metrics in each terminal state, with an exact binomial 95%
interval, reported per issuer and pooled. **This is the headline. It is a base rate.**

### 7.2 Primary hypothesis (specification #1 — the only confirmatory test)

Issuers are split by their own metric behaviour:

- **KEEPERS** — no metric `DISCONTINUED` and none substantively `REDEFINED`.
- **MOVERS** — at least one `DISCONTINUED` or substantively `REDEFINED`.

> **H1.** Movers exhibit a higher rate of subsequent adverse filing events than Keepers.

**Outcome variable — objective, dated, public, and not chosen by the author.** Extracted
from the `items` array of the bulk EDGAR submissions archive. An issuer has an adverse
filing event if, **after** the first discontinuation or redefinition date (and for
Keepers, after the equivalent median offset from listing), it filed any of:

| Signal | Source |
|---|---|
| Non-reliance on previously issued financials | 8-K **Item 4.02** |
| Auditor change | 8-K **Item 4.01** |
| Late periodic filing | **NT 10-K / NT 10-Q** |
| Delisting | **Form 25 / 25-NSE** |

**Test.** Difference in proportions, Keepers vs Movers, with a **10,000-resample
bootstrap 95% CI**; Fisher's exact test for the 2×2. Mann-Whitney U on the per-issuer
count of adverse events as a secondary form. **n in each arm is published beside every
figure.**

**Power, stated honestly and in advance.** At n=50 this study is **underpowered for
anything but a large effect.** It cannot support a causal claim and will not make one.
The words *cause*, *predicts*, and *leads to* do not appear in the findings. The
reported quantity is an association with an interval, and the interval will be wide.

### 7.3 Pre-registered counter-hypothesis — published either way

> **H0-counter.** Metrics disappear regardless of which way they were moving.

Discontinuation rate is computed **conditional on the metric's own last-reported
direction** (improving vs deteriorating over its final two reported periods). If
metrics vanish at similar rates whether improving or deteriorating, the headline is
substantially weakened — **and that result is published with the same prominence as the
primary.** This is committed to before the data exists precisely so it cannot be
quietly dropped.

### 7.4 Pre-registered sensitivity

The primary is re-run with hand-labelled benign causes removed. Both versions are
published. The difference is stated in the abstract.

### 7.5 De-SPAC projection arm

For issuers whose S-4/F-4 contains a *Certain Unaudited Prospective Financial
Information* table, projections are **hand-transcribed** with accession number, page,
and exact caption, then compared with realised revenue from XBRL `companyfacts`.
Reported as a realisation ratio with the coverage rate published. Hand transcription is
deliberate: it is cheaper than parsing, unimpeachable, and it is itself the reading
work.

### 7.6 Specification counter

Every specification run against the data is logged in `data/derived/spec_log.csv` with
its timestamp and result, and the total is published on `/method` as
*"Specifications run: N. Pre-registered: #1. All N shown below."*
(cf. Bailey, Borwein, López de Prado & Zhu on backtest overfitting.)

---

## 8. Publication rules

### 8.1 The asymmetric rule

> **Adverse conclusions appear only on already-resolved cases.**
> **Constructive, valued positions appear only on live names.**

An adverse conclusion may be published about a company only where the outcome is
already on the public record — a restatement filed, a delisting effected, an
enforcement action concluded, a take-private completed. **No adverse forward-looking
view is published on any live company, ever.**

Cohort arithmetic is unaffected: every company remains in the counts, because those are
public facts anyone can recompute.

### 8.2 Observations, never allegations

Every published sentence about a company must survive this test:

- *"This metric appears in no document filed after 2023-11-14"* — a **fact**, and checkable.
- *"Management hid it"* — an **allegation**, and unpublishable.

### 8.3 Provenance gate

Every numeral in published prose resolves to an accession number. A **build-time gate
fails the deploy** if any published figure lacks a resolvable citation. A scheduled job
re-fetches random cited spans and fails if a stored verbatim span no longer matches its
source.

### 8.4 Prices

No free price API permits public redistribution. Where a price is needed it is derived
as **filed value ÷ filed shares from Form 13F**, taken as the median across at least
three unrelated large filers, with cross-filer dispersion published. Sourcing prices
from filings rather than a vendor feed is a licensing decision and is stated as one.

### 8.5 Nothing is retroactively edited

Superseded views are struck through in place with a dated revision note. Killed
positions remain visible with the filing that killed them.

---

## 9. Positions

Live positions are constructive only (§8.1), signed, dated, and each carries:

- a stance in the first eight words;
- a variant perception — what the author believes that the price does not reflect;
- what would have to be true;
- expected return as a **spread over a bottom-up cost of capital in basis points**,
  never a target price. The hurdle is built line by line from **Damodaran's published
  country and industry risk premia** (NYU Stern, dated edition cited), plus a
  capital-structure adjustment. Every line cites its source.
- a downside stress case;
- **three dated, machine-checkable kill criteria**, evaluated by a scheduled job
  against incoming filings without the author's involvement.

Horizons are 10 and 20 years. Target prices, IRR, MOIC, Sharpe ratios, and
benchmark-relative alpha are not used — they describe a different kind of investor from
the one this study is written by.

---

## 10. Data sources

| Source | Use | Access |
|---|---|---|
| SEC EDGAR full-text search | Cohort enumeration | Public API |
| SEC EDGAR bulk submissions | Filing index, `items` array for outcomes | Public bulk |
| SEC XBRL `companyfacts` | Realised revenue | Public API |
| SEC Form 13F | Derived prices | Public filings |
| Damodaran / NYU Stern | Risk premia for the hurdle build | Public, cited by edition |

All access respects the SEC fair-access policy: a declared User-Agent with contact
details and a hard rate limit of 10 requests/second.

**No commercial data vendor is used. No licensed feed is redistributed.**

---

## 11. Provenance and independence

- Built entirely from **public SEC filings**, on **personal hardware**, on **personal time**.
- **No employer data, systems, methods, or work product** is used, referenced, or relied on.
- A full source manifest with **SHA-256 hashes** for every retrieved document is published
  at `/provenance`, so any reader can verify that a cited document is byte-identical to
  the one this study read.
- Where the author has a personal position in a cohort company, it is disclosed on the
  page where that company appears.

---

## 12. Limitations — stated first, not buried

1. **n is small.** At n=50 the study is underpowered for anything but a large effect. No
   causal claim is made or implied.
2. **The corpus is the SEC-filed record only.** Investor decks and transcripts are outside it.
3. **Survivorship.** Companies that delisted early file less, and are structurally
   under-represented in later periods. The direction of this bias is stated where it bites.
4. **Adjudication is one human's judgment.** It is recorded, dated, initialled, reasoned,
   and browsable — so it can be disagreed with row by row. It is not thereby objective.
5. **A discontinuation is not proof of intent** and benign causes exist. This is why §7.4
   is pre-registered rather than optional.
6. **2019–2021 is one cohort in one market.** Nothing here generalises to other vintages
   or other jurisdictions, and no attempt is made to claim otherwise.

---

*Pre-registered 28 August 2026, before data collection. Tagged `preregistration-v1`.*
