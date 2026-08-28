"""Entry point: fetch each cohort issuer's listing document.

Small and separate on purpose. METHOD.md §4 locates candidates in the listing
document; §6 measures the filed record *after* listing. Those are different
corpora, and `config.CORPUS_FORMS` deliberately excludes registration statements
so that a pre-listing document can never appear inside a §6 reporting period.

The consequence went unnoticed: the locator only ever saw the §6 corpus, so the
study was grading promises made at listing while never reading a listing
document. Lemonade's corpus is 109 documents, 82 of them 8-Ks, and not one S-1.
A metric defined in the prospectus and dropped before the first annual report -
exactly the case this study exists to find - could not be located at all.

So the listing documents are fetched here, into the same content-addressed
cache, and handed only to the locator. They never enter the absence corpus.

Roughly one request per issuer. Run it once, after the corpus build.

    .venv/Scripts/python.exe -m pipeline.fetch_listings
"""

from __future__ import annotations

import csv
import json
import sys
import time
import warnings

from bs4 import XMLParsedAsHTMLWarning

from . import config
from .build_candidates import listing_documents
from .edgar import EdgarClient
from .filings import complete_index

warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)

OUTPUT = config.DERIVED / "listing_documents.json"


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    path = config.COHORT / "cohort_frozen.csv"
    if not path.exists():
        raise SystemExit(f"No frozen cohort at {path}.")
    with path.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))

    # Online, unlike the other post-corpus stages: these documents are not in
    # the cache because nothing has ever asked for them.
    client = EdgarClient()
    print(f"Fetching the listing document for {len(rows)} issuers.")
    print("These are for candidate LOCATION only (METHOD.md §4); they never")
    print("enter the §6 absence corpus.")
    print()

    got: list[dict] = []
    missing: list[dict] = []
    started = time.time()

    for i, row in enumerate(rows, start=1):
        cik = int(row["cik"])
        name = row["name"][:36]
        try:
            index = complete_index(client, cik)
            documents = listing_documents(client, index, row)
        except Exception as exc:  # noqa: BLE001 - one issuer must not stop the run
            missing.append({"cik": cik, "name": row["name"], "reason": str(exc)[:120]})
            print(f"[{i:>2}/{len(rows)}] {name:<36} FAILED {type(exc).__name__}")
            continue

        if not documents:
            missing.append(
                {"cik": cik, "name": row["name"], "reason": "no registration statement on file"}
            )
            print(f"[{i:>2}/{len(rows)}] {name:<36} no registration statement")
            continue

        chars = sum(len(d.text) for d in documents)
        got.append(
            {
                "cik": cik,
                "name": row["name"],
                "arm": row["arm"],
                "accession": documents[0].accession,
                "form": documents[0].form,
                "filing_date": documents[0].filing_date.isoformat(),
                "document": documents[0].filename,
                "url": documents[0].url,
                "characters": chars,
            }
        )
        print(
            f"[{i:>2}/{len(rows)}] {name:<36} {documents[0].form:<6} "
            f"{documents[0].filing_date}  {chars:>9,} chars",
            flush=True,
        )

    OUTPUT.write_text(
        json.dumps(
            {
                "as_at": config.AS_AT_DATE,
                "fetched": len(got),
                "missing": len(missing),
                "complete": not missing,
                "documents": got,
                "not_found": missing,
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    print()
    print(f"Listing documents : {len(got)} of {len(rows)}")
    if missing:
        print(f"Not obtained      : {len(missing)} - recorded in the output")
    print(f"Elapsed           : {(time.time() - started) / 60:.1f} min")
    print(f"Index             -> {OUTPUT}")
    print(f"Manifest          -> {client.write_manifest('listing_sources.json', stage='listing-documents', covers='Registration statements read for METHOD.md section 4 candidate location. Not part of the section 6 absence corpus.')}")
    print()
    print("Now re-run `python -m pipeline.build_candidates` to locate against them.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
