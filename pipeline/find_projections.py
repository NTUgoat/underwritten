"""Entry point: locate the de-SPAC projection tables for hand transcription.

METHOD.md §7.5 is explicit that these are **hand-transcribed**, and the reason is
given there: "Hand transcription is deliberate: it is cheaper than parsing,
unimpeachable, and it is itself the reading work."

So this stage does not parse a projection. It finds the section, extracts the
surrounding text, and writes a worklist with everything the machine can
establish already filled in - issuer, accession, form, filing date, a link to the
document on sec.gov - and the four columns that require a human reading a table
left blank:

    fiscal_year, projected_revenue, page, caption

That division is the same one §4 draws for metrics. The machine narrows a
two-megabyte registration statement to a page; the person reads the page.

Offline: reads the cached listing documents, never the network.

    .venv/Scripts/python.exe -m pipeline.find_projections
"""

from __future__ import annotations

import csv
import re
import sys
import warnings

from bs4 import XMLParsedAsHTMLWarning

from . import config
from .build_candidates import listing_documents
from .edgar import EdgarClient, OfflineCacheMiss
from .filings import complete_index

warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)

WORKLIST = config.ADJUDICATION / "projections_worklist.csv"

#: The captions issuers actually use over a projection table. The first is the
#: standard one named in METHOD.md §7.5; the others are the common variants.
SECTION = re.compile(
    r"Certain\s+Unaudited\s+Prospective\s+Financial\s+Information"
    r"|Unaudited\s+Prospective\s+Financial\s+Information"
    r"|Projected\s+Financial\s+Information"
    r"|Certain\s+Financial\s+Projections"
    r"|Summary\s+of\s+.{0,30}Projections",
    re.IGNORECASE,
)

CONTEXT_CHARS = 2600

FIELDS = (
    # Established by the machine.
    "cik",
    "issuer",
    "accession",
    "form",
    "filing_date",
    "url",
    "section_found",
    "section_char_offset",
    "section_text",
    # Left blank. A person reads the table and fills these in.
    "fiscal_year",
    "projected_revenue",
    "page",
    "caption",
    "transcriber",
    "transcribed_date",
)


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    client = EdgarClient(offline=True)

    path = config.COHORT / "cohort_frozen.csv"
    if not path.exists():
        raise SystemExit(f"No frozen cohort at {path}.")
    with path.open(encoding="utf-8", newline="") as handle:
        rows = [r for r in csv.DictReader(handle) if r.get("arm") == "DESPAC"]

    print(f"de-SPAC arm: {len(rows)} issuers")
    print("Locating the projection section in each registration statement.")
    print()

    out: list[dict[str, str]] = []
    found = 0

    for i, row in enumerate(rows, start=1):
        cik = int(row["cik"])
        name = row["name"][:34]
        try:
            documents = listing_documents(client, complete_index(client, cik), row)
        except (OfflineCacheMiss, OSError, ValueError, KeyError):
            documents = ()

        if not documents:
            out.append(
                {
                    **{f: "" for f in FIELDS},
                    "cik": str(cik),
                    "issuer": row["name"],
                    "section_found": "no listing document cached",
                }
            )
            print(f"[{i:>2}/{len(rows)}] {name:<34} listing document not cached")
            continue

        document = documents[0]
        text = document.text
        matches = list(SECTION.finditer(text))
        base = {
            **{f: "" for f in FIELDS},
            "cik": str(cik),
            "issuer": row["name"],
            "accession": document.accession,
            "form": document.form,
            "filing_date": document.filing_date.isoformat(),
            "url": document.url,
        }

        if not matches:
            base["section_found"] = "no projection section located"
            out.append(base)
            print(f"[{i:>2}/{len(rows)}] {name:<34} no projection section")
            continue

        # The last occurrence is the section itself; earlier ones are usually
        # the table of contents.
        match = matches[-1]
        excerpt = " ".join(
            text[match.start() : match.start() + CONTEXT_CHARS].split()
        )
        base["section_found"] = "yes"
        base["section_char_offset"] = str(match.start())
        base["section_text"] = excerpt
        out.append(base)
        found += 1
        print(
            f"[{i:>2}/{len(rows)}] {name:<34} located at offset "
            f"{match.start():,} ({len(matches)} occurrence(s))"
        )

    WORKLIST.parent.mkdir(parents=True, exist_ok=True)
    with WORKLIST.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(FIELDS))
        writer.writeheader()
        writer.writerows(out)

    print()
    print(f"Section located : {found} of {len(rows)}")
    print(f"Worklist        -> {WORKLIST}")
    print()
    print(
        "Every projection column is blank. METHOD.md §7.5 requires these to be\n"
        "hand-transcribed with the accession and page, because that transcription\n"
        "is itself the reading work. Fill fiscal_year, projected_revenue, page and\n"
        "caption, then save as data/adjudication/projections.csv."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
