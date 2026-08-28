"""Entry point: run the pre-registered analysis and write what the site reads.

Resumable and cheap to re-run. Every EDGAR document is cached by `EdgarClient`,
so a second run reads from disk and touches sec.gov only for what is new.

    .venv/Scripts/python.exe -m pipeline.build_analysis
    .venv/Scripts/python.exe -m pipeline.build_analysis --offline

**The ledger comes first.** `data/adjudication/metrics.csv` is written by a
human and does not exist yet. Without it there is no terminal state, no
Keeper/Mover split, and nothing to score adverse events against - so this run
does not go near the network, writes a well-formed result marked
`available: false` with the reason on it, and exits 0. That is the normal state
of this stage until the reading work is done, and it is not an error.

Three files are written on every run, in the shapes `app/data.py` documents:

    data/derived/finding.json      METHOD.md §7.1 to §7.5
    data/derived/scoreboard.json   the one dense graphic
    data/derived/metrics.json      the ledger behind /cohort

and `data/derived/spec_log.csv` is appended to, never rewritten (§7.6).
"""

from __future__ import annotations

import argparse
import json
import sys
import warnings
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from bs4 import XMLParsedAsHTMLWarning

from . import config
from .analysis import (
    OUTCOME_NOT_RUN_REASON,
    OUTCOME_SKIPPED_REASON,
    AnalysisError,
    AnalysisInputs,
    CohortRow,
    Projection,
    append_spec_runs,
    assert_no_forbidden_words,
    forbidden_word_findings,
    read_cohort,
    read_ledger,
    read_projections,
    realised_annual_revenue,
    run,
    spec_line,
)
from .edgar import EdgarClient
from .filings import complete_index
from .outcomes import AdverseEvent, classify

warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)

FINDING_PATH = config.DERIVED / "finding.json"
SCOREBOARD_PATH = config.DERIVED / "scoreboard.json"
METRICS_PATH = config.DERIVED / "metrics.json"


def _write_json(path: Path, payload: Any) -> None:
    """Write one published file atomically. A half-written finding is not one."""
    staging = path.with_name(path.name + ".partial")
    staging.write_text(json.dumps(payload, indent=2, sort_keys=False), encoding="utf-8")
    staging.replace(path)


def _collect_events(
    client: EdgarClient, cohort: tuple[CohortRow, ...]
) -> tuple[dict[int, tuple[AdverseEvent, ...]], list[dict[str, str]]]:
    """Candidate adverse events per issuer, with exclusions labelled by §7.2.

    One issuer that cannot be read must not stop the run, but it must not be
    silently scored as "no adverse events" either - it is recorded as an error
    and published in `finding.json` under `outcome_variable.errors`.
    """
    events: dict[int, tuple[AdverseEvent, ...]] = {}
    errors: list[dict[str, str]] = []
    for index, member in enumerate(cohort, start=1):
        try:
            index_rows = complete_index(client, member.cik)
            events[member.cik] = tuple(classify(client, index_rows))
        except Exception as exc:  # noqa: BLE001 - one issuer must not stop the run
            errors.append(
                {
                    "stage": "§7.2 filing index",
                    "cik": str(member.cik),
                    "name": member.name,
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )
            print(
                f"[{index:>2}/{len(cohort)}] {member.name[:38]:<38} "
                f"FAILED {type(exc).__name__}: {exc}",
                flush=True,
            )
            continue
        counted = sum(1 for e in events[member.cik] if e.counts)
        excluded = len(events[member.cik]) - counted
        print(
            f"[{index:>2}/{len(cohort)}] {member.name[:38]:<38} "
            f"{len(index_rows):>5} filings  "
            f"{counted:>2} adverse  {excluded:>2} excluded",
            flush=True,
        )
    return events, errors


def _collect_realised_revenue(
    client: EdgarClient, projections: tuple[Projection, ...]
) -> tuple[dict[int, dict[str, float]], list[dict[str, str]]]:
    """Realised annual revenue for every issuer that has a transcribed projection."""
    realised: dict[int, dict[str, float]] = {}
    errors: list[dict[str, str]] = []
    for cik in sorted({p.cik for p in projections}):
        try:
            realised[cik] = realised_annual_revenue(client.company_facts(cik))
        except Exception as exc:  # noqa: BLE001
            errors.append(
                {
                    "stage": "§7.5 realised revenue",
                    "cik": str(cik),
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )
    return realised, errors


def main(argv: list[str] | None = None) -> int:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--offline",
        action="store_true",
        help=(
            "Do not touch EDGAR. The descriptive specifications still run; the "
            "confirmatory test is marked unavailable with that reason."
        ),
    )
    args = parser.parse_args(argv)

    try:
        cohort = read_cohort()
    except AnalysisError as exc:
        print(str(exc))
        return 1

    ledger = read_ledger(cohort_ciks=[row.cik for row in cohort])

    print(f"As-at          : {config.AS_AT_DATE}")
    print(f"Cohort         : {len(cohort)} issuers")
    print(f"Ledger         : {ledger.path} - {'read' if ledger.available else 'ABSENT'}")
    if ledger.available:
        print(
            f"                 {len(ledger.metric_rows)} adjudicated metrics, "
            f"{len(ledger.no_metrics_ciks)} issuer(s) with no metric defined"
        )
        if ledger.invalid:
            print(f"                 {len(ledger.invalid)} unparseable row(s), published as such")
        if ledger.off_cohort:
            print(f"                 {len(ledger.off_cohort)} row(s) outside the frozen cohort")
    else:
        print(f"                 {ledger.reason}")
    print(f"Bootstrap      : {config.BOOTSTRAP_RESAMPLES:,} resamples, seed {config.RANDOM_SEED}")
    print()

    events: dict[int, tuple[AdverseEvent, ...]] = {}
    outcome_errors: list[dict[str, str]] = []
    realised: dict[int, dict[str, float]] = {}
    try:
        projections = read_projections()
    except AnalysisError as exc:
        print(str(exc))
        return 1

    if not ledger.available:
        outcome_available = False
        outcome_reason = OUTCOME_SKIPPED_REASON
        print("Skipping the adverse-event extraction: nothing to score it against.")
    elif args.offline:
        outcome_available = False
        outcome_reason = OUTCOME_NOT_RUN_REASON + " This run was started with --offline."
        print("Running offline: the confirmatory test is marked unavailable.")
    else:
        client = EdgarClient()
        print("Extracting adverse filing events (METHOD.md §7.2, Amendment 1)...")
        events, outcome_errors = _collect_events(client, cohort)
        outcome_available = bool(events)
        outcome_reason = (
            ""
            if outcome_available
            else "No issuer's filing index could be read from EDGAR."
        )
        if projections:
            print()
            print(f"Reading realised revenue for {len({p.cik for p in projections})} issuer(s)...")
            realised, revenue_errors = _collect_realised_revenue(client, projections)
            outcome_errors.extend(revenue_errors)
        print()
        print(f"Manifest       -> {client.write_manifest('analysis_sources.json')}")

    print(
        f"Projections    : {len(projections)} hand-transcribed row(s)"
        + ("" if projections else " - §7.5 coverage is 0")
    )
    print()

    inputs = AnalysisInputs(
        as_at=config.AS_AT_DATE,
        generated=datetime.now(UTC).replace(microsecond=0).isoformat(),
        cohort=cohort,
        ledger=ledger,
        events=events,
        outcome_available=outcome_available,
        outcome_reason=outcome_reason,
        outcome_errors=tuple(outcome_errors),
        projections=projections,
        realised_revenue=realised,
    )

    try:
        analysis = run(inputs)
    except AnalysisError as exc:
        print(f"ANALYSIS FAILED: {exc}")
        return 1

    finding = dict(analysis.finding)
    scoreboard = dict(analysis.scoreboard)
    metrics = dict(analysis.metrics)

    # METHOD.md §7.2: the words cause, predicts and leads to do not appear in
    # the findings. Enforced before anything is written, so the rule fails a run
    # rather than reaching a reader.
    #
    # The rule governs the prose this study writes. Passages copied verbatim out
    # of the human ledger - benign labels, rationales, quoted defining sentences
    # - are the reviewer's record, and §4 requires them published unedited. A
    # reviewer writing "superseded by ASC 606, which caused ..." must not be
    # able to hard-fail a build and leave all three files unwritten. Those
    # regions are therefore reported rather than enforced, and the enforced
    # check still covers every string this module composes.
    try:
        for label, payload in (
            ("finding.json", finding),
            ("scoreboard.json", scoreboard),
            ("metrics.json", metrics),
        ):
            assert_no_forbidden_words(payload, label, skip_verbatim=True)
    except AnalysisError as exc:
        print(f"PUBLICATION GATE FAILED: {exc}")
        return 1

    for label, payload in (
        ("finding.json", finding),
        ("scoreboard.json", scoreboard),
        ("metrics.json", metrics),
    ):
        carried = forbidden_word_findings(payload, skip_verbatim=False)
        composed = forbidden_word_findings(payload, skip_verbatim=True)
        for offence in carried:
            if offence not in composed and "cause_of_death<key>" not in offence:
                print(
                    f"NOTE: {label} carries a reviewer's own wording that "
                    f"METHOD.md §7.2 keeps out of this study's prose - {offence}. "
                    "It is published unedited under §4 and is not a finding."
                )

    _write_json(FINDING_PATH, finding)
    _write_json(SCOREBOARD_PATH, scoreboard)
    _write_json(METRICS_PATH, metrics)

    try:
        total = append_spec_runs(analysis.spec_runs, timestamp=inputs.generated)
    except AnalysisError as exc:
        print(f"SPECIFICATION LOG FAILED: {exc}")
        return 1

    print(f"Finding        -> {FINDING_PATH}")
    print(f"Scoreboard     -> {SCOREBOARD_PATH}")
    print(f"Metrics        -> {METRICS_PATH}")
    print(f"Spec log       -> {config.DERIVED / 'spec_log.csv'}")
    print()
    print(spec_line(total))
    _report(finding)
    return 0


def _report(finding: dict[str, Any]) -> None:
    """Print what ran and what did not. An unavailable result is still a result."""
    print()
    for key, label in (
        ("base_rate", "§7.1 base rate"),
        ("primary_test", "§7.2 primary (#1)"),
        ("counter_test", "§7.3 counter-test"),
        ("sensitivity", "§7.4 sensitivity"),
        ("projection_arm", "§7.5 projection arm"),
    ):
        block = finding.get(key) or {}
        state = "available" if block.get("available") else "UNAVAILABLE"
        print(f"  {label:<22} {state}")
        if not block.get("available") and block.get("reason"):
            print(f"      {block['reason'][:150]}")

    if not finding.get("available"):
        print()
        print(
            "Nothing was fabricated in place of the missing rulings. Write "
            "data/adjudication/metrics.csv and re-run."
        )


if __name__ == "__main__":
    raise SystemExit(main())
