"""Entry point: fetch the full filed corpus for every frozen cohort member.

Long-running and network-bound, but resumable: `EdgarClient` caches every
document by URL, so re-running after an interruption re-reads from disk instead
of re-requesting. Interrupting this is safe.

Coverage is written per issuer to data/derived/corpus_coverage.json. It is a
published artifact, not a log: METHOD.md §6 can only call a metric absent if the
corpus was actually read, so the count of documents that could NOT be read is
evidence bearing directly on the finding and belongs on /provenance.

    .venv/Scripts/python.exe -m pipeline.build_corpus
"""

from __future__ import annotations

import csv
import json
import sys
import time
import warnings

from bs4 import XMLParsedAsHTMLWarning

from . import config
from .corpus import build
from .edgar import EdgarClient
from .filings import complete_index

warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)


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
    client = EdgarClient()
    rows = _cohort_rows()

    print(f"Cohort: {len(rows)} issuers")
    print(f"Corpus forms: {', '.join(config.CORPUS_FORMS)}")
    print("Exhibits: EX-99.x on 8-K and 6-K included (METHOD.md §6)")
    print()

    coverage: list[dict] = []
    started = time.time()

    for i, row in enumerate(rows, start=1):
        cik = int(row["cik"])
        name = row["name"][:38]
        t0 = time.time()
        try:
            index = complete_index(client, cik)
            corpus = build(client, cik, index)
        except Exception as exc:  # noqa: BLE001 - one issuer must not stop the run
            print(f"[{i:>2}/{len(rows)}] {name:<38} FAILED {type(exc).__name__}: {exc}")
            coverage.append(
                {"cik": cik, "name": row["name"], "error": f"{type(exc).__name__}: {exc}"}
            )
            continue

        stats = corpus.coverage()
        stats["name"] = row["name"]
        stats["arm"] = row["arm"]
        coverage.append(stats)

        print(
            f"[{i:>2}/{len(rows)}] {name:<38} "
            f"{stats['documents']:>4} docs "
            f"({stats['exhibits']:>3} exhibits) "
            f"{stats['periods']:>3} periods "
            f"{stats['failures']:>2} failed "
            f"{time.time() - t0:>5.0f}s",
            flush=True,
        )

    elapsed = time.time() - started
    out = config.DERIVED / "corpus_coverage.json"
    total_docs = sum(c.get("documents", 0) for c in coverage)
    total_failures = sum(c.get("failures", 0) for c in coverage)

    out.write_text(
        json.dumps(
            {
                "as_at": config.AS_AT_DATE,
                "issuers": len(coverage),
                "documents_read": total_docs,
                "documents_failed": total_failures,
                "elapsed_seconds": round(elapsed),
                "per_issuer": coverage,
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    print()
    print(f"Documents read   : {total_docs:,}")
    print(f"Documents failed : {total_failures:,}")
    print(f"Elapsed          : {elapsed / 60:.1f} min")
    print(f"Coverage         -> {out}")
    print(f"Manifest         -> {client.write_manifest('corpus_sources.json')}")

    if total_failures:
        print()
        print(
            "NOTE: unreadable documents are gaps in coverage, and a gap is evidence "
            "AGAINST calling a metric absent. They are published on /provenance and "
            "must be reflected in NOT_DETERMINABLE verdicts rather than ignored."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
