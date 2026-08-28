"""Tests for the local adjudication tool.

Three things are being defended here, in descending order of importance.

1. **The integrity claim.** The published site says every ruling is a human
   judgment, signed and dated. So: no route writes a ruling without a verdict,
   initials and a rationale supplied on that request; a deferral is never
   written as an exclusion; and there is no bulk path that could commit a row
   nobody looked at.
2. **The blast radius.** These routes write to disk. They must not exist unless
   ``UNDERWRITTEN_ADJUDICATE=1`` is set, and they must not be able to write
   anywhere but the adjudication directory.
3. **The time saving, which is the point of the tool.** Occurrences collapse
   into one decision per (issuer, normalised metric name), one ruling writes
   through to every occurrence, and resuming skips what is already ruled.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from app import adjudicate, main
from pipeline import config as pipeline_config
from pipeline import metrics as pipeline_metrics

# ---------------------------------------------------------------------------
# synthetic corpus
# ---------------------------------------------------------------------------

SENTENCE_A = (
    "We define Adjusted Active Consumers as the number of unique consumer "
    "accounts that placed at least one order in the trailing twelve months."
)
SENTENCE_B = (
    "We define Adjusted Active Consumers as the number of unique consumer "
    "accounts that placed at least two orders in the trailing three months, "
    "excluding accounts acquired through partnerships."
)
SENTENCE_GBV = (
    "We define Gross Booking Value as the total dollar value of bookings "
    "processed on our platform before deductions."
)
SENTENCE_NI = "We define net income as the residual after all expenses."
HEADING_TEXT = "Key Operating Metrics The table below sets out the measures management reviews."


def _candidate(
    *,
    cik: int,
    accession: str,
    form: str,
    filing_date: str,
    name: str,
    sentence: str,
    locator: str = "we_define",
    document: str = "doc.htm",
    offset: int = 100,
) -> pipeline_metrics.MetricCandidate:
    return pipeline_metrics.MetricCandidate(
        candidate_id=pipeline_metrics.candidate_id(cik, accession, document, locator, offset),
        cik=cik,
        accession=accession,
        form=form,
        filing_date=filing_date,
        document=document,
        doc_type="EX-99.1" if form in {"8-K", "6-K"} else form,
        locator=locator,
        metric_name=name,
        defining_sentence=sentence,
        char_offset=offset,
        sentence_char_offset=offset - 10,
        url=(
            f"https://www.sec.gov/Archives/edgar/data/{cik}/"
            f"{accession.replace('-', '')}/{document}"
        ),
    )


def _corpus() -> tuple[pipeline_metrics.MetricCandidate, ...]:
    """Four decisions across two issuers, from six located occurrences."""
    return (
        # One metric, three occurrences, three spellings, two definitions.
        _candidate(
            cik=1000,
            accession="0001000-19-000001",
            form="S-1",
            filing_date="2019-05-01",
            name="Adjusted Active Consumers",
            sentence=SENTENCE_A,
            offset=100,
        ),
        _candidate(
            cik=1000,
            accession="0001000-21-000002",
            form="10-K",
            filing_date="2021-03-01",
            name="adjusted   active consumers,",
            sentence=SENTENCE_A,
            offset=200,
        ),
        _candidate(
            cik=1000,
            accession="0001000-23-000003",
            form="10-K",
            filing_date="2023-03-01",
            name="Adjusted Active Consumers",
            sentence=SENTENCE_B,
            offset=300,
        ),
        # A GAAP measure the locator picked up anyway.
        _candidate(
            cik=1000,
            accession="0001000-19-000001",
            form="S-1",
            filing_date="2019-05-01",
            name="net income",
            sentence=SENTENCE_NI,
            offset=400,
        ),
        # A heading, which is a pointer at a scoreboard and not a metric.
        _candidate(
            cik=1000,
            accession="0001000-19-000001",
            form="S-1",
            filing_date="2019-05-01",
            name="Key Operating Metrics",
            sentence=HEADING_TEXT,
            locator="heading_key_operating_metrics",
            offset=500,
        ),
        # A second issuer.
        _candidate(
            cik=2000,
            accession="0002000-20-000001",
            form="F-1",
            filing_date="2020-02-01",
            name="Gross Booking Value",
            sentence=SENTENCE_GBV,
            offset=100,
        ),
        _candidate(
            cik=2000,
            accession="0002000-22-000002",
            form="20-F",
            filing_date="2022-04-01",
            name="Gross Booking Value",
            sentence=SENTENCE_GBV,
            offset=150,
        ),
    )


@pytest.fixture()
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A temp adjudication directory holding a synthetic candidate file."""
    monkeypatch.setenv(adjudicate.DIR_FLAG, str(tmp_path))
    monkeypatch.delenv(adjudicate.REVIEWER_FLAG, raising=False)
    adjudicate._CACHE.clear()
    adjudicate._document_text.cache_clear()
    pipeline_metrics.write_candidates(_corpus(), path=tmp_path / adjudicate.CANDIDATES_NAME)
    return tmp_path


@pytest.fixture()
def client(workspace: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv(adjudicate.ENABLE_FLAG, "1")
    return TestClient(main.create_app())


def _ledger(workspace: Path) -> list[dict[str, str]]:
    path = workspace / adjudicate.LEDGER_NAME
    if not path.is_file():
        return []
    with path.open(newline="", encoding="utf-8") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


def _group_for(name: str, order: str = "issuer") -> adjudicate.Group:
    board = adjudicate.load_board(order)
    key = adjudicate.normalise_name(name)
    for group in board.groups:
        if group.normalised == key:
            return group
    raise AssertionError(f"no group for {name!r}")


def _rule(client: TestClient, group: adjudicate.Group, **fields: str):
    payload = {
        "verdict": adjudicate.INCLUDE,
        "reviewer": "JL",
        "rationale": "Company-defined and quantitative (§4).",
        "rationale_source": "preset:1",
    }
    payload.update(fields)
    return client.post(f"/adjudicate/{group.gid}", json=payload)


# ===========================================================================
# 1. The switch: these routes do not exist unless the flag is set
# ===========================================================================


def test_routes_are_absent_when_the_flag_is_unset(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(adjudicate.ENABLE_FLAG, raising=False)
    unguarded = TestClient(main.create_app())

    for path in ("/adjudicate", "/adjudicate/done", "/adjudicate/anything"):
        assert unguarded.get(path).status_code == 404, path
    assert unguarded.post("/adjudicate/anything", json={}).status_code == 404


@pytest.mark.parametrize("value", ["", "0", "true", "yes", "2", " 1 x"])
def test_only_the_exact_string_one_enables_the_tool(
    workspace: Path, monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    monkeypatch.setenv(adjudicate.ENABLE_FLAG, value)
    assert adjudicate.is_enabled() is False
    assert TestClient(main.create_app()).get("/adjudicate").status_code == 404


def test_routes_are_present_when_the_flag_is_set(client: TestClient) -> None:
    landing = client.get("/adjudicate", follow_redirects=False)
    assert landing.status_code == 303
    assert landing.headers["location"].startswith("/adjudicate/")
    assert client.get("/adjudicate").status_code == 200
    assert client.get("/adjudicate/done").status_code == 200


def test_the_public_site_is_untouched_when_the_tool_is_mounted(client: TestClient) -> None:
    assert client.get("/healthz").json() == {"status": "ok"}
    assert client.get("/").status_code == 200


# ===========================================================================
# 2. Blast radius: nothing is written outside the adjudication directory
# ===========================================================================


@pytest.mark.parametrize(
    "bad",
    ["../escape.csv", "a/b.csv", "/absolute.csv", "", ".", "..", "sub\\evil.csv"],
)
def test_write_target_refuses_anything_but_a_plain_filename(
    workspace: Path, bad: str
) -> None:
    with pytest.raises(ValueError):
        adjudicate._write_target(bad)


def test_write_target_resolves_inside_the_adjudication_directory(workspace: Path) -> None:
    assert adjudicate.ledger_path().parent == workspace.resolve()
    assert adjudicate.log_path().parent == workspace.resolve()


def test_a_ruling_only_ever_writes_the_two_permitted_filenames(
    client: TestClient, workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every write goes through _write_target, and only with a literal name."""
    seen: list[str] = []
    original = adjudicate._write_target

    def recording(filename: str) -> Path:
        seen.append(filename)
        return original(filename)

    monkeypatch.setattr(adjudicate, "_write_target", recording)
    assert _rule(client, _group_for("Gross Booking Value")).status_code == 200
    assert set(seen) <= {adjudicate.LEDGER_NAME, adjudicate.LOG_NAME}
    assert adjudicate.LEDGER_NAME in seen


def test_a_traversal_group_id_is_a_404_and_writes_nothing(
    client: TestClient, workspace: Path
) -> None:
    before = sorted(p.name for p in workspace.iterdir())
    for gid in ("..%2F..%2Fevil", "....//evil", "0" * 16):
        assert client.get(f"/adjudicate/{gid}").status_code == 404
        assert client.post(f"/adjudicate/{gid}", json={"verdict": "INCLUDE"}).status_code == 404
    assert sorted(p.name for p in workspace.iterdir()) == before
    assert not (workspace.parent / "evil").exists()


def test_reading_a_group_writes_nothing(client: TestClient, workspace: Path) -> None:
    client.get("/adjudicate")
    client.get(f"/adjudicate/{_group_for('Gross Booking Value').gid}")
    client.get("/adjudicate/done")
    assert not (workspace / adjudicate.LEDGER_NAME).exists()
    assert not (workspace / adjudicate.LOG_NAME).exists()


def test_a_cross_origin_post_is_refused(client: TestClient) -> None:
    group = _group_for("Gross Booking Value")
    response = client.post(
        f"/adjudicate/{group.gid}",
        json={"verdict": "INCLUDE", "reviewer": "JL", "rationale": "x"},
        headers={"origin": "https://evil.example"},
    )
    assert response.status_code == 403


# ===========================================================================
# 3. Grouping: one decision per (issuer, normalised metric name)
# ===========================================================================


def test_grouping_collapses_case_whitespace_and_punctuation(workspace: Path) -> None:
    board = adjudicate.load_board()
    assert board.total == 4  # from seven located occurrences

    group = _group_for("Adjusted Active Consumers")
    assert group.n == 3
    assert group.cik == "1000"
    assert group.first_date == "2019-05-01"
    assert group.last_date == "2023-03-01"
    assert group.forms == ("10-K", "S-1")


def test_normalisation_is_only_used_for_the_key_not_for_display(workspace: Path) -> None:
    group = _group_for("Adjusted Active Consumers")
    assert group.normalised == "adjusted active consumers"
    # The display name is a spelling the issuer actually used, verbatim.
    assert group.display_name == "Adjusted Active Consumers"
    assert {o.metric_name for o in group.occurrences} == {
        "Adjusted Active Consumers",
        "adjusted   active consumers,",
    }


def test_two_issuers_using_the_same_metric_name_are_two_decisions(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    shared = _corpus() + (
        _candidate(
            cik=3000,
            accession="0003000-21-000001",
            form="S-1",
            filing_date="2021-06-01",
            name="Gross Booking Value",
            sentence=SENTENCE_GBV,
            offset=100,
        ),
    )
    pipeline_metrics.write_candidates(shared, path=workspace / adjudicate.CANDIDATES_NAME)
    adjudicate._CACHE.clear()
    board = adjudicate.load_board()
    gbv = [g for g in board.groups if g.normalised == "gross booking value"]
    assert len(gbv) == 2
    assert {g.cik for g in gbv} == {"2000", "3000"}


def test_distinct_definition_variants_are_surfaced(workspace: Path) -> None:
    """A changed definition is what §5 REDEFINED is about; it must be visible."""
    group = _group_for("Adjusted Active Consumers")
    assert len(group.variants) == 2
    texts = [variant.text for variant in group.variants]
    assert SENTENCE_A in texts and SENTENCE_B in texts
    first, second = group.variants
    assert first.first_date == "2019-05-01"
    assert second.first_date == "2023-03-01"


def test_the_page_shows_the_variants_and_a_changed_definition_warning(
    client: TestClient,
) -> None:
    group = _group_for("Adjusted Active Consumers")
    body = client.get(f"/adjudicate/{group.gid}").text
    assert "trailing twelve months" in body
    assert "trailing three months" in body
    assert "2 different ways" in body
    assert "REDEFINED" in body
    assert "<strong>3</strong> occurrences" in body
    assert "2019-05-01" in body and "2023-03-01" in body
    assert "0001000-23-000003" in body
    assert "https://www.sec.gov/Archives/edgar/data/1000/" in body


@pytest.mark.parametrize("order", ["issuer", "count", "name"])
def test_every_order_contains_every_group_exactly_once(
    workspace: Path, order: str
) -> None:
    board = adjudicate.load_board(order)
    ids = [group.gid for group in board.groups]
    assert len(ids) == len(set(ids)) == 4


# ===========================================================================
# 4. The proposal: reasoned, and never a ruling
# ===========================================================================


def test_obvious_gaap_measures_are_proposed_for_exclusion(workspace: Path) -> None:
    proposal = adjudicate.propose(_group_for("net income"))
    assert proposal.verdict == adjudicate.EXCLUDE
    assert proposal.rule == "gaap_measure"
    assert "§4" in proposal.reason


@pytest.mark.parametrize(
    "name",
    ["revenue", "Total Revenue", "EPS", "gross profit", "operating income", "total assets"],
)
def test_the_named_gaap_measures_of_section_4_all_propose_exclude(
    workspace: Path, name: str
) -> None:
    group = adjudicate.Group(
        gid="x",
        cik="1",
        normalised=adjudicate.normalise_name(name),
        display_name=name,
        occurrences=(),
        variants=(),
    )
    assert adjudicate.propose(group).verdict == adjudicate.EXCLUDE


def test_a_bespoke_metric_containing_a_gaap_word_is_not_swept_up(
    workspace: Path,
) -> None:
    group = adjudicate.Group(
        gid="x",
        cik="1",
        normalised=adjudicate.normalise_name("Net Revenue Retention"),
        display_name="Net Revenue Retention",
        occurrences=(),
        variants=(
            adjudicate.Variant(
                text="We define Net Revenue Retention as the rate at which revenue "
                "from existing customers grows, expressed as a percentage.",
                occurrences=(),
            ),
        ),
    )
    assert adjudicate.propose(group).verdict == adjudicate.INCLUDE


def test_a_section_heading_is_proposed_for_exclusion(workspace: Path) -> None:
    proposal = adjudicate.propose(_group_for("Key Operating Metrics"))
    assert proposal.verdict == adjudicate.EXCLUDE
    assert proposal.rule == "section_heading"


def test_a_company_defined_measure_is_proposed_for_inclusion(workspace: Path) -> None:
    proposal = adjudicate.propose(_group_for("Gross Booking Value"))
    assert proposal.verdict == adjudicate.INCLUDE
    assert "§4" in proposal.reason


def test_the_machine_may_decline_to_propose(workspace: Path) -> None:
    group = adjudicate.Group(
        gid="x",
        cik="1",
        normalised="brand promise",
        display_name="Brand Promise",
        occurrences=(),
        variants=(
            adjudicate.Variant(
                text="Brand Promise is defined as who we are.", occurrences=()
            ),
        ),
    )
    proposal = adjudicate.propose(group)
    assert proposal.verdict is None
    assert proposal.label == "no proposal"


def test_the_proposal_is_rendered_as_a_proposal_not_a_ruling(client: TestClient) -> None:
    body = client.get(f"/adjudicate/{_group_for('net income').gid}").text
    assert "machine proposal · not a ruling" in body
    assert "proposed EXCLUDE" in body
    assert "adj-proposal" in body
    # Nothing is pre-selected: no radio arrives checked, and the rationale is empty.
    assert "checked" not in body
    assert 'id="adj-rationale" name="rationale" rows="3" maxlength="400"' in body
    assert "></textarea>" in body


def test_no_route_offers_a_bulk_action(client: TestClient) -> None:
    """The words that would give the game away, in the two pages it could hide in."""
    pages = [
        client.get(f"/adjudicate/{_group_for('net income').gid}").text,
        client.get("/adjudicate/done").text,
    ]
    for body in pages:
        lowered = body.lower()
        for phrase in (
            "accept all",
            "approve all",
            "apply to all",
            "auto-approve",
            "rule the rest",
        ):
            assert phrase not in lowered


# ===========================================================================
# 5. Writing the ledger
# ===========================================================================


def test_one_ruling_writes_through_to_every_occurrence(
    client: TestClient, workspace: Path
) -> None:
    group = _group_for("Adjusted Active Consumers")
    response = _rule(
        client,
        group,
        verdict=adjudicate.INCLUDE,
        reviewer="JL",
        rationale="Quantitative and company-defined (§4).",
    )
    assert response.status_code == 200
    assert response.json()["rows_written"] == 3

    rows = _ledger(workspace)
    assert len(rows) == 3
    assert {row["candidate_id"] for row in rows} == {
        o.candidate_id for o in group.occurrences
    }
    for row in rows:
        assert row["include"] == "yes"
        assert row["reviewer"] == "JL"
        assert row["review_date"]
        assert row["rationale"] == "Quantitative and company-defined (§4)."
        # The evidence METHOD.md §4 requires beside every ruling.
        assert row["defining_sentence"]
        assert row["accession"]
        assert row["char_offset"]


def test_the_written_ledger_round_trips_through_pipeline_metrics(
    client: TestClient, workspace: Path
) -> None:
    group = _group_for("Adjusted Active Consumers")
    _rule(client, group, rationale="Company-defined operating metric (§4).")

    rulings = pipeline_metrics.read_rulings(workspace / adjudicate.LEDGER_NAME)
    assert set(rulings) == {o.candidate_id for o in group.occurrences}
    for candidate_id, row in rulings.items():
        assert row["include"] == "yes"
        assert row["reviewer"] == "JL"
        assert row["rationale"] == "Company-defined operating metric (§4)."
        assert row["candidate_id"] == candidate_id

    with (workspace / adjudicate.LEDGER_NAME).open(newline="", encoding="utf-8") as handle:
        assert csv.DictReader(handle).fieldnames == list(pipeline_metrics.LEDGER_FIELDS)


def test_the_ledger_is_written_atomically_and_leaves_no_partial(
    client: TestClient, workspace: Path
) -> None:
    _rule(client, _group_for("Gross Booking Value"))
    assert (workspace / adjudicate.LEDGER_NAME).is_file()
    assert not list(workspace.glob("*.partial"))


def test_a_second_ruling_appends_rather_than_replacing_the_file(
    client: TestClient, workspace: Path
) -> None:
    _rule(client, _group_for("Adjusted Active Consumers"))
    _rule(
        client,
        _group_for("Gross Booking Value"),
        verdict=adjudicate.EXCLUDE,
        rationale="Not the issuer's own operations (§4).",
    )
    rows = _ledger(workspace)
    assert len(rows) == 5
    assert [row["include"] for row in rows[:3]] == ["yes"] * 3
    assert [row["include"] for row in rows[3:]] == ["no"] * 2


def test_re_ruling_a_group_replaces_its_rows_in_place(
    client: TestClient, workspace: Path
) -> None:
    group = _group_for("Adjusted Active Consumers")
    _rule(client, group, verdict=adjudicate.INCLUDE, rationale="First reading (§4).")
    _rule(client, group, verdict=adjudicate.EXCLUDE, rationale="On reflection, GAAP (§4).")

    rows = _ledger(workspace)
    assert len(rows) == 3  # replaced, not duplicated
    assert {row["include"] for row in rows} == {"no"}
    assert {row["rationale"] for row in rows} == {"On reflection, GAAP (§4)."}


def test_rows_written_by_hand_are_not_lost_on_rewrite(
    client: TestClient, workspace: Path
) -> None:
    path = workspace / adjudicate.LEDGER_NAME
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(pipeline_metrics.LEDGER_FIELDS))
        writer.writeheader()
        writer.writerow(
            {
                **{field: "" for field in pipeline_metrics.LEDGER_FIELDS},
                "candidate_id": "handwritten0000",
                "metric_name": "Ruled by hand earlier",
                "include": "yes",
                "reviewer": "JL",
                "review_date": "2026-08-01",
                "rationale": "Written straight into the ledger.",
            }
        )

    _rule(client, _group_for("Gross Booking Value"))
    rows = _ledger(workspace)
    assert rows[0]["candidate_id"] == "handwritten0000"
    assert len(rows) == 3


# ===========================================================================
# 6. Nothing is committed that a human did not rule on
# ===========================================================================


@pytest.mark.parametrize(
    "fields, fragment",
    [
        ({"reviewer": ""}, "initials"),
        ({"reviewer": "   "}, "initials"),
        ({"reviewer": "12345"}, "initials"),
        ({"rationale": ""}, "rationale"),
        ({"rationale": "   "}, "rationale"),
        ({"verdict": ""}, "verdict"),
        ({"verdict": "MAYBE"}, "verdict"),
        ({"rationale": "x" * 401}, "400 characters"),
    ],
)
def test_an_incomplete_ruling_writes_nothing(
    client: TestClient, workspace: Path, fields: dict[str, str], fragment: str
) -> None:
    response = _rule(client, _group_for("Gross Booking Value"), **fields)
    assert response.status_code == 400
    assert fragment in response.json()["error"]
    assert not (workspace / adjudicate.LEDGER_NAME).exists()


def test_the_verdict_must_be_supplied_on_the_request(
    client: TestClient, workspace: Path
) -> None:
    """No default verdict exists anywhere: an empty body is a 400, not a ruling."""
    group = _group_for("Gross Booking Value")
    response = client.post(f"/adjudicate/{group.gid}", json={})
    assert response.status_code == 400
    assert not (workspace / adjudicate.LEDGER_NAME).exists()


def test_commit_ruling_refuses_an_unknown_verdict_at_the_function_level(
    workspace: Path,
) -> None:
    group = _group_for("Gross Booking Value")
    with pytest.raises(ValueError):
        adjudicate.commit_ruling(
            group=group,
            verdict="PROBABLY",
            reviewer="JL",
            rationale="…",
            rationale_source="free_text",
            proposal=adjudicate.propose(group),
        )
    assert not (workspace / adjudicate.LEDGER_NAME).exists()


def test_the_machine_proposal_is_never_used_as_the_rationale(
    client: TestClient, workspace: Path
) -> None:
    group = _group_for("net income")
    proposal = adjudicate.propose(group)
    _rule(
        client,
        group,
        verdict=adjudicate.EXCLUDE,
        rationale="GAAP measure, not company-defined (§4).",
        rationale_source="preset:1",
    )
    rows = _ledger(workspace)
    assert rows[0]["rationale"] == "GAAP measure, not company-defined (§4)."
    assert proposal.reason not in rows[0]["rationale"]


# ===========================================================================
# 7. Deferral is not a soft exclude
# ===========================================================================


def test_a_deferral_is_written_as_not_determinable(
    client: TestClient, workspace: Path
) -> None:
    group = _group_for("Gross Booking Value")
    response = _rule(
        client,
        group,
        verdict=adjudicate.NOT_DETERMINABLE,
        rationale="The filed record does not settle it (§5).",
    )
    assert response.status_code == 200

    rows = _ledger(workspace)
    assert {row["include"] for row in rows} == {"NOT_DETERMINABLE"}
    assert "no" not in {row["include"] for row in rows}

    rulings = pipeline_metrics.read_rulings(workspace / adjudicate.LEDGER_NAME)
    assert all(row["include"] == "NOT_DETERMINABLE" for row in rulings.values())


def test_the_deferral_option_is_offered_on_the_page(client: TestClient) -> None:
    body = client.get(f"/adjudicate/{_group_for('Gross Booking Value').gid}").text
    assert "NOT_DETERMINABLE" in body
    assert "can’t tell from the filed record" in body


# ===========================================================================
# 8. Resume, progress, and the audit trail
# ===========================================================================


def test_resume_skips_groups_that_are_already_ruled(
    client: TestClient, workspace: Path
) -> None:
    first = client.get("/adjudicate", follow_redirects=False).headers["location"]
    gid = first.rsplit("/", 1)[-1]

    board = adjudicate.load_board()
    assert board.n_ruled == 0

    _rule(client, board.groups[0])
    adjudicate._CACHE.clear()

    resumed = client.get("/adjudicate", follow_redirects=False).headers["location"]
    assert resumed != first
    assert resumed.rsplit("/", 1)[-1] != gid
    assert adjudicate.load_board().n_ruled == 1


def test_resume_lands_on_the_summary_once_everything_is_ruled(
    client: TestClient, workspace: Path
) -> None:
    for group in adjudicate.load_board().groups:
        assert _rule(client, group).status_code == 200

    landing = client.get("/adjudicate", follow_redirects=False)
    assert landing.status_code == 303
    assert landing.headers["location"] == "/adjudicate/done"

    summary = client.get("/adjudicate/done")
    assert summary.status_code == 200
    assert "Every group has been ruled" in summary.text
    assert "4 of 4" in summary.text


def test_progress_is_reported_on_the_page(client: TestClient) -> None:
    _rule(client, _group_for("Gross Booking Value"))
    adjudicate._CACHE.clear()
    body = client.get(f"/adjudicate/{_group_for('net income').gid}").text
    assert "<strong>1</strong> / 4 groups ruled" in body


def test_the_response_carries_the_next_group_so_the_reviewer_never_waits(
    client: TestClient,
) -> None:
    board = adjudicate.load_board()
    payload = _rule(client, board.groups[0]).json()
    assert payload["ok"] is True
    assert payload["next_url"].startswith("/adjudicate/")
    assert payload["next_url"].rsplit("/", 1)[-1] != board.groups[0].gid
    assert payload["n_ruled"] == 1
    assert payload["total"] == 4


def test_the_audit_log_records_the_proposal_beside_the_human_ruling(
    client: TestClient, workspace: Path
) -> None:
    group = _group_for("net income")
    _rule(
        client,
        group,
        verdict=adjudicate.INCLUDE,
        rationale="Disagreeing with the machine on purpose (§4).",
        rationale_source="free_text",
    )
    lines = (workspace / adjudicate.LOG_NAME).read_text(encoding="utf-8").splitlines()
    entry = json.loads(lines[-1])
    assert entry["verdict"] == "INCLUDE"
    assert entry["reviewer"] == "JL"
    assert entry["proposal"]["verdict"] == "EXCLUDE"
    assert entry["agreed_with_proposal"] is False
    assert entry["n_occurrences"] == 1
    assert entry["rationale_source"] == "free_text"


def test_a_group_ruled_by_hand_inconsistently_reads_as_mixed(
    client: TestClient, workspace: Path
) -> None:
    """A partially ruled group is offered again, never counted as done."""
    group = _group_for("Adjusted Active Consumers")
    path = workspace / adjudicate.LEDGER_NAME
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(pipeline_metrics.LEDGER_FIELDS))
        writer.writeheader()
        writer.writerow(
            {
                **{field: "" for field in pipeline_metrics.LEDGER_FIELDS},
                "candidate_id": group.occurrences[0].candidate_id,
                "include": "yes",
                "reviewer": "JL",
                "review_date": "2026-08-01",
                "rationale": "Only one of three occurrences.",
            }
        )
    board = adjudicate.load_board()
    assert board.status(group) == "MIXED"
    assert any(entry["verdict"] == "MIXED" for entry in board.tally())


# ===========================================================================
# 9. Driving it: reviewer defaults, keyboard map, no-JavaScript fallback
# ===========================================================================


def test_the_reviewer_field_defaults_to_the_environment_variable(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(adjudicate.ENABLE_FLAG, "1")
    monkeypatch.setenv(adjudicate.REVIEWER_FLAG, "JL")
    body = TestClient(main.create_app()).get("/adjudicate").text
    assert 'value="JL"' in body


def test_the_keyboard_map_is_published_on_the_page(client: TestClient) -> None:
    body = client.get("/adjudicate").text
    for key in ("<kbd>i</kbd>", "<kbd>x</kbd>", "<kbd>n</kbd>", "<kbd>s</kbd>", "<kbd>b</kbd>"):
        assert key in body
    assert "1…9" in body


def test_a_plain_form_post_works_without_javascript(
    client: TestClient, workspace: Path
) -> None:
    group = _group_for("Gross Booking Value")
    response = client.post(
        f"/adjudicate/{group.gid}",
        content="verdict=EXCLUDE&reviewer=JL&rationale=Not+quantitative+%28%C2%A74%29.",
        headers={"content-type": "application/x-www-form-urlencoded"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"].startswith("/adjudicate")
    rows = _ledger(workspace)
    assert len(rows) == 2
    assert {row["include"] for row in rows} == {"no"}


def test_a_failed_form_post_re_renders_the_page_with_the_error(
    client: TestClient, workspace: Path
) -> None:
    group = _group_for("Gross Booking Value")
    response = client.post(
        f"/adjudicate/{group.gid}",
        content="verdict=EXCLUDE&reviewer=JL&rationale=",
        headers={"content-type": "application/x-www-form-urlencoded"},
    )
    assert response.status_code == 200
    assert "rationale is required" in response.text
    assert not (workspace / adjudicate.LEDGER_NAME).exists()


def test_an_oversized_body_is_refused(client: TestClient, workspace: Path) -> None:
    group = _group_for("Gross Booking Value")
    response = client.post(
        f"/adjudicate/{group.gid}",
        content="x" * (adjudicate.MAX_BODY_BYTES + 1),
        headers={"content-type": "application/x-www-form-urlencoded"},
    )
    assert response.status_code == 413
    assert not (workspace / adjudicate.LEDGER_NAME).exists()


def test_a_missing_candidate_file_says_so_rather_than_inventing_groups(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (workspace / adjudicate.CANDIDATES_NAME).unlink()
    adjudicate._CACHE.clear()
    monkeypatch.setenv(adjudicate.ENABLE_FLAG, "1")
    body = TestClient(main.create_app()).get("/adjudicate").text
    assert "Nothing to adjudicate yet" in body
    assert adjudicate.CANDIDATES_NAME in body


def test_context_is_reported_as_absent_rather_than_reconstructed(
    workspace: Path,
) -> None:
    """No local copy of the document means no context — never a paraphrase."""
    group = _group_for("Gross Booking Value")
    context = adjudicate.surrounding_context(group.occurrences[0])
    assert context["available"] == ""
    assert context["before"] == "" and context["after"] == ""
    assert "sec.gov" in context["note"]


def test_context_is_recovered_from_the_bytes_the_locator_actually_read(
    workspace: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When the document is cached locally, the reviewer sees around the sentence.

    The offsets in the candidate file were computed against ``html_to_text`` of
    the retrieved bytes, so the same two functions reproduce the same string and
    the slice lands where it should. Anything else is reported as unavailable
    rather than approximated.
    """
    from pipeline import edgar as pipeline_edgar

    monkeypatch.setattr(pipeline_config, "RAW", tmp_path / "raw")
    adjudicate._document_text.cache_clear()

    group = _group_for("Gross Booking Value")
    occurrence = group.occurrences[0]
    before = "The following table sets out the measures management reviews. "
    after = " We believe this measure is useful to investors."
    body = ("<html><body><p>" + before + SENTENCE_GBV + after + "</p></body></html>").encode()

    cached = pipeline_edgar._cache_path(occurrence.url)
    cached.parent.mkdir(parents=True, exist_ok=True)
    cached.write_bytes(body)

    context = adjudicate.surrounding_context(occurrence)
    assert context["available"] == "1"
    assert "management reviews" in context["before"]
    assert "useful to investors" in context["after"]
    assert SENTENCE_GBV not in context["before"]
    assert SENTENCE_GBV not in context["after"]


def test_context_is_refused_when_the_offsets_do_not_match_the_local_copy(
    workspace: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from pipeline import edgar as pipeline_edgar

    monkeypatch.setattr(pipeline_config, "RAW", tmp_path / "raw")
    adjudicate._document_text.cache_clear()

    occurrence = _group_for("Gross Booking Value").occurrences[0]
    cached = pipeline_edgar._cache_path(occurrence.url)
    cached.parent.mkdir(parents=True, exist_ok=True)
    cached.write_bytes(b"<html><body><p>A different document entirely.</p></body></html>")

    context = adjudicate.surrounding_context(occurrence)
    assert context["available"] == ""
    assert "does not match" in context["note"]


def test_the_reviewer_field_is_form_associated_for_the_no_javascript_path(
    client: TestClient,
) -> None:
    """Without JavaScript the initials still have to reach the server."""
    body = client.get("/adjudicate").text
    assert 'name="reviewer" form="adj-form"' in body
    assert 'id="adj-reviewer-mirror"' not in body
