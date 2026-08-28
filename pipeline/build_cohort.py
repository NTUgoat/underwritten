"""Entry point: enumerate candidates, apply C1-C5, freeze the cohort.

Run once. The output is committed and is not regenerated casually - re-running
after the freeze would let the cohort drift, which is the thing METHOD.md §3 is
written to prevent.

    .venv/Scripts/python.exe -m pipeline.build_cohort
"""

from __future__ import annotations

import json
import sys
import warnings
from collections import Counter

from bs4 import XMLParsedAsHTMLWarning

from . import config
from .cohort import build, enumerate_candidates, freeze
from .edgar import EdgarClient

warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    client = EdgarClient()

    print(f"Listing window : {config.LISTING_WINDOW_START} to {config.LISTING_WINDOW_END}")
    print(f"Target n       : {config.COHORT_TARGET_N} (floor {config.COHORT_FLOOR_N})")
    print(f"As-at          : {config.AS_AT_DATE}")
    print()

    print("Enumerating candidates from EDGAR full-text search...", flush=True)
    candidates = enumerate_candidates(client)
    print(f"  {len(candidates)} distinct issuers, sorted by CIK ascending")
    arms = Counter(c.arm for c in candidates)
    print(f"  by arm: {dict(arms)}")
    print()

    print("Assessing in CIK order until the target is reached...", flush=True)
    members, exclusions = build(client, candidates)

    print()
    print(f"ACCEPTED : {len(members)}")
    print(f"REJECTED : {len(exclusions)}")
    failed = Counter(e.failed for e in exclusions)
    for criterion, count in sorted(failed.items()):
        print(f"    {criterion}: {count}")

    cohort_path, exclusions_path = freeze(members, exclusions)
    print()
    print(f"Frozen  -> {cohort_path}")
    print(f"Funnel  -> {exclusions_path}")

    # The funnel summary is published on /method, so it is written as data
    # rather than left in a terminal scrollback.
    summary = {
        "as_at": config.AS_AT_DATE,
        "listing_window": [config.LISTING_WINDOW_START, config.LISTING_WINDOW_END],
        "target_n": config.COHORT_TARGET_N,
        "floor_n": config.COHORT_FLOOR_N,
        "candidates_enumerated": len(candidates),
        "candidates_by_arm": dict(arms),
        "accepted": len(members),
        "accepted_by_arm": dict(Counter(m.arm for m in members)),
        "rejected": len(exclusions),
        "rejected_by_criterion": dict(failed),
        "met_target": len(members) >= config.COHORT_TARGET_N,
        "met_floor": len(members) >= config.COHORT_FLOOR_N,
    }
    summary_path = config.DERIVED / "cohort_funnel.json"
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(f"Summary -> {summary_path}")

    manifest_path = client.write_manifest("cohort_sources.json")
    print(f"Manifest-> {manifest_path} ({len(client.manifest_rows())} documents)")

    if len(members) < config.COHORT_FLOOR_N:
        print()
        print(
            f"WARNING: {len(members)} members is below the pre-registered floor of "
            f"{config.COHORT_FLOOR_N}. METHOD.md §3 requires this be published as a "
            f"null result with the shortfall stated."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
