"""Entry point: check the cohort for new filings, and grade open positions.

This is the study's continuing operation, and it is deliberately not something
the author drives. Cohort companies file on a schedule nobody here controls, so
this job runs weekly, notices what is new, and evaluates every open kill
criterion against it.

Two things follow from that, and they are the point:

* The public record accrues whether or not the author touches the project. A
  changelog that keeps moving after an application was submitted is evidence the
  work was not theatre; one that goes quiet the day after is evidence it was.
* A position is graded by a machine reading filings, not by its author deciding
  whether he still believes it. METHOD.md §9 requires kill criteria to be
  "evaluated by a scheduled job against incoming filings without the author's
  involvement", and this is that job.

State is a per-issuer high-water mark of accessions already seen, committed so
the diff is against the last published check rather than against nothing.

    .venv/Scripts/python.exe -m pipeline.watch          # report only
    .venv/Scripts/python.exe -m pipeline.watch --write  # update the state file
"""

from __future__ import annotations

import csv
import json
import sys
import warnings
from datetime import date

from bs4 import XMLParsedAsHTMLWarning

from . import config
from .edgar import EdgarClient
from .filings import complete_index
from .outcomes import classify, counted

warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)

STATE = config.DERIVED / "watch_state.json"
REPORT = config.DERIVED / "watch_report.json"


def _cohort() -> list[dict[str, str]]:
    path = config.COHORT / "cohort_frozen.csv"
    if not path.exists():
        raise SystemExit(f"No frozen cohort at {path}.")
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def _load_state() -> dict[str, list[str]]:
    if not STATE.exists():
        return {}
    try:
        payload = json.loads(STATE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    seen = payload.get("seen_accessions") or {}
    return {str(k): list(v) for k, v in seen.items()} if isinstance(seen, dict) else {}


def _open_positions() -> list[dict]:
    path = config.DERIVED / "positions.json"
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    return [p for p in payload.get("positions") or [] if p.get("status") == "OPEN"]


def main(argv: list[str] | None = None) -> int:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    argv = list(sys.argv[1:] if argv is None else argv)
    write = "--write" in argv

    client = EdgarClient()
    rows = _cohort()
    state = _load_state()
    positions = _open_positions()

    print(f"Cohort: {len(rows)} issuers")
    print(f"Open positions: {len(positions)}")
    print(f"Previous state: {'none - first run' if not state else f'{len(state)} issuers'}")
    print()

    new_filings: list[dict] = []
    new_adverse: list[dict] = []
    next_state: dict[str, list[str]] = {}
    errors: list[dict] = []

    for row in rows:
        cik = str(int(row["cik"]))
        try:
            index = complete_index(client, int(cik))
        except Exception as exc:  # noqa: BLE001 - one issuer must not stop the run
            errors.append({"cik": cik, "name": row["name"], "error": str(exc)[:160]})
            # Carry the previous high-water mark forward. Dropping it would make
            # everything look new next week and manufacture a false burst.
            next_state[cik] = state.get(cik, [])
            continue

        accessions = [f.accession for f in index]
        next_state[cik] = accessions

        known = set(state.get(cik, []))
        if not state:
            # First run establishes the baseline. Reporting 50 issuers' entire
            # filing histories as "new" would be true and useless.
            continue

        fresh = [f for f in index if f.accession not in known]
        for filing in fresh:
            new_filings.append(
                {
                    "cik": cik,
                    "issuer": row["name"],
                    "form": filing.form,
                    "filed": filing.filing_date.isoformat(),
                    "accession": filing.accession,
                    "url": filing.index_url,
                }
            )

        if fresh:
            # Adverse-event classification honours Amendment 1's exclusions, so
            # a warrant-wave restatement or a mechanical de-SPAC delisting does
            # not raise an alarm here either.
            for event in counted(classify(client, fresh)):
                new_adverse.append(
                    {
                        "cik": cik,
                        "issuer": row["name"],
                        "reason": event.reason,
                        "form": event.filing.form,
                        "filed": event.filing.filing_date.isoformat(),
                        "accession": event.filing.accession,
                        "url": event.filing.index_url,
                    }
                )

    # Kill criteria. Dates are evaluated here; the substance of a criterion is
    # read by a human from the filings this job surfaces. A criterion whose date
    # has passed is reported as DUE, never silently resolved either way - the
    # machine says "this needs answering", the author answers it in public.
    today = date.fromisoformat(config.AS_AT_DATE)
    due: list[dict] = []
    for position in positions:
        for criterion in position.get("kill_criteria") or []:
            by = criterion.get("by_date") or ""
            try:
                deadline = date.fromisoformat(by)
            except ValueError:
                continue
            if deadline <= today and criterion.get("status") == "NOT_TRIGGERED":
                due.append(
                    {
                        "position": position.get("ticker") or position.get("id"),
                        "n": criterion.get("n"),
                        "by_date": by,
                        "test": criterion.get("test"),
                    }
                )

    report = {
        "checked_on": config.AS_AT_DATE,
        "baseline_established": not state,
        "issuers_checked": len(rows) - len(errors),
        "errors": errors,
        "new_filings": new_filings,
        "new_adverse_events": new_adverse,
        "kill_criteria_due": due,
        "open_positions": len(positions),
    }
    REPORT.write_text(json.dumps(report, indent=2), encoding="utf-8")

    if not state:
        print("Baseline established. Nothing is reported as new on a first run.")
    else:
        print(f"New filings        : {len(new_filings)}")
        for f in new_filings[:20]:
            print(f"   {f['filed']}  {f['form']:<8} {f['issuer'][:34]:<34} {f['accession']}")
        if len(new_filings) > 20:
            print(f"   ... and {len(new_filings) - 20} more (all in the report)")
        print(f"New adverse events : {len(new_adverse)}")
        for e in new_adverse:
            print(f"   {e['filed']}  {e['form']:<8} {e['issuer'][:34]:<34} {e['reason']}")
        print(f"Kill criteria due  : {len(due)}")
        for d in due:
            print(f"   {d['position']} #{d['n']} due {d['by_date']}: {d['test']}")

    if errors:
        print(f"\nIssuers not reached: {len(errors)} (previous state carried forward)")

    if write:
        STATE.write_text(
            json.dumps(
                {"checked_on": config.AS_AT_DATE, "seen_accessions": next_state},
                indent=2,
            ),
            encoding="utf-8",
        )
        print(f"\nState written -> {STATE}")
    else:
        print("\nState NOT written (pass --write to update the high-water mark).")

    print(f"Report        -> {REPORT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
