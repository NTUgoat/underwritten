# Adjudication guide

Operational guidance for ruling on metric candidates under `METHOD.md` §4 and §5.
Written before the rulings begin, so the standard is fixed in advance rather than
drifting as the work goes on.

---

## How to run the session

Two commands. The first regenerates the candidate file from the corpus; the second
starts the tool on localhost.

```powershell
# 1. Extract candidates from the built corpus (minutes, no network)
.venv\Scripts\python.exe -m pipeline.build_candidates

# 2. Start the tool. It writes to disk, so it only mounts with the flag set.
$env:UNDERWRITTEN_ADJUDICATE = "1"
$env:UNDERWRITTEN_REVIEWER   = "JL"     # your initials, written into every row
.venv\Scripts\python.exe -m uvicorn app.main:app --port 8000
```

Then open <http://127.0.0.1:8000/adjudicate>.

**The flag is never set on the deployed site.** With it unset the tool registers zero
routes — the public site cannot reach it, which is checked by a test rather than assumed.

### What to expect

- Roughly **550 groups** across the cohort, one decision each. A group is one issuer and
  one metric name; ruling it once writes through to every occurrence — the first group is
  a heading with **1,014** occurrences behind it.
- Over half are section headings or boilerplate: one keystroke each.
- Progress is saved after every ruling. Close the browser whenever you like and resume
  where you stopped; ruled groups are skipped.

### Keys

| Key | Action |
|---|---|
| `i` / `x` / `n` | arm INCLUDE / EXCLUDE / NOT_DETERMINABLE |
| `1`–`9` | pick a rationale preset **and commit** |
| `Enter` | commit the armed verdict with the rationale shown |
| `t` | jump to the free-text rationale |
| `s` | skip (writes nothing) |
| `b` | back |
| `?` | show all keys |

Nothing is armed on load, and a commit requires a verdict, your initials, and a non-empty
rationale — so an accidental keypress cannot write a row.

### The second pass: §5 terminal states

Once §4 is done, `/adjudicate/state` walks the metrics you INCLUDED and asks for a
terminal state. Same ledger, same initials, same atomic writes.

| Key | State |
|---|---|
| `a` | ALIVE |
| `e` | REDEFINED — you must then set substantive or cosmetic explicitly |
| `m` | RENAMED — you must give the new name, so §6 can be re-run over the alias |
| `o` | ABSORBED |
| `d` | DISCONTINUED — guarded, see below |
| `n` | NOT_DETERMINABLE |

Presets on `1`–`9` **do not commit** here, unlike §4: a §5 ruling usually needs a date too.

**The DISCONTINUED guard.** §6 makes the four-period absence test a *necessary* condition,
so the tool refuses `DISCONTINUED` outright when the test is not met, naming the trailing
absent count against the four required. When it *is* met you must still tick that you
checked for a rename and write one line saying what you checked — because Airbnb's
"Nights and Experiences Booked" meets the test and was merely renamed. The status is read
from the evidence file inside the handler, never from the page, so it cannot be posted around.

**Benign causes** (§7.4) are recorded as labels on DISCONTINUED and REDEFINED, not folded
into the rationale, because the primary is re-run with them removed and published both ways.

### The machine's proposal

Each group carries a proposed ruling with the rule it fired on, rendered dashed and
labelled *"machine proposal · not a ruling"*. Nothing is pre-selected. The proposal text
is **never** used as your rationale, and whether you agreed with it is recorded in
`adjudication_log.jsonl`, so the agreement rate can be inspected afterwards. If that rate
is near 100%, that is evidence the proposals were rubber-stamped rather than reviewed —
which is exactly what a sceptical reader would want to check, so it is recorded.

Validated against real filings: the proposals correctly excluded 24 section headings,
included 10 genuine company-defined metrics (`EVE`, `cumulative deposit beta`), abstained
on 9 it could not classify, and produced **one false include** — `Contents A CAM`, a
Critical Audit Matter from the auditor's report. Expect a few of those. Catching them is
the job.

**This document does not contain any ruling.** It explains how to make one, and
records the edge cases already found in real filings so the same question is not
re-decided differently on day two. Every ruling is yours, signed and dated.

---

## 0. The one rule that matters

> **The machine locates. You decide.**

The locator finds text matching definitional patterns. It has no view on whether
something is a metric. It cannot tell a retired metric from a renamed one, and it
must never try — that distinction is the study's entire finding, and it is the
reason a human ledger exists at all.

If you find yourself ruling quickly because the proposal looked right, stop. The
proposal is a sorting aid, not evidence. The verbatim sentence is the evidence.

---

## 1. What you are deciding, in order

For each **group** (one issuer, one metric name, all its occurrences):

1. **Include or exclude?** Is this a company-defined operating metric under §4?
2. If included, later: **what happened to it?** Its terminal state under §5.

Step 1 is the bulk of the work and most of it is obvious. Step 2 is the
judgment-heavy part and applies only to the survivors.

---

## 2. §4 — include or exclude

A candidate is **included** only when all four hold:

| Test | Include | Exclude |
|---|---|---|
| **Quantitative** | Reported as a number or rate | A concept, a policy, a defined term |
| **Company-defined** | The issuer supplies its own definition | GAAP/IFRS or an SEC form requirement defines it |
| **Operating performance** | Measures how the business performed | Risk factor, market-size estimate, one-off |
| **About this issuer** | The issuer's own operations | The industry, the market, a counterparty |

### Worked examples from this cohort

**Lemonade (CIK 1691421), S-1 of 2020-06-08 — INCLUDE all of these:**

- `Customers` — "we define Customers as…". Quantitative, company-defined, operating. A
  headcount definition is a real choice: who counts as a customer is exactly the kind
  of definition that later moves.
- `in force premium ("IFP")` — company-defined, no GAAP equivalent.
- `Premium per Customer ("PPC")` — a derived ratio the company invented.
- `gross loss ratio`, `net loss ratio` — insurance measures the company defines itself.
- `adjusted gross profit`, `adjusted EBITDA`, `adjusted gross margin`, `adjusted EBITDA
  margin`, `operating revenue` — non-GAAP, and Lemonade supplies a definition for each.
  §4 admits non-GAAP measures **only** where the company defines them. It does here.

**HBT Financial (CIK 775215) — INCLUDE:**

- `EVE` (economic value of equity) — quantitative, defined by the issuer.
- `cumulative deposit beta` — a real bank operating metric with a company definition.

**EXCLUDE — seen repeatedly, rule once and move on:**

- `non-GAAP financial measures`, `non-GAAP financial information`, `non-GAAP measures`,
  `non-GAAP metrics` — **section headings**, not metrics. The metrics live underneath.
- `key performance indicators`, `key operating metrics`, `key metrics`, `key performance
  metrics` — also headings. The locator matched the heading; the metrics are below it.
- `this transaction`, `this agreement`, `collectively`, `throughout this annual report on
  Form 10-K`, `the PLA and the PLA Plus collectively` — definitional **boilerplate**. The
  pattern `we define X as` is used throughout legal prose to define terms of art. Not
  quantitative, not operating.
- `its incremental borrowing rates for specific lease terms, used to discount future
  lease payments` — an **accounting policy** under ASC 842, not a company-invented metric.
- `revenue`, `net income`, `EPS`, `gross profit`, `operating income`, `total assets` —
  GAAP. Excluded by §4 explicitly, even when the filing restates the definition.

### The heading trap

`Key Operating Metrics` as a *heading* is excluded. But its presence means the issuer
almost certainly defines real metrics in that section. If a group is only ever the
heading and no real metrics were located for that issuer, **flag it** rather than
recording `NO_METRICS_DEFINED` — the locator may have missed a table or a bulleted list,
and a false `NO_METRICS_DEFINED` quietly shrinks the denominator in §7.1.

---

## 3. §5 — terminal state, for included metrics only

| State | Rule |
|---|---|
| `ALIVE` | Reported in the most recent annual or quarterly report. |
| `REDEFINED` | Still reported, definition changed **substantively** — the population counted, the time window, or the arithmetic. |
| `RENAMED` | Same definition, new label. Trace it; treated as continuous. |
| `ABSORBED` | The company states a broader metric now subsumes it. |
| `DISCONTINUED` | Meets the §6 four-period absence test **and** you have ruled out rename, absorption, and benign cause. |
| `NOT_DETERMINABLE` | The filed record does not settle it. **Use this. It is not a failure.** |

### Substantive vs cosmetic

Substantive — the number would change:
- The population counted changes ("active users" from 30-day to 90-day).
- The time window changes (quarterly to trailing-twelve-months).
- The arithmetic changes (a cost line added to or removed from a margin).

Cosmetic — the number would not change:
- Rewording that leaves the calculation identical.
- Capitalisation, punctuation, rounding, or unit presentation.
- Moving the definition to a different section.

When unsure: **would this change the reported number?** If yes, substantive.

### The rename trap — read this before ruling any DISCONTINUED

This was found in real data and it is the single most likely way to publish a false
finding.

**Airbnb** defined `Nights and Experiences Booked`. The §6 absence test scores
`ABSENCE_TEST_MET` — absent for five consecutive periods after 2025-05-01. A pipeline
that trusted the test would publish it as discontinued.

It was not. Airbnb **renamed** it to `Nights and Seats Booked`, first appearing
2025-08-06 — **inside an EX-99.1 earnings exhibit**, not in an annual report. Trace the
rename and the metric is continuous across all 23 periods.

So, before any `DISCONTINUED`:

1. Read the most recent earnings release, **including EX-99 exhibits**.
2. Look for a similarly-shaped metric that appeared around when this one stopped.
3. Check whether the company disposed of the business the metric measured.
4. Check for a segment reclassification (ASC 280) or an accounting standard change.
5. Only then, and only if the filed record is clear, rule `DISCONTINUED`.

If steps 1–4 leave doubt, the answer is `NOT_DETERMINABLE`. The study publishes that
state, and publishing it honestly costs nothing.

### Benign causes — label, do not just exclude

`METHOD.md` §7.4 re-runs the primary with benign causes removed, so these need their own
label rather than a quiet exclusion:

- Segment reclassification under ASC 280 / IFRS 8
- Supersession by an accounting standard
- SEC comment-letter-driven non-GAAP change
- Disposal of the business the metric measured

---

## 4. Writing the rationale

One line. It is published beside your initials and a reader may disagree with it — that
is the point. Say what you relied on, not how you felt.

Good:
- `heading, not a metric — the metrics are defined below it`
- `GAAP measure, excluded under §4`
- `renamed to "Nights and Seats Booked", EX-99.1 of 2025-08-06`
- `definition changed from 30-day to 90-day active, 10-K FY2022 — substantive`
- `absent 6 periods; no rename found in any filed document; no disposal disclosed`
- `cannot determine: 10-Q for Q3 2023 unreadable, gap covers the trailing run`

Bad:
- `not a metric` (why?)
- `looks fine` (what did you check?)
- `probably renamed` (then it is `NOT_DETERMINABLE`, not `RENAMED`)

---

## 5. Order of work

1. **Sweep the obvious excludes first.** Headings and boilerplate are perhaps two-thirds
   of the groups and each takes seconds. This shrinks the problem fast and calibrates you.
2. **Then the clear includes** — named, quantitative, company-defined.
3. **Then the hard ones**, with the filings open. Expect these to be a small minority and
   to take most of the time. That is correct; they are where the finding lives.

Do not rule terminal states in the same pass as inclusion. Inclusion is a fast
consistent judgment; terminal state needs the filing history in front of you.

---

## 6. Pace and honesty

Roughly **600 groups** are expected across 50 issuers. Most are seconds; a minority take
minutes. If you find yourself averaging under five seconds a group across a long run, you
have stopped reading — and the ledger is the one artifact in this project that cannot
survive being faked, because every row is public and checkable against a filing.

If you get tired, stop. A `NOT_DETERMINABLE` recorded honestly at 2am is fine. A
`DISCONTINUED` recorded carelessly at 2am is a false finding with your name on it.
