# Underwritten

**Grading listing-document promises against the subsequent SEC-filed record.**

**Live:** <https://underwritten-production.up.railway.app> · **Method:** [`METHOD.md`](METHOD.md) (pre-registered, tagged `preregistration-v1`, committed before any data was collected) · **Amendments:** [`CHANGELOG.md`](CHANGELOG.md)

When a company lists, it publishes two kinds of promise nobody is obliged to keep. It
invents a scoreboard — bespoke operating metrics it defined for itself, *"we define
Adjusted Active Consumers as…"* — chosen because management believed those were the right
terms on which to be judged. And, in a de-SPAC, it files multi-year revenue and EBITDA
projections in the S-4/F-4 under *Certain Unaudited Prospective Financial Information*.
Both can be abandoned at will, and abandoning them costs nothing.

This study asks what happened to them.

**Why I built it.** I invest my own money. None of it is in these fifty companies — but the
next time I look at one that has recently listed, I want to know something its prospectus
will not tell me: when a company invents a measure to be judged by, does it go on reporting
it? Nobody publishes that, so I measured it. The method below is what it took to answer the
question in a way I would still trust after I had put money behind it.

> Across US companies that listed between 2019 and 2021, what proportion of the operating
> metrics they defined for themselves in their own listing documents were later
> discontinued or substantively redefined — and is discontinuation associated with
> subsequent adverse filing events?

A secondary question, on the de-SPAC arm only: how did management's own filed projections
compare with what was later reported?

It deliberately measures something other than returns. Price performance for this cohort
is exhaustively documented and adds nothing. Whether a company still reports the
scoreboard it invented is not documented, is fully determinable from the filed record, and
is a question about disclosure behaviour rather than about the market.

---

## What it found

> **No finding yet. The instrument is complete; the reading is not.**
>
> As at 28 August 2026: the cohort is frozen at **50 of 50** issuers, the corpus is
> complete at **9,016 documents** with none unreadable, and pattern search has located
> **1,374 candidate metrics** from 22,397 occurrences. **Zero have been ruled on.**
>
> That last number is the study. `METHOD.md` §4 reserves every inclusion decision for a
> human, and §5 makes every terminal state a human ruling, so
> `data/adjudication/metrics.csv` does not exist until a person writes it. Nothing is
> inferred in the meantime and no figure stands in for one: `build_analysis` reports every
> section unavailable, and the site publishes the absence.
>
> When it is filled in, from pipeline output only, it will state: the realised **n**
> against the pre-registered target of 50 and floor of 25; the share of included metrics
> in each terminal state with an exact binomial 95% interval, published under both
> `NEVER_REPORTED` counting rules; the Keeper/Mover comparison with a bootstrap interval
> and the n in each arm; the result of the pre-registered counter-hypothesis; and the
> sensitivity with hand-labelled benign causes removed.
>
> A weak result, a null result, or a shortfall against the floor is the publication. See
> [`METHOD.md`](METHOD.md) §0 and §7.
>
> Live progress, and the weekly re-read of the cohort that runs regardless:
> [`/watch`](https://underwritten-production.up.railway.app/watch).

---

## The method, in five sentences

Every US company with an effective registration statement dated between 2019-01-01 and
2021-12-31 that was an operating company at listing, filed at least three subsequent
annual reports, and is retrievable from EDGAR is enumerated in CIK order — arbitrary with
respect to every outcome the study measures — and the resulting cohort is frozen and
committed before any metric is extracted. Candidate operating metrics are located in the
listing document by pattern search over definitional constructions, but pattern search
decides nothing: every candidate is ruled on by hand, and each ruling is written to
`data/adjudication/metrics.csv` with the reviewer's initials, the date, the verbatim
defining sentence, the accession number, the character offset, and a one-line rationale.
Each included metric is then tracked across the issuer's *entire* filed corpus — 10-K,
10-Q, 20-F, 40-F, 8-K and 6-K including furnished earnings-release exhibits — and assigned
exactly one terminal state, where `DISCONTINUED` requires absence from that whole corpus
for four consecutive reporting periods, so that a metric which merely *moved* from the
annual report into a quarterly earnings release is never mistaken for one that vanished.
Issuers are then split by their own behaviour into Keepers and Movers and compared on an
objective, dated, public outcome taken from the `items` array of the EDGAR submissions
archive — non-reliance filings, auditor changes, late filings, delistings — with two
pre-registered exclusions for confounds that would otherwise score the de-SPAC arm as
adverse by construction. Every specification run against the data is logged and published,
including the ones that produced nothing, and at n=50 the study is underpowered for
anything but a large effect, so it reports an association with a wide interval and makes
no causal claim.

### Why US filings, and not Singapore ones

Because the method has to be checkable by someone who does not trust it, and EDGAR is the
only filings corpus in the world that makes that possible for free: a complete bulk
submissions archive, full-text search across every filer, a machine-readable `items`
taxonomy that classifies corporate events without anyone hand-labelling them, and a
published fair-access policy that says exactly how fast a stranger may re-fetch what this
study read. SGX offers no equivalent. The same study on Singapore filings could be argued
for but not *audited*, and an unauditable version of this study is not worth doing.

The question is jurisdiction-agnostic — whether management goes on reporting the yardstick
it chose for itself is not a US phenomenon. The evidence is not. This is one cohort in one
market, and `METHOD.md` §12.6 says so rather than implying otherwise.

The full pre-registration, including the inclusion rule, the event taxonomy, the
discontinuation test, the publication rules and the stated limitations, is in
**[`METHOD.md`](METHOD.md)**. It is the document to read if you intend to disagree with
this study, because it is where the disagreement can be made precise.

---

## Honesty

This section is not boilerplate. The study's entire premise is that a claim is worth what
its provenance is worth, so the same standard is applied to the study itself.

**The publication date and the document dates are different claims.** This publication
began on **28 August 2026**. The documents it reads go back to 2019. Those two facts are
never conflated: where a figure is described as historical, it refers to the age of the
underlying filed record, not to the age of this research. Nothing here is presented as a
call made in 2019 and vindicated since.

**The method was pre-registered before any data was collected.** `METHOD.md` was written
and committed before a single filing was retrieved, any metric extracted, or any statistic
computed, and tagged **`preregistration-v1`** at commit **`0a663cf`**. Every amendment
since is recorded in **[`CHANGELOG.md`](CHANGELOG.md)** with its date, its reason, and the
commit it superseded. Amendment 1 — two exclusions from the adverse-event outcome — was
made before data collection and makes the primary hypothesis *harder* to support, not
easier; that is stated there with the four public accession numbers it was confirmed
against. Nothing in `METHOD.md` is edited silently.

**The engineering was built with AI assistance. The judgment was not.** The pipeline in
`pipeline/` and the site in `app/` — the EDGAR client, the rate limiter, the caching and
hashing, the filing index, the parsers, the deployment configuration, this file — were
written with AI assistance. The adjudication ledger is not. Every ruling on whether a span
is a company-defined operating metric, every substantive-versus-cosmetic call on a
redefinition, every benign-cause label, and the written note and the positions are the
author's own work, made by reading the filings, and each carries the initials and date of
the person who made it. That division is the whole point: the machinery is a tool for
getting documents onto the desk accurately and provably, and what is claimed about them is
a human's, with a name against it. Stating this plainly is cheaper than being asked.

**Public sources only.** Built entirely from public SEC filings, on personal hardware, on
personal time. **No employer data, systems, methods, or work product** is used, referenced,
or relied on. No commercial data vendor is used and no licensed feed is redistributed —
which is also why prices, where needed at all, are derived from Form 13F filings rather
than a vendor feed. A full source manifest with a SHA-256 for every retrieved document is
published, so any reader can check that a cited document is byte-identical to the one this
study read.

**The corpus has a boundary, and it is stated rather than implied.** The corpus is the
SEC-filed record and nothing more. Investor decks, earnings-call transcripts, websites and
press materials not furnished to EDGAR are **outside** it. A metric this study calls
`DISCONTINUED` may still appear in an investor deck; that is a limitation of the corpus,
not a contradiction of the finding, and where the boundary is material to a specific
company the state recorded is `NOT_DETERMINABLE` rather than `DISCONTINUED`.

**What is not claimed.** No causal claim, at any n this study will reach. No adverse
forward-looking view on any live company. No allegation about anyone's intent — a
discontinuation is a fact about a filing, not evidence about a motive, and benign causes
exist and are labelled. Adjudication is one human's judgment: recorded, dated, initialled,
reasoned and browsable so it can be disagreed with row by row, which does not make it
objective.

---

## Reproducing it

Everything below runs against public endpoints. You do not need credentials, only a
contact address.

**1. Environment.** Python 3.13 (3.11+ is supported), with [uv](https://docs.astral.sh/uv/):

```bash
uv venv
uv pip install -r pyproject.toml       # add --extra dev for pytest and ruff
source .venv/bin/activate              # Windows: .venv\Scripts\activate
```

**2. `SEC_CONTACT` — required, and not a formality.** The SEC's fair-access policy requires
automated clients to declare a real contact address in the User-Agent header, and caps
access at 10 requests/second. `pipeline/edgar.py` refuses to construct a client whose
contact string has no address in it, and rate-limits itself to 8 requests/second,
deliberately below the ceiling. Set it explicitly: if it is unset, `pipeline/config.py`
falls back to a hardcoded default, and the address sent to sec.gov on your behalf will be
someone else's.

```bash
export SEC_CONTACT="Your Name you@example.com"     # PowerShell: $env:SEC_CONTACT = "..."
```

**3. Run it.** The cohort freeze is a one-shot: its output is committed, and re-running it
after the freeze would let the cohort drift, which is what `METHOD.md` §3 exists to
prevent.

```bash
python -m pipeline.build_cohort     # enumerate, apply C1-C5, freeze
pytest                              # requires the dev extra
```

**4. The TLS note, because it will otherwise cost you an afternoon.** The machine this was
developed on sits behind a TLS-inspecting corporate proxy whose CA is present in the
Windows trust store but absent from certifi's bundle. `curl` therefore reaches sec.gov and
`requests` does not, which looks like an SEC outage and is not. `truststore` is a hard
dependency for that reason: `pipeline/__init__.py` calls `truststore.inject_into_ssl()` to
route Python's SSL through the operating system's trust store. The injection is
best-effort and a no-op where certifi already works, such as on Railway's Linux images.
**Certificate verification is never disabled.** If both paths fail, the fetch fails loudly.

Deployment — the Docker image, the Railway configuration, and what the site does and does
not serve — is in **[`docs/DEPLOY.md`](docs/DEPLOY.md)**.

**Continuous re-reading.** [`.github/workflows/watch.yml`](.github/workflows/watch.yml) runs
**every Monday at 06:17 UTC**, and on demand. It re-reads all 50 issuers' complete EDGAR
filing indexes, diffs them against a committed per-issuer high-water mark of accessions
already seen, classifies anything new as an adverse event under `METHOD.md` §7.2 — with
Amendment 1's two exclusions applied, so a warrant-wave restatement or a mechanical de-SPAC
delisting raises no alarm — reports any kill criterion whose date has passed, and commits
the new state and report. It reads `SEC_CONTACT` from a repository secret of the same name
(*Settings → Secrets and variables → Actions*), and it refuses to run without one.

What it is allowed to write is `data/derived/watch_state.json` and
`data/derived/watch_report.json`, and nothing else. It never edits `METHOD.md`, the frozen
cohort, or the adjudication ledger — those are the study, and a scheduled job has no
business touching them.

The result is published at **`/watch`**. Two things follow from running it, and they are the
reason it exists: the public record accrues whether or not the author touches this project,
and a position is graded by a machine reading filings rather than by its author deciding
whether he still believes it, which is what `METHOD.md` §9 means by *"evaluated by a
scheduled job … without the author's involvement"*. A changelog that keeps moving is
evidence the work was not theatre; one that stops the day something was submitted is
evidence that it was.

---

## Repository map

| Path | What it is |
|---|---|
| `METHOD.md` | The pre-registration. Written and tagged before any data was collected. |
| `CHANGELOG.md` | Every amendment to the method, dated, reasoned, append-only. |
| `pipeline/config.py` | Every parameter that governs the study, in one place. |
| `pipeline/edgar.py` | Rate-limited, caching EDGAR client. Records a SHA-256 for every document on the same code path as the fetch. |
| `pipeline/filings.py` | The complete filing index for an issuer, including the submissions overflow files a truncated read would miss. |
| `pipeline/cohort.py` | Enumeration and the frozen inclusion decision (§3). |
| `pipeline/build_cohort.py` | One-shot entry point: enumerate, apply C1–C5, freeze. |
| `pipeline/outcomes.py` | The adverse-event outcome and its two labelled exclusions (§7.2, Amendment 1). |
| `app/` | The published site: FastAPI, served from precomputed artifacts only. |
| `data/cohort/` | The frozen company list and the exclusions, with reasons. Committed. |
| `data/adjudication/` | The human ruling ledger. **This is the study.** Committed. |
| `data/manifest/` | SHA-256 of every document read. Committed. |
| `data/derived/` | Computed tables and the specification log. Committed. |
| `data/raw/` | The cached EDGAR corpus. Gitignored, excluded from the image, reproducible from the manifest. |
| `Dockerfile`, `.dockerignore`, `.railway/railway.ts` | The deployed image and its Railway configuration. |
| `docs/DEPLOY.md` | How the site is built and deployed. |
| `.github/workflows/watch.yml` | The weekly re-read. Runs Mondays 06:17 UTC. |
| `pipeline/watch.py` | The job itself: diff against the high-water mark, classify, report. |
| `data/derived/watch_state.json` | Accessions already seen, per issuer. Committed by the job. |
| `data/derived/watch_report.json` | What the last run found. Rendered at `/watch`. |

---

## Licence

**Code: MIT** — [`LICENSE`](LICENSE). Covers `pipeline/`, `app/`, `tests/`, the
`Dockerfile`, `.railway/` and `.github/workflows/`. Take the machinery and do what you
like with it.

**Research content: CC BY 4.0** — [`LICENSE-CONTENT`](LICENSE-CONTENT). Covers `METHOD.md`,
`CHANGELOG.md`, this file, `docs/`, everything under `data/` including the adjudication
ledger, and all published prose. Quote it and build on it with attribution.

The SEC filings this study reads are public records and are not covered by either licence.
What is licensed here is the selection, the adjudication and the writing about them.

One request that is not a condition: **if you quote a finding, quote its interval.** Every
published figure carries an explicit uncertainty, and a point estimate reproduced without
one says something this study does not.

---

*Author: Jex Lin. Pre-registered 28 August 2026, tagged `preregistration-v1` at `0a663cf`,
before data collection.*
