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

### Run the first sweep in `?order=name`

Open <http://127.0.0.1:8000/adjudicate?order=name> rather than the default.

It sorts by metric name rather than by issuer, so identical phrases arrive
**consecutively**. `non-GAAP financial measures` appears for 23 different issuers;
`key metrics` for 21; `non-GAAP measures` for 20. Ruling them back to back takes
seconds each, because the evidence is the same and you are not re-deciding
anything — whereas in issuer order those same 23 rulings are scattered across the
whole session and each one costs you a fresh read.

Measured on the real worklist: **946 groups become 705 consecutive runs.** A
quarter of the session is a repeat of the phrase you just ruled on.

There is deliberately **no "apply to all issuers" button**, and it would be a
mistake to add one. It is safe for a section heading, whose ruling cannot vary by
issuer — but 117 repeated groups are `company_defined` proposals like
`adjusted EBITDA`, and §4 admits a non-GAAP measure *only where that company
supplies its own definition*. The ruling genuinely differs by issuer, and a bulk
action would quietly flatten that distinction across a dozen companies. Ordering
gives you the speed without the failure mode.

Switch back to `?order=issuer` for the §5 pass, where you want one company's
whole filing history in your head at once.

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
| `p` | NEVER_REPORTED — promised at listing, never reported; guarded, see below |
| `n` | NOT_DETERMINABLE |

Presets on `1`–`9` **do not commit** here, unlike §4: a §5 ruling usually needs a date too.

**The DISCONTINUED guard.** §6 makes the four-period absence test a *necessary* condition,
so the tool refuses `DISCONTINUED` outright when the test is not met, naming the trailing
absent count against the four required. When it *is* met you must still tick that you
checked for a rename and write one line saying what you checked — because Airbnb's
"Nights and Experiences Booked" meets the test and was merely renamed. The status is read
from the evidence file inside the handler, never from the page, so it cannot be posted around.

**The NEVER_REPORTED guard.** Same shape, different fact. The evidence stage records two
things per metric: whether the defining phrase occurs in the issuer's listing document, and
how many times it appears in everything filed afterwards. The tool refuses `NEVER_REPORTED`
unless the first is true and the second is zero, and the refusal says which of the two
failed — *not found in the listing document*, or *appears in N later filings*. Both facts
are read from `data/derived/absence_evidence.json` inside the handler, never from the page.
Evidence written before the 29 August 2026 amendment carries neither field, and that is
also a refusal: an uncomputed fact is not an established one. Re-run
`python -m pipeline.build_evidence`.

When it is permitted you must still tick that you looked for a label the issuer adopted
*before* its first report, and write one line saying what you checked — the same discipline
as the rename trap, for the same reason. Several of the 107 eligible phrases are prospectus
boilerplate and should not have survived §4 at all.

`NEVER_REPORTED` also needs a **date** and takes `UNDETERMINED` as its direction, and the
tool enforces both. The date is the listing document's, entered as *first appearance*: for
this state that is also the last appearance, because there is no other, and §7.2 drops a
Mover it cannot place in time. The direction is undetermined by construction — §7.3
conditions on the final two *reported* periods and this metric has none.

**Benign causes** (§7.4) are recorded as labels on DISCONTINUED, REDEFINED and
NEVER_REPORTED, not folded into the rationale, because the primary is re-run with them
removed and published both ways.

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
| `NEVER_REPORTED` | Defined in the listing document, appearing in **no** subsequent filing, **and** you have ruled out a label adopted before the first report and prospectus boilerplate. |
| `NOT_DETERMINABLE` | The filed record does not settle it. **Use this. It is not a failure.** |

### NEVER_REPORTED is not a kind of DISCONTINUED

A discontinued metric was reported and then stopped. A never-reported metric was promised
and never reported at all. The §6 absence test cannot tell you about the second: it counts
absence across reporting periods and needs a first appearance to count absence *from*, so
every one of these scores `NOT_DETERMINABLE` on the mechanical test. That is why the state
was added, and it is the only amendment so far that makes the finding **larger** rather
than smaller — which is why §7.1 publishes the base rate both with it and without it, and
why the guard on it is strict.

**Super League Enterprise** put five metrics under a heading reading "KPI" in its own
prospectus — *Always On Venues*, *Experiences*, *Conversion Registered Accounts*,
*Engagement Participations*, *Gameplay Hours*. Three appear in nothing it has filed since.
That is the case the state exists for.

Before ruling it:

1. Read the **first** annual or quarterly report after listing, including EX-99 exhibits.
2. Look for a similarly-shaped metric reported in place of this one — a label the issuer
   adopted between the prospectus and its first report is `RENAMED`, not this.
3. Ask whether the phrase is a metric under §4 at all. Prospectus boilerplate that survived
   the first pass should be sent back to §4, not given a terminal state.
4. Only then, and only if the filed record is clear, rule `NEVER_REPORTED`.

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
