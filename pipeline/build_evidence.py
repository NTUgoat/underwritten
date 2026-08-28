"""Entry point: run METHOD.md §6 for every located metric, before adjudication.

This stage exists because §6 was defined and never invoked. That inverted the
design: the reviewer would have been asked to rule `DISCONTINUED` without the
mechanical evidence §6 requires in front of them, and a ruling made without it
is not the ruling the method describes.

The order matters and is deliberate:

    corpus -> candidates -> **evidence (here)** -> human adjudication -> analysis

The machine computes whether a phrase is absent for four consecutive reporting
periods across the *entire* filed corpus. That is a necessary condition for
`DISCONTINUED`, never a sufficient one, and this stage never writes a terminal
state. Airbnb's "Nights and Experiences Booked" scores ABSENCE_TEST_MET and was
not discontinued - it was renamed, and the new name first appears in an EX-99.1
exhibit. Only a human reading the filings can tell those apart, which is exactly
why this stage hands over evidence rather than a verdict.

Offline: reads the cached corpus, never the network.

    .venv/Scripts/python.exe -m pipeline.build_evidence
"""

from __future__ import annotations

import csv
import json
import sys
import time
import warnings
from collections import Counter, defaultdict

from bs4 import XMLParsedAsHTMLWarning

from . import config
from .build_candidates import MIN_CORPUS_COMPLETENESS
from .corpus import build as build_corpus
from .edgar import EdgarClient, OfflineCacheMiss
from .filings import complete_index
from .build_candidates import listing_documents
from .metrics import absence_test, metric_key, phrase_pattern

warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)

OUTPUT = config.DERIVED / "absence_evidence.json"


def _appearance(found) -> dict | None:
    """Serialise one Appearance, or None.

    Written out explicitly rather than through a `hasattr(..., "as_row")`
    guard. That guard was here first, `Appearance` has no `as_row` - only
    `AbsenceEvidence` does - and so every one of the 79 rows written silently
    carried `first_appearance: null`. Nothing failed and nothing was logged.
    Defensive coding that swallows the case it was guarding against is worse
    than no guard, because it converts a crash into missing data.

    These fields are what lets a reader re-check the claim by hand, which is
    the whole point of recording them.
    """
    if found is None:
        return None
    return {
        "accession": found.accession,
        "form": found.form,
        "filing_date": found.filing_date.isoformat(),
        "document": found.document,
        "doc_type": found.doc_type,
        "url": found.url,
        "char_offset": found.char_offset,
        "context": found.context,
        "match_mode": found.match_mode,
        "matched_text": found.matched_text,
    }


def _cohort_rows() -> list[dict[str, str]]:
    path = config.COHORT / "cohort_frozen.csv"
    if not path.exists():
        raise SystemExit(f"No frozen cohort at {path}.")
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def _candidates_by_cik() -> dict[int, dict[str, str]]:
    """Unique metric phrases per issuer, keyed by the normalised name.

    One entry per metric, not per occurrence - the same collapse the analysis
    applies to the ledger. The value keeps a spelling the issuer actually used,
    because that is what §6 searches for.
    """
    path = config.ADJUDICATION / "metrics_candidates.csv"
    if not path.exists():
        raise SystemExit(
            f"No candidates at {path}. Run `python -m pipeline.build_candidates`."
        )
    out: dict[int, dict[str, str]] = defaultdict(dict)
    with path.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            name = (row.get("metric_name") or "").strip()
            if not name:
                continue
            try:
                cik = int(row["cik"])
            except (KeyError, ValueError):
                continue
            key = metric_key(name)
            if key:
                out[cik].setdefault(key, name)
    return out


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    client = EdgarClient(offline=True)
    rows = _cohort_rows()
    candidates = _candidates_by_cik()

    print(f"Cohort: {len(rows)} issuers")
    print(f"METHOD.md §6: absence across {config.DISCONTINUATION_PERIODS} consecutive")
    print("reporting periods, over the entire filed corpus including exhibits.")
    print()

    evidence: list[dict] = []
    skipped: list[dict] = []
    statuses: Counter[str] = Counter()
    started = time.time()

    for i, row in enumerate(rows, start=1):
        cik = int(row["cik"])
        name = row["name"][:34]
        phrases = candidates.get(cik, {})
        if not phrases:
            skipped.append({"cik": cik, "name": row["name"], "reason": "no candidates"})
            continue

        try:
            # Built once and reused: the listing-document search below needs the
            # same index, and asking for it twice would double the work.
            index = complete_index(client, cik)
            corpus = build_corpus(client, cik, index)
        except OfflineCacheMiss:
            skipped.append({"cik": cik, "name": row["name"], "reason": "corpus not built"})
            print(f"[{i:>2}/{len(rows)}] {name:<34} corpus not built - skipped")
            continue

        expected = int(row.get("n_corpus_filings") or 0)
        if corpus.n_documents < max(1, int(expected * MIN_CORPUS_COMPLETENESS)):
            skipped.append(
                {
                    "cik": cik,
                    "name": row["name"],
                    "reason": "corpus incomplete",
                    "documents": corpus.n_documents,
                    "expected_filings": expected,
                }
            )
            print(f"[{i:>2}/{len(rows)}] {name:<34} corpus incomplete - skipped")
            continue

        # The listing document is not part of the §6 corpus, by design. But
        # METHOD.md §5 NEVER_REPORTED turns on whether a phrase occurs there and
        # nowhere afterwards, so it is searched separately for that one fact.
        try:
            listing_text = " ".join(
                d.text for d in listing_documents(client, index, row)
            )
        except (OfflineCacheMiss, OSError, ValueError, KeyError) as exc:
            # An issuer whose listing document is not in the cache still gets a
            # row: `in_listing_document` is False, `never_reported_eligible` is
            # False, and the §5 guard in `app/adjudicate.py` refuses the state
            # rather than granting it on absent evidence.
            #
            # The exception list is narrow ON PURPOSE. It was `except
            # Exception`, and the name `index` was undefined at this point, so
            # every issuer raised NameError here, every listing search returned
            # "", and all 946 rows were written with in_listing_document=False.
            # Nothing failed and nothing was logged - the same defect this
            # module's `_appearance` docstring already records once.
            print(
                f"    listing document unavailable for CIK {cik}: "
                f"{type(exc).__name__}: {exc}",
                flush=True,
            )
            listing_text = ""

        per_issuer: Counter[str] = Counter()
        for key, phrase in sorted(phrases.items()):
            # No aliases here. A rename is a HUMAN ruling (§6); once the
            # reviewer traces one, the test is re-run over the traced set.
            result = absence_test(corpus, phrase)

            # Purely factual, and deliberately narrow: does the phrase occur in
            # the listing document, and nowhere in anything filed afterwards?
            # That is the evidence for NEVER_REPORTED. It is NOT the state -
            # the phrase may have been renamed before the first report, or may
            # never have been a metric. A human rules, as on every other state.
            in_listing = bool(
                listing_text and phrase_pattern(phrase).search(listing_text)
            )
            never_reported_eligible = in_listing and result.n_appearances == 0

            per_issuer[result.status] += 1
            statuses[result.status] += 1
            if never_reported_eligible:
                statuses["(never-reported evidence)"] += 1
            evidence.append(
                {
                    "cik": cik,
                    "issuer": row["name"],
                    "arm": row["arm"],
                    "metric_key": key,
                    "phrase": phrase,
                    "status": result.status,
                    "reason": result.reason,
                    "required_periods": result.required_periods,
                    "n_periods": result.n_periods,
                    "presence_vector": list(result.presence_vector),
                    "trailing_absent_periods": result.trailing_absent_periods,
                    "max_absent_run": result.max_absent_run,
                    "n_appearances": result.n_appearances,
                    "n_documents_searched": result.n_documents_searched,
                    "n_documents_failed": result.n_documents_failed,
                    "in_listing_document": in_listing,
                    "never_reported_eligible": never_reported_eligible,
                    "first_appearance": _appearance(result.first_appearance),
                    "last_appearance": _appearance(result.last_appearance),
                }
            )

        print(
            f"[{i:>2}/{len(rows)}] {name:<34} {len(phrases):>3} metrics  "
            f"{dict(per_issuer)}",
            flush=True,
        )

    OUTPUT.write_text(
        json.dumps(
            {
                "as_at": config.AS_AT_DATE,
                "required_periods": config.DISCONTINUATION_PERIODS,
                "issuers_measured": len({e["cik"] for e in evidence}),
                "issuers_skipped": len(skipped),
                "metrics": len(evidence),
                "by_status": dict(statuses),
                "skipped": skipped,
                "evidence": evidence,
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    print()
    print(f"Metrics tested   : {len(evidence):,}")
    for status, count in statuses.most_common():
        print(f"  {status:<24} {count:>5}")
    print(f"Issuers skipped  : {len(skipped)}")
    print(f"Elapsed          : {(time.time() - started) / 60:.1f} min")
    print(f"Evidence         -> {OUTPUT}")
    print()
    print(
        "ABSENCE_TEST_MET is a necessary condition for DISCONTINUED, never a\n"
        "sufficient one. A metric can meet it and simply have been renamed -\n"
        "Airbnb's 'Nights and Experiences Booked' does exactly that. Every\n"
        "terminal state is still a human ruling."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
