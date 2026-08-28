"""Loaders for everything the site publishes, and the expected schema of each file.

Design rules, in order of priority:

1. **Never fabricate.** No loader invents a number. When a file is absent the
   loader returns an unavailable :class:`Dataset` and the template renders a
   visible ``[pending adjudication]`` marker instead of a plausible figure.
2. **Never crash.** A malformed or half-written file degrades to unavailable
   with the parse error recorded, so the site still serves while the pipeline
   is mid-run.
3. **Never mutate.** Every loader returns freshly built objects. Nothing here
   caches across requests, because the pipeline writes these files underneath a
   running server.

--------------------------------------------------------------------------
EXPECTED FILES AND SCHEMAS
--------------------------------------------------------------------------

Written by the pipeline; none of them exist yet. Paths are relative to the
repository root. Committed files only - ``data/raw/`` is never read by the app.

``data/derived/finding.json`` - the front page. METHOD.md 7.1 to 7.4.

    {
      "as_at": "2026-08-28",                # study as-at date, not "today"
      "generated": "2026-08-28T09:41:00Z",
      "cohort": {
        "n_issuers": 47,                    # realised n, published either way
        "target_n": 50, "floor_n": 25,
        "floor_met": true,
        "n_metrics": 214,
        "n_keepers": 19, "n_movers": 28,
        "n_no_metrics_defined": 3           # C4 failures, denominator of 7.1
      },
      "finding": {"segments": [...]},       # see SEGMENTS below - the headline
      "base_rate": {                        # 7.1, exact binomial interval
        "denominator": 214,
        "states": [
          {"state": "ALIVE", "n": 118, "share": 0.551,
           "ci_low": 0.483, "ci_high": 0.618}
        ]
      },
      "counter_test": {                     # 7.3 - sits ABOVE the signal
        "verdict": "WEAKENED" | "SURVIVED" | "NOT_DETERMINABLE",
        "segments": [...],
        "improving":     {"n": 61, "discontinued": 14, "share": 0.230},
        "deteriorating": {"n": 58, "discontinued": 19, "share": 0.328},
        "difference_pp": 9.8
      },
      "sensitivity": {                      # 7.4 - benign causes removed
        "segments": [...],
        "primary": {"label": "As pre-registered", "value": 0.243},
        "benign_removed": {"label": "Benign causes removed", "value": 0.191},
        "delta_pp": -5.2,
        "benign_labels": ["ASC 280 segment reclassification", ...]
      },
      "primary_test": {                     # 7.2 - the signal, BELOW the above
        "keepers": {"n": 19, "adverse": 4, "share": 0.211},
        "movers":  {"n": 28, "adverse": 13, "share": 0.464},
        "difference_pp": 25.3,
        "bootstrap_ci_pp": [-1.4, 49.8],    # 10,000 resamples, seed 20260828
        "fisher_p": 0.11,
        "mann_whitney": {"u": 198.0, "p": 0.14},
        "power_note": "Underpowered for anything but a large effect."
      },
      "exclusions_applied": {               # Amendment 1, counted not dropped
        "SECTOR_WIDE_WARRANT_RESTATEMENT": 11,
        "MECHANICAL_DESPAC_DELISTING": 9
      }
    }

``data/derived/scoreboard.json`` - the one dense graphic on the front page.

    {
      "as_at": "2026-08-28",
      "fiscal_years": ["FY2019", ..., "FY2025"],
      "issuers": [
        {"cik": 1759546, "name": "...", "ticker": "RIDE", "arm": "DESPAC",
         "sector": "Motor Vehicles", "group": "MOVER",
         "cells": [{"fy": "FY2021", "state": "ALIVE", "n": 4, "note": "..."}]}
      ]
    }

    Cell ``state`` is one of ALIVE, REDEFINED, INTRODUCED, DISCONTINUED,
    NOT_DETERMINABLE, or NONE (issuer not yet listed / nothing filed that year).
    ``group`` is KEEPER or MOVER per METHOD.md 7.2.

``data/derived/metrics.json`` - the ledger behind /cohort, one entry per
adjudicated metric.

    {"metrics": [
      {"id": "1759546-adjusted-active-consumers",
       "cik": 1759546, "issuer": "...", "ticker": "...", "arm": "IPO",
       "sector": "Services-Prepackaged Software", "sic": "7372",
       "name": "Adjusted Active Consumers",
       "state": "DISCONTINUED",              # METHOD.md 5 terminal state
       "direction_at_last_report": "DETERIORATING",   # 7.3
       "first_appearance": <CITATION>,       # verbatim defining sentence
       "definition_changes": [
         {"date": "2023-02-28", "substantive": true,
          "before": <CITATION>, "after": <CITATION>, "rationale": "..."}
       ],
       "last_appearance": <CITATION>,        # last appearance ANYWHERE in corpus
       "absence_periods": 4,                 # 6, the four-period test
       "cause_of_death": {"label": "BENIGN_SEGMENT_RECLASSIFICATION",
                          "benign": true, "detail": "..."},
       "adjudication": {"initials": "JL", "date": "2026-08-28",
                        "rationale": "...",
                        "row": "data/adjudication/metrics.csv:42"}}
    ]}

``data/derived/note.json`` - the written note (/note).

    {"title": "...", "standfirst": "...", "author": "Jex Lin",
     "dated": "2026-08-28", "word_count": 2740,
     "sections": [{"id": "...", "heading": "...",
                   "blocks": [{"segments": [...]},          # a paragraph
                              {"pull": "..."},              # a pull quote
                              {"struck": {"segments": [...], "note": "...",
                                          "date": "..."}}]}],   # 8.5
     "revisions": [{"date": "...", "what": "...", "supersedes": "0a663cf"}]}

``data/derived/positions.json`` - live positions (/positions), METHOD.md 9.

    {"as_at": "...", "positions": [
      {"id": "...", "issuer": "...", "ticker": "...", "cik": 0,
       "status": "OPEN" | "HELD" | "KILLED",
       "opened": "2026-08-28", "author": "Jex Lin", "horizons_years": [10, 20],
       "stance": "Eight words, no more, stating the position.",
       "variant_perception": {"segments": [...]},
       "what_would_have_to_be_true": [{"segments": [...]}],
       "hurdle": {"total_bps": 1120, "edition": "Damodaran, January 2026",
                  "lines": [{"label": "US 10-year risk-free", "bps": 420,
                             "source": "...", "citation": <CITATION>}]},
       "expected_spread_bps": {"central": 350, "low": 120, "high": 640,
                               "basis": "spread over the hurdle above"},
       "downside": {"segments": [...]},
       "kill_criteria": [{"n": 1, "test": "...", "by_date": "2027-06-30",
                          "machine_checkable": true,
                          "status": "NOT_TRIGGERED", "last_checked": "..."}],
       "disclosure": "The author holds no position in ..."}]}

``data/derived/resolved.json`` - retrospective cases (/resolved), METHOD.md 8.1.

    {"cases": [
      {"id": "...", "issuer": "...", "cik": 0, "ticker": "...",
       "outcome": "RESTATEMENT" | "DELISTING" | "ENFORCEMENT" | "TAKE_PRIVATE",
       "resolved_on": "2023-06-27",
       "summary": {"segments": [...]},
       "what_the_metrics_showed": {"segments": [...]},
       "record": [<CITATION>]}]}

``data/derived/spec_log.csv`` - METHOD.md 7.6.
    columns: n, timestamp, specification, preregistered, result, notes

``data/cohort/cohort_frozen.csv`` - written by pipeline.cohort.freeze();
    columns exactly as pipeline.cohort.CohortMember.

``data/cohort/exclusions.csv`` - written by pipeline.cohort.freeze();
    columns exactly as pipeline.cohort.Exclusion.

``data/manifest/sources.json`` - written by pipeline.edgar.write_manifest().

    {"as_at": "...", "n_documents": 0,
     "documents": [{"url": "...", "local_path": "...", "sha256": "...",
                    "bytes": 0}]}

--------------------------------------------------------------------------
SEGMENTS - how published prose carries its citations
--------------------------------------------------------------------------

METHOD.md 8.3: every numeral in published prose resolves to an accession
number. Prose is therefore never a bare string. It is a list of segments:

    [{"text": "Of the "},
     {"figure": {"value": "214", ...CITATION...}},
     {"text": " metrics adjudicated, "}]

A CITATION is::

    {"value": "214",                       # what is printed in the prose
     "quote": "verbatim sentence from the filing, unedited",
     "form": "10-K", "filed": "2023-11-14",
     "accession": "0001104659-21-064530",
     "cik": 1759546,
     "url": "https://www.sec.gov/Archives/edgar/data/...",   # optional
     "page": "F-12"}                                          # optional

``url`` is derived from ``cik`` and ``accession`` when absent. A figure whose
citation lacks a quote or an accession is rendered as ``[pending adjudication]``
- an uncited numeral is never printed. That is the build-time gate of 8.3
expressed in the view layer.
"""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"

PENDING = "[pending adjudication]"
NOT_YET_RUN = "[not yet run]"

#: Terminal states, in the order they are displayed. METHOD.md 5.
METRIC_STATES = (
    "ALIVE",
    "REDEFINED",
    "RENAMED",
    "ABSORBED",
    "DISCONTINUED",
    "NOT_DETERMINABLE",
)

#: Scoreboard cell states: the above, plus INTRODUCED and NONE.
CELL_STATES = (
    "ALIVE",
    "REDEFINED",
    "INTRODUCED",
    "DISCONTINUED",
    "NOT_DETERMINABLE",
    "NONE",
)

CELL_LEGEND = {
    "ALIVE": "reported, definition unchanged",
    "REDEFINED": "still reported, definition substantively changed",
    "INTRODUCED": "first defined in this year",
    "DISCONTINUED": "absent from the whole corpus for four periods",
    "NOT_DETERMINABLE": "the filed record does not settle it",
    "NONE": "no annual report covering this year",
}


# --------------------------------------------------------------------------
# result types
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Source:
    """Where a dataset was meant to come from, and whether it was there."""

    path: str
    exists: bool
    error: str = ""

    @property
    def status(self) -> str:
        if self.error:
            return "unreadable"
        return "present" if self.exists else "not yet written"


@dataclass(frozen=True)
class Dataset:
    """A loaded file, or a clearly-marked absence. Never a fabrication."""

    source: Source
    payload: Any = None
    rows: list[dict[str, str]] = field(default_factory=list)

    @property
    def available(self) -> bool:
        return self.source.exists and not self.source.error

    def get(self, key: str, default: Any = None) -> Any:
        if isinstance(self.payload, dict):
            return self.payload.get(key, default)
        return default


def _relative(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT)).replace("\\", "/")
    except ValueError:  # pragma: no cover - path outside the repo
        return str(path)


# --------------------------------------------------------------------------
# primitive loaders
# --------------------------------------------------------------------------


def load_json(relative_path: str) -> Dataset:
    """Read one JSON file under data/. A missing or broken file is an absence."""
    path = DATA / relative_path
    if not path.is_file():
        return Dataset(Source(_relative(path), exists=False))
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        return Dataset(
            Source(_relative(path), exists=True, error=f"{type(exc).__name__}: {exc}")
        )
    return Dataset(Source(_relative(path), exists=True), payload=payload)


def load_csv(relative_path: str) -> Dataset:
    """Read one CSV file under data/ into a list of dicts, keeping column order."""
    path = DATA / relative_path
    if not path.is_file():
        return Dataset(Source(_relative(path), exists=False))
    try:
        with path.open(newline="", encoding="utf-8") as handle:
            rows = [dict(row) for row in csv.DictReader(handle)]
    except (OSError, UnicodeDecodeError, csv.Error) as exc:
        return Dataset(
            Source(_relative(path), exists=True, error=f"{type(exc).__name__}: {exc}")
        )
    return Dataset(Source(_relative(path), exists=True), payload=rows, rows=rows)


def load_text(path: Path) -> Dataset:
    """Read one text document from the repo root (METHOD.md, CHANGELOG.md)."""
    if not path.is_file():
        return Dataset(Source(_relative(path), exists=False))
    try:
        body = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        return Dataset(
            Source(_relative(path), exists=True, error=f"{type(exc).__name__}: {exc}")
        )
    return Dataset(Source(_relative(path), exists=True), payload=body)


# --------------------------------------------------------------------------
# named loaders - one per published file
# --------------------------------------------------------------------------


def finding() -> Dataset:
    return load_json("derived/finding.json")


def scoreboard() -> Dataset:
    return load_json("derived/scoreboard.json")


def metrics() -> Dataset:
    return load_json("derived/metrics.json")


def note() -> Dataset:
    return load_json("derived/note.json")


def positions() -> Dataset:
    return load_json("derived/positions.json")


def resolved() -> Dataset:
    return load_json("derived/resolved.json")


def spec_log() -> Dataset:
    return load_csv("derived/spec_log.csv")


def cohort_frozen() -> Dataset:
    return load_csv("cohort/cohort_frozen.csv")


def cohort_exclusions() -> Dataset:
    return load_csv("cohort/exclusions.csv")


def adjudication_ledger() -> Dataset:
    return load_csv("adjudication/metrics.csv")


def cohort_funnel() -> Dataset:
    """Candidates enumerated, accepted, and rejected by criterion. METHOD.md 3."""
    return load_json("derived/cohort_funnel.json")


def corpus_coverage() -> Dataset:
    """What was read per issuer, and what could not be.

    Published because a coverage gap is a fact about the FINDING, not about the
    infrastructure. METHOD.md §6 turns absence into DISCONTINUED, so a document
    that could not be read inside the window where a metric looks absent is
    exactly the difference between DISCONTINUED and NOT_DETERMINABLE. A reader
    who cannot see which documents are missing cannot disagree with a verdict
    that depends on them.
    """
    return load_json("derived/corpus_coverage.json")


def manifest() -> Dataset:
    """Every manifest in data/manifest/, merged and deduplicated by URL.

    The retrieval client writes one manifest per stage of the pipeline
    (``cohort_sources.json`` and any later ones), so the provenance page reads
    the whole directory rather than a single hardcoded filename. A manifest that
    is unreadable is reported, never skipped silently.
    """
    directory = DATA / "manifest"
    files = sorted(directory.glob("*.json")) if directory.is_dir() else []
    if not files:
        return Dataset(Source("data/manifest/*.json", exists=False))

    documents: dict[str, dict[str, Any]] = {}
    as_at_values: list[str] = []
    errors: list[str] = []
    stages: list[dict[str, Any]] = []

    for path in files:
        loaded = load_json(f"manifest/{path.name}")
        if not loaded.available:
            errors.append(f"{path.name}: {loaded.source.error}")
            continue
        payload = loaded.payload if isinstance(loaded.payload, dict) else {}
        as_at = str(payload.get("as_at") or "").strip()
        if as_at:
            as_at_values.append(as_at)
        # Each manifest declares the stage that produced it. The page names the
        # stages present rather than publishing one stage's count as though it
        # were the whole manifest - METHOD.md §11 promises hashes for every
        # retrieved document, and overstating coverage on the provenance page
        # is the one place this study cannot afford to be loose.
        stages.append(
            {
                "file": path.name,
                "stage": str(payload.get("stage") or path.stem),
                "covers": str(payload.get("covers") or ""),
                "n_documents": len(payload.get("documents") or []),
            }
        )
        for document in payload.get("documents") or []:
            if isinstance(document, dict) and document.get("url"):
                documents.setdefault(str(document["url"]), document)

    merged = sorted(documents.values(), key=lambda row: str(row.get("url", "")))
    label = f"data/manifest/*.json ({len(files)} file{'' if len(files) == 1 else 's'})"
    # A manifest that fails to parse is only fatal if it was the only one.
    fatal = "; ".join(errors) if errors and not merged else ""
    return Dataset(
        Source(label, exists=True, error=fatal),
        payload={
            "as_at": max(as_at_values) if as_at_values else "",
            "n_documents": len(merged),
            "documents": merged,
            "files": [path.name for path in files],
            "stages": sorted(stages, key=lambda s: s["stage"]),
            "errors": errors,
        },
    )


def method_document() -> Dataset:
    return load_text(ROOT / "METHOD.md")


def changelog_document() -> Dataset:
    return load_text(ROOT / "CHANGELOG.md")


# --------------------------------------------------------------------------
# derived views - all pure, all returning new objects
# --------------------------------------------------------------------------


def metric_list(dataset: Dataset) -> list[dict[str, Any]]:
    """The metric ledger as a list, whichever shape the file took."""
    payload = dataset.payload
    if isinstance(payload, dict):
        rows = payload.get("metrics", [])
    elif isinstance(payload, list):
        rows = payload
    else:
        rows = []
    return [row for row in rows if isinstance(row, dict)]


def facets(rows: list[dict[str, Any]]) -> dict[str, list[str]]:
    """Distinct sector and arm values present in the ledger, sorted."""
    sectors = sorted({str(r.get("sector") or "").strip() for r in rows} - {""})
    arms = sorted({str(r.get("arm") or "").strip() for r in rows} - {""})
    return {"sectors": sectors, "arms": arms}


def cohort_facets(rows: list[dict[str, str]]) -> dict[str, list[str]]:
    """Sector and arm values present in the frozen cohort CSV.

    The sector is the SIC description EDGAR itself assigns. No hand-built
    taxonomy sits between the reader and the filing.
    """
    sectors = sorted({str(r.get("sic_description") or "").strip() for r in rows} - {""})
    arms = sorted({str(r.get("arm") or "").strip() for r in rows} - {""})
    return {"sectors": sectors, "arms": arms}


def merge_facets(*sets: dict[str, list[str]]) -> dict[str, list[str]]:
    """Union of several facet dictionaries, as a new dictionary."""
    sectors: set[str] = set()
    arms: set[str] = set()
    for facet in sets:
        sectors.update(facet.get("sectors", []))
        arms.update(facet.get("arms", []))
    return {"sectors": sorted(sectors), "arms": sorted(arms)}


def filter_issuers(
    rows: list[dict[str, str]], *, sector: str = "", arm: str = ""
) -> list[dict[str, str]]:
    """The frozen cohort narrowed by the same two facets the ledger uses."""

    def keep(row: dict[str, str]) -> bool:
        if sector and str(row.get("sic_description") or "") != sector:
            return False
        return not (arm and str(row.get("arm") or "") != arm)

    return [row for row in rows if keep(row)]


def filter_metrics(
    rows: list[dict[str, Any]],
    *,
    sector: str = "",
    arm: str = "",
    state: str = "",
) -> list[dict[str, Any]]:
    """A new filtered list. An empty filter value means 'no constraint'."""

    def keep(row: dict[str, Any]) -> bool:
        if sector and str(row.get("sector") or "") != sector:
            return False
        if arm and str(row.get("arm") or "") != arm:
            return False
        return not (state and str(row.get("state") or "") != state)

    return [row for row in rows if keep(row)]


def find_metric(rows: list[dict[str, Any]], metric_id: str) -> dict[str, Any] | None:
    if not metric_id:
        return None
    return next((row for row in rows if str(row.get("id") or "") == metric_id), None)


def state_tally(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Counts by terminal state over the rows in hand.

    A count of loaded rows, not a published statistic: the published base rate
    with its exact binomial interval comes from finding.json and nowhere else.
    """
    counts = {state: 0 for state in METRIC_STATES}
    for row in rows:
        state = str(row.get("state") or "")
        if state in counts:
            counts[state] += 1
    total = sum(counts.values())
    return [
        {"state": state, "n": n, "share": (n / total) if total else None}
        for state, n in counts.items()
    ]


def sec_url(citation: Any) -> str:
    """Best available sec.gov link for a citation, or '' if none can be built."""
    if not isinstance(citation, dict):
        return ""
    explicit = str(citation.get("url") or "").strip()
    if explicit:
        return explicit
    accession = str(citation.get("accession") or "").strip()
    cik = str(citation.get("cik") or "").strip()
    if not accession:
        return ""
    if not cik.isdigit():
        return f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={cik}"
    return (
        f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/"
        f"{accession.replace('-', '')}/{accession}-index.htm"
    )


def citation_is_complete(citation: Any) -> bool:
    """A citation may be printed only if it resolves to a filed document.

    METHOD.md 8.3 fails the build for an uncited numeral. In the view layer the
    same rule renders the numeral as ``[pending adjudication]`` rather than
    printing a figure the reader cannot check.
    """
    if not isinstance(citation, dict):
        return False
    has_value = bool(str(citation.get("value") or "").strip())
    has_quote = bool(str(citation.get("quote") or "").strip())
    has_doc = bool(str(citation.get("accession") or "").strip()) or bool(
        str(citation.get("url") or "").strip()
    )
    return has_value and has_quote and has_doc


def sources_for(*datasets: Dataset) -> list[Source]:
    """The provenance strip: which files a page was rendered from."""
    seen: dict[str, Source] = {}
    for dataset in datasets:
        seen.setdefault(dataset.source.path, dataset.source)
    return list(seen.values())
