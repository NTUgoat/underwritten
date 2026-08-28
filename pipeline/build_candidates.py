"""Entry point: locate metric candidates across the frozen cohort's corpus.

Runs offline against the cached corpus - no network, so it is safe to run while
a corpus build is still going, and cheap to re-run.

What this writes is a *worklist*, not a result. Every ruling column
(``include``, ``reviewer``, ``review_date``, ``rationale``) is left blank for a
human to fill, and existing rulings are carried forward by ``candidate_id`` so
re-running after more issuers finish never destroys work already done.

    .venv/Scripts/python.exe -m pipeline.build_candidates
"""

from __future__ import annotations

import csv
import json
import sys
import time
import warnings
from collections import Counter

from bs4 import XMLParsedAsHTMLWarning

from . import config
from .corpus import build as build_corpus
from .edgar import EdgarClient, OfflineCacheMiss
from .filings import complete_index
from .metrics import clean_metric_name, locate, write_candidates

warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)

# An issuer counts as measured only when its cached corpus reaches this fraction
# of the filing count recorded in the frozen cohort. Documents include exhibits
# and normally exceed the filing count, so this is a loose floor; it exists to
# reject half-built corpora, not to police coverage.
MIN_CORPUS_COMPLETENESS = 0.9


def _cohort_rows() -> list[dict[str, str]]:
    path = config.COHORT / "cohort_frozen.csv"
    if not path.exists():
        raise SystemExit(
            f"No frozen cohort at {path}. Run `python -m pipeline.build_cohort` first."
        )
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    # Offline: this stage reads the cache and must never compete with a
    # running corpus build for the SEC's fair-access budget.
    client = EdgarClient(offline=True)
    rows = _cohort_rows()

    print(f"Cohort: {len(rows)} issuers")
    print("Locating candidates from the cached corpus (no network).")
    print()

    all_candidates = []
    covered: list[dict] = []
    skipped: list[dict] = []
    started = time.time()

    for i, row in enumerate(rows, start=1):
        cik = int(row["cik"])
        name = row["name"][:36]
        try:
            index = complete_index(client, cik)
            corpus = build_corpus(client, cik, index)
            found = locate(corpus.documents)
        except OfflineCacheMiss:
            skipped.append(
                {"cik": cik, "name": row["name"], "reason": "corpus not built yet"}
            )
            print(f"[{i:>2}/{len(rows)}] {name:<36} not built yet - skipped")
            continue
        except Exception as exc:  # noqa: BLE001 - one issuer must not stop the run
            skipped.append({"cik": cik, "name": row["name"], "reason": str(exc)[:120]})
            print(f"[{i:>2}/{len(rows)}] {name:<36} SKIPPED  {type(exc).__name__}")
            continue

        # `corpus.build` catches a cache miss internally and records it as a gap
        # rather than raising, so an issuer whose corpus is missing or half-built
        # comes back as a VALID but incomplete corpus. Counting that as
        # "measured, no metrics found" would later be recorded as C4
        # NO_METRICS_DEFINED - a false zero that silently shrinks the §7.1
        # denominator.
        #
        # Testing for zero documents is not enough: Nikola came back with 1
        # document against 157 expected and would have passed. The frozen cohort
        # records the expected filing count per issuer, so completeness is
        # checked against that. Documents include exhibits and so normally
        # exceed the filing count; anything well below it is a partial build.
        expected = int(row.get("n_corpus_filings") or 0)
        threshold = int(expected * MIN_CORPUS_COMPLETENESS)
        if corpus.n_documents < max(1, threshold):
            skipped.append(
                {
                    "cik": cik,
                    "name": row["name"],
                    "reason": "corpus incomplete",
                    "documents": corpus.n_documents,
                    "expected_filings": expected,
                    "gaps": corpus.n_failures,
                }
            )
            print(
                f"[{i:>2}/{len(rows)}] {name:<36} incomplete - skipped "
                f"({corpus.n_documents} docs, {expected} filings expected)"
            )
            continue

        all_candidates.extend(found)
        groups = {
            (c.cik, clean_metric_name(c.metric_name).lower().strip()) for c in found
        }
        covered.append(
            {
                "cik": cik,
                "name": row["name"],
                "arm": row["arm"],
                "documents": corpus.n_documents,
                "occurrences": len(found),
                "groups": len(groups),
            }
        )
        print(
            f"[{i:>2}/{len(rows)}] {name:<36} "
            f"{corpus.n_documents:>4} docs  "
            f"{len(found):>5} occurrences  "
            f"{len(groups):>4} groups",
            flush=True,
        )

    path = write_candidates(all_candidates)

    total_groups = sum(c["groups"] for c in covered)
    summary = {
        "as_at": config.AS_AT_DATE,
        "issuers_measured": len(covered),
        "complete": len(skipped) == 0,
        "issuers_skipped": len(skipped),
        "occurrences": len(all_candidates),
        "groups_to_rule": total_groups,
        "by_arm": dict(Counter(c["arm"] for c in covered)),
        "per_issuer": covered,
        "skipped": skipped,
    }
    out = config.DERIVED / "candidates_summary.json"
    out.write_text(json.dumps(summary, indent=2), encoding="utf-8")

    print()
    print(f"Issuers measured  : {len(covered)} of {len(rows)}")
    if skipped:
        print(f"Issuers NOT built : {len(skipped)} - their corpus does not exist yet")
        print()
        print(
            "\n  THIS WORKLIST IS PARTIAL. An issuer with no corpus has not been"
            "\n  measured, and must not be recorded as NO_METRICS_DEFINED - that"
            "\n  would be a false zero in the denominator. Re-run this once the"
            "\n  corpus build finishes; existing rulings are carried forward.\n"
        )
    print(f"Occurrences       : {len(all_candidates):,}")
    print(f"GROUPS TO RULE    : {total_groups:,}")
    print(f"Elapsed           : {(time.time() - started) / 60:.1f} min")
    print(f"Worklist          -> {path}")
    print(f"Summary           -> {out}")
    print()
    print(
        "Every ruling column is blank. Rulings are made by hand - see "
        "docs/ADJUDICATION.md for how to run the session."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
