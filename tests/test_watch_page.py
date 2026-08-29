"""The live surface must report the committed run, and never more than it.

`/watch` is the only page on the site whose content changes without the author
touching anything, which makes it the one page most able to overstate itself.
Two failure modes are guarded here:

1. **Reporting activity that did not happen.** Every figure on the page is a
   count of something in `watch_report.json` or `watch_state.json`. If the page
   can show a number the committed report does not contain, the claim that the
   site renders only what the scheduled job wrote is false.
2. **Reading silence as absence of checking.** A week with no new filings and a
   week where the job never ran look identical if the page only prints zeroes.
   The page must distinguish them, so the tests do too.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from app import data, main

ROOT = Path(__file__).resolve().parent.parent
REPORT = ROOT / "data" / "derived" / "watch_report.json"
STATE = ROOT / "data" / "derived" / "watch_state.json"


@pytest.fixture(scope="module")
def client() -> TestClient:
    return TestClient(main.create_app())


@pytest.fixture(scope="module")
def report() -> dict:
    if not REPORT.is_file():
        pytest.skip("no watch report committed; run pipeline.watch")
    return json.loads(REPORT.read_text(encoding="utf-8"))


def test_the_page_serves(client: TestClient) -> None:
    assert client.get("/watch").status_code == 200


def test_the_watch_is_reachable_from_the_navigation() -> None:
    """A live surface nobody can find is not a live surface."""
    hrefs = {item["href"] for item in main.NAV}
    assert "/watch" in hrefs
    entry = next(item for item in main.NAV if item["href"] == "/watch")
    assert entry["tier"] == "study", "the watch belongs with the study, not the apparatus"


def test_status_reports_the_committed_run_and_nothing_else(report: dict) -> None:
    status = data.watch_status()
    assert status["checked_on"] == report["checked_on"]
    assert status["issuers_checked"] == report["issuers_checked"]
    assert len(status["new_filings"]) == len(report["new_filings"])
    assert len(status["adverse"]) == len(report["new_adverse_events"])
    assert len(status["kill_criteria_due"]) == len(report["kill_criteria_due"])
    assert len(status["errors"]) == len(report["errors"])


def test_the_high_water_mark_is_counted_not_estimated() -> None:
    """`accessions_tracked` is what separates "nothing new" from "nothing checked"."""
    if not STATE.is_file():
        pytest.skip("no watch state committed")
    seen = json.loads(STATE.read_text(encoding="utf-8"))["seen_accessions"]
    status = data.watch_status()
    assert status["issuers_tracked"] == len(seen)
    assert status["accessions_tracked"] == sum(len(v) for v in seen.values())


def test_a_quiet_week_says_it_checked(client: TestClient, report: dict) -> None:
    """Zero new filings must be published as a reading, not as a blank section."""
    if report["new_filings"]:
        pytest.skip("the last run found new filings; the quiet path is not exercised")
    body = client.get("/watch").text
    assert "Nothing new" in body
    status = data.watch_status()
    # The reader is told how much was looked at, so silence is legible.
    assert f"{status['accessions_tracked']:,}" in body
    assert f"{status['issuers_tracked']:,}" in body


def test_the_schedule_on_the_page_matches_the_workflow() -> None:
    """The page states when the job runs. It must not state a cron nobody set."""
    workflow = ROOT / ".github" / "workflows" / "watch.yml"
    if not workflow.is_file():
        pytest.skip("no workflow committed")
    assert data.WATCH_SCHEDULE["cron"] in workflow.read_text(encoding="utf-8")


def test_the_front_page_carries_the_live_figure(client: TestClient) -> None:
    """The study must not read as something that finished and stopped."""
    body = client.get("/").text
    assert "/watch" in body
    status = data.watch_status()
    assert f"{status['accessions_tracked']:,}" in body


def test_the_motive_claims_no_holding() -> None:
    """METHOD.md §11 requires disclosure of a personal position in a cohort company.

    The front page states why the study exists in the first person. The author
    holds nothing in the cohort, and the copy must keep saying so — a motive
    that quietly grew into an implied position would be exactly the kind of
    unprovenanced claim this project refuses.
    """
    why = main.SITE["why"].lower()
    assert "none of it is in these fifty companies" in why
    for forbidden in ("i own", "i hold", "my position in", "i am long"):
        assert forbidden not in why
