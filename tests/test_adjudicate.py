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
from pipeline import analysis as pipeline_analysis
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
    # The §6 evidence index is an lru_cache over a file outside this workspace.
    # Cleared here so no test inherits the real repo's evidence, or another
    # test's synthetic evidence, through it.
    adjudicate._absence_evidence_index.cache_clear()
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


# ===========================================================================
# 10. METHOD.md §5 — the terminal state, the second pass
#
# Three things are being defended here, and the order is the same as at the top
# of this file.
#
# 1. **The integrity claim.** §5 is where the study's finding lives, so: no
#    machine proposal exists on this page; DISCONTINUED is refused unless the
#    §6 four-period absence test was met, and refused again unless the reviewer
#    confirmed they looked for a rename and wrote down what they checked;
#    NOT_DETERMINABLE is always available and is never a soft exclude; and the
#    substantive/cosmetic call is recorded as the reviewer set it, never
#    inferred.
# 2. **The blast radius.** One ledger, two passes, and neither may blank the
#    other. A §4 ruling has to survive a later §5 ruling on the same metric and
#    the reverse, which is the defect this section exists to pin.
# 3. **The round trip.** A state written here has to be the state
#    `pipeline.analysis.read_ledger` reads back, under the column names it
#    actually reads. A mismatch is not an error — it is a ruling dropped in
#    silence.
# ===========================================================================

def _evidence_row(
    *,
    cik: int = 1000,
    key: str = "adjusted active consumers",
    phrase: str = "Adjusted Active Consumers",
    status: str,
    trailing: int = 1,
    vector: list[bool] | None = None,
    required: int = 4,
    in_listing: bool | None = None,
) -> dict:
    """One row shaped exactly as `pipeline.build_evidence` writes them.

    `in_listing=None` writes the pre-amendment shape: no `in_listing_document`
    and no `never_reported_eligible`, which is what every row on disk written
    before 29 August 2026 looks like. That is a distinct case from `False` and
    the §5 guard has to tell them apart.
    """
    flags = vector if vector is not None else [True, True, True, True, False]
    row = {
        "cik": cik,
        "issuer": "Synthetic Issuer, Inc.",
        "arm": "IPO",
        "metric_key": key,
        "phrase": phrase,
        "status": status,
        "reason": f"Synthetic §6 evidence for a test: {status}.",
        "required_periods": required,
        "n_periods": len(flags),
        "presence_vector": flags,
        "trailing_absent_periods": trailing,
        "max_absent_run": trailing,
        "n_appearances": sum(1 for f in flags if f),
        "n_documents_searched": 197,
        "n_documents_failed": 0,
        "first_appearance": None,
        "last_appearance": None,
    }
    if in_listing is not None:
        row["in_listing_document"] = in_listing
        row["never_reported_eligible"] = bool(in_listing and row["n_appearances"] == 0)
    return row


def _never_reported_row(**overrides) -> dict:
    """Evidence for a metric promised at listing and never reported since.

    Faithful to what `pipeline.metrics.absence_test` actually returns for such
    a phrase: NOT_DETERMINABLE, because the §6 test refuses to score a phrase
    that appears nowhere at all. That is the whole reason §5 needed a state of
    its own - all 107 of these landed in NOT_DETERMINABLE.
    """
    fields = {
        "status": pipeline_metrics.NOT_DETERMINABLE,
        "vector": [False, False, False, False, False],
        "trailing": 5,
        "in_listing": True,
    }
    fields.update(overrides)
    return _evidence_row(**fields)


@pytest.fixture()
def evidence(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """A §6 evidence file this test controls, and nothing from the real repo."""
    derived = tmp_path / "derived"
    derived.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(pipeline_config, "DERIVED", derived)
    adjudicate._absence_evidence_index.cache_clear()

    def write(*rows: dict) -> None:
        (derived / "absence_evidence.json").write_text(
            json.dumps({"evidence": list(rows)}), encoding="utf-8"
        )
        adjudicate._absence_evidence_index.cache_clear()

    yield write
    adjudicate._absence_evidence_index.cache_clear()


def _include(client: TestClient, name: str) -> adjudicate.Group:
    """Rule a group INCLUDE at §4 so it reaches the §5 pass."""
    group = _group_for(name)
    assert _rule(client, group, verdict=adjudicate.INCLUDE).status_code == 200
    return group


def _state(client: TestClient, group: adjudicate.Group, **fields: str):
    payload = {
        "state": adjudicate.ALIVE,
        "reviewer": "JL",
        "rationale": "Reported in the most recent annual report (§5).",
        "rationale_source": "preset:1",
    }
    payload.update(fields)
    return client.post(f"/adjudicate/state/{group.gid}", json=payload)


def _rows_for(workspace: Path, group: adjudicate.Group) -> list[dict[str, str]]:
    ids = {o.candidate_id for o in group.occurrences}
    return [row for row in _ledger(workspace) if row["candidate_id"] in ids]


# --- the switch, again -----------------------------------------------------


def test_the_state_routes_are_absent_when_the_flag_is_unset(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(adjudicate.ENABLE_FLAG, raising=False)
    unguarded = TestClient(main.create_app())
    for path in ("/adjudicate/state", "/adjudicate/state/done", "/adjudicate/state/x"):
        assert unguarded.get(path).status_code == 404, path
    assert unguarded.post("/adjudicate/state/x", json={}).status_code == 404


def test_the_state_route_is_not_swallowed_by_the_group_route(client: TestClient) -> None:
    """`/adjudicate/state` is a literal path, not a group id. Order matters."""
    landing = client.get("/adjudicate/state", follow_redirects=False)
    assert landing.status_code == 200  # nothing included yet: the summary renders
    assert "No metric has been included yet" in landing.text


# --- only §4's includes reach this pass ------------------------------------


def test_only_metrics_included_at_section_4_are_offered_a_terminal_state(
    client: TestClient, workspace: Path
) -> None:
    included = _include(client, "Adjusted Active Consumers")
    excluded = _group_for("net income")
    assert _rule(
        client, excluded, verdict=adjudicate.EXCLUDE, rationale="GAAP (§4)."
    ).status_code == 200
    deferred = _group_for("Gross Booking Value")
    assert _rule(
        client, deferred, verdict=adjudicate.NOT_DETERMINABLE, rationale="Unclear (§5)."
    ).status_code == 200

    board = adjudicate.load_state_board()
    assert [g.gid for g in board.groups] == [included.gid]
    assert board.total == 1

    # And the ones that did not survive §4 are a 404 here, not a disabled form.
    assert client.get(f"/adjudicate/state/{excluded.gid}").status_code == 404
    assert client.get(f"/adjudicate/state/{deferred.gid}").status_code == 404
    assert client.post(
        f"/adjudicate/state/{excluded.gid}", json={"state": "ALIVE"}
    ).status_code == 404


def test_the_state_pass_says_so_when_nothing_has_been_included(
    client: TestClient,
) -> None:
    body = client.get("/adjudicate/state").text
    assert "No metric has been included yet" in body
    assert "4 group(s) have not been ruled at §4" in body


# --- the DISCONTINUED guard: the point of the feature ----------------------


def test_discontinued_is_refused_when_the_section_6_test_is_not_met(
    client: TestClient, workspace: Path, evidence
) -> None:
    evidence(
        _evidence_row(status=pipeline_metrics.ABSENCE_TEST_NOT_MET, trailing=1)
    )
    group = _include(client, "Adjusted Active Consumers")

    response = _state(
        client,
        group,
        state=adjudicate.DISCONTINUED,
        rename_confirmed="true",
        state_checked="Read the latest EX-99.1; nothing similar appeared.",
        state_change_date="2024-03-01",
        rationale="Absent since 2023 (§6).",
    )
    assert response.status_code == 400
    error = response.json()["error"]
    assert "ABSENCE_TEST_NOT_MET" in error
    assert "absent for 1 trailing reporting period" in error
    assert "4 consecutive periods" in error
    assert "NOT_DETERMINABLE" in error

    # Nothing was written: the §4 ruling stands alone.
    for row in _rows_for(workspace, group):
        assert row.get("state", "") == ""
        assert row["include"] == "yes"


def test_discontinued_is_refused_when_no_section_6_evidence_exists(
    client: TestClient, workspace: Path, evidence
) -> None:
    """An uncomputed test is not a passed one."""
    evidence()  # an empty evidence file: nothing for this metric
    group = _include(client, "Adjusted Active Consumers")

    response = _state(
        client,
        group,
        state=adjudicate.DISCONTINUED,
        rename_confirmed="true",
        state_checked="Read the latest EX-99.1.",
        state_change_date="2024-03-01",
    )
    assert response.status_code == 400
    error = response.json()["error"]
    assert "no §6 evidence has been computed" in error
    assert "4 required" in error
    assert not any(row.get("state") for row in _rows_for(workspace, group))


def test_discontinued_is_refused_until_the_rename_check_is_confirmed(
    client: TestClient, workspace: Path, evidence
) -> None:
    """docs/ADJUDICATION.md: the single most likely way to publish a false finding."""
    evidence(_evidence_row(status=pipeline_metrics.ABSENCE_TEST_MET, trailing=5))
    group = _include(client, "Adjusted Active Consumers")

    response = _state(
        client,
        group,
        state=adjudicate.DISCONTINUED,
        state_checked="Read the latest EX-99.1.",
        state_change_date="2024-03-01",
    )
    assert response.status_code == 400
    error = response.json()["error"]
    assert "checked for a rename" in error
    assert "Nights and Seats Booked" in error
    assert not any(row.get("state") for row in _rows_for(workspace, group))


def test_discontinued_is_refused_without_a_record_of_what_was_checked(
    client: TestClient, workspace: Path, evidence
) -> None:
    evidence(_evidence_row(status=pipeline_metrics.ABSENCE_TEST_MET, trailing=5))
    group = _include(client, "Adjusted Active Consumers")

    response = _state(
        client,
        group,
        state=adjudicate.DISCONTINUED,
        rename_confirmed="true",
        state_change_date="2024-03-01",
    )
    assert response.status_code == 400
    assert "what you checked is required" in response.json()["error"]
    assert not any(row.get("state") for row in _rows_for(workspace, group))


def test_discontinued_commits_once_section_6_is_met_and_the_rename_was_checked(
    client: TestClient, workspace: Path, evidence
) -> None:
    evidence(_evidence_row(status=pipeline_metrics.ABSENCE_TEST_MET, trailing=5))
    group = _include(client, "Adjusted Active Consumers")

    response = _state(
        client,
        group,
        state=adjudicate.DISCONTINUED,
        rename_confirmed="true",
        state_checked="Read FY2025 EX-99.1; no similar metric introduced; no disposal.",
        state_change_date="2024-03-01",
        last_appearance_date="2023-03-01",
        rationale="Absent 5 periods; no rename found in any filed document (§6).",
    )
    assert response.status_code == 200
    assert response.json()["rows_written"] == 3

    rows = _rows_for(workspace, group)
    assert len(rows) == 3
    for row in rows:
        assert row["state"] == "DISCONTINUED"
        assert row["state_reviewer"] == "JL"
        assert row["state_review_date"]
        assert row["state_rationale"].startswith("Absent 5 periods")
        assert row["state_checked"].startswith("Read FY2025 EX-99.1")
        assert row["state_change_date"] == "2024-03-01"
        assert row["last_appearance_date"] == "2023-03-01"
        assert row["absence_status_at_ruling"] == pipeline_metrics.ABSENCE_TEST_MET
        assert row["absence_periods"] == "5"


def test_a_posted_absence_status_cannot_walk_past_the_guard(
    client: TestClient, workspace: Path, evidence
) -> None:
    """The §6 status is read from the evidence file, never from the request."""
    evidence(_evidence_row(status=pipeline_metrics.ABSENCE_TEST_NOT_MET, trailing=0))
    group = _include(client, "Adjusted Active Consumers")

    response = _state(
        client,
        group,
        state=adjudicate.DISCONTINUED,
        rename_confirmed="true",
        state_checked="Checked.",
        state_change_date="2024-03-01",
        status=pipeline_metrics.ABSENCE_TEST_MET,
        absence_status_at_ruling=pipeline_metrics.ABSENCE_TEST_MET,
        trailing_absent_periods="9",
    )
    assert response.status_code == 400
    assert "ABSENCE_TEST_NOT_MET" in response.json()["error"]
    assert not any(row.get("state") for row in _rows_for(workspace, group))


def test_the_page_blocks_the_discontinued_option_and_names_the_counts(
    client: TestClient, evidence
) -> None:
    evidence(
        _evidence_row(status=pipeline_metrics.ABSENCE_TEST_NOT_MET, trailing=2)
    )
    group = _include(client, "Adjusted Active Consumers")
    body = client.get(f"/adjudicate/state/{group.gid}").text

    assert 'value="DISCONTINUED" disabled' in body
    assert "absent for 2 trailing reporting period" in body
    assert "4 consecutive periods" in body
    # And the other five states are still offered.
    for state in ("ALIVE", "REDEFINED", "RENAMED", "ABSORBED", "NOT_DETERMINABLE"):
        assert f'value="{state}"' in body


def test_the_discontinued_option_is_offered_once_section_6_is_met(
    client: TestClient, evidence
) -> None:
    evidence(_evidence_row(status=pipeline_metrics.ABSENCE_TEST_MET, trailing=6))
    group = _include(client, "Adjusted Active Consumers")
    body = client.get(f"/adjudicate/state/{group.gid}").text

    assert 'value="DISCONTINUED" disabled' not in body
    assert "cannot be committed" not in body
    assert "checked for a rename" in body or "looked for a similarly-shaped" in body
    assert "Nights and Seats Booked" in body


# --- the NEVER_REPORTED guard: METHOD.md §5, amended 29 August 2026 ---------
#
# The state exists because 107 of 946 candidates appear in the listing document
# and in nothing filed afterwards, which the §6 absence test cannot describe.
# It is also the ONE amendment that makes the finding larger, so the guard on it
# is stricter than the §6 one, not looser: the machine's two facts are read from
# the evidence file, the reviewer confirms both ways of being wrong, and both a
# date and the §7.3 direction are constrained.


def test_never_reported_is_refused_when_the_phrase_is_not_in_the_listing_document(
    client: TestClient, workspace: Path, evidence
) -> None:
    evidence(_evidence_row(status=pipeline_metrics.NOT_DETERMINABLE, in_listing=False))
    group = _include(client, "Adjusted Active Consumers")

    response = _state(
        client,
        group,
        state=adjudicate.NEVER_REPORTED,
        never_reported_confirmed="true",
        state_checked_never_reported="Read the first 10-K after listing.",
        first_appearance_date="2019-05-01",
        rationale="Never reported (§5).",
    )
    assert response.status_code == 400
    error = response.json()["error"]
    assert "not found in this issuer" in error
    assert "listing document" in error
    assert "NOT_DETERMINABLE" in error
    assert not any(row.get("state") for row in _rows_for(workspace, group))


def test_never_reported_is_refused_when_the_phrase_was_reported_afterwards(
    client: TestClient, workspace: Path, evidence
) -> None:
    """And the refusal names how many times, because that is the objection."""
    evidence(
        _evidence_row(
            status=pipeline_metrics.ABSENCE_TEST_NOT_MET,
            vector=[True, True, True, False],
            trailing=1,
            in_listing=True,
        )
    )
    group = _include(client, "Adjusted Active Consumers")

    response = _state(
        client,
        group,
        state=adjudicate.NEVER_REPORTED,
        never_reported_confirmed="true",
        state_checked_never_reported="Read the first 10-K after listing.",
        first_appearance_date="2019-05-01",
    )
    assert response.status_code == 400
    error = response.json()["error"]
    assert "appears 3 time(s) in the filed corpus afterwards" in error
    assert "DISCONTINUED" in error
    assert not any(row.get("state") for row in _rows_for(workspace, group))


def test_never_reported_is_refused_when_the_evidence_predates_the_amendment(
    client: TestClient, workspace: Path, evidence
) -> None:
    """A row with no `in_listing_document` field never looked. That is not a pass."""
    evidence(
        _evidence_row(status=pipeline_metrics.NOT_DETERMINABLE, vector=[False, False])
    )
    group = _include(client, "Adjusted Active Consumers")

    response = _state(
        client,
        group,
        state=adjudicate.NEVER_REPORTED,
        never_reported_confirmed="true",
        state_checked_never_reported="Read the first 10-K after listing.",
        first_appearance_date="2019-05-01",
    )
    assert response.status_code == 400
    error = response.json()["error"]
    assert "in_listing_document" in error
    assert "pipeline.build_evidence" in error
    assert not any(row.get("state") for row in _rows_for(workspace, group))


def test_never_reported_is_refused_when_the_evidence_contradicts_itself(
    client: TestClient, workspace: Path, evidence
) -> None:
    """A hand-edited flag does not outrank the two facts it is derived from."""
    row = _never_reported_row()
    row["never_reported_eligible"] = False  # contradicts in_listing + 0 appearances
    evidence(row)
    group = _include(client, "Adjusted Active Consumers")

    response = _state(
        client,
        group,
        state=adjudicate.NEVER_REPORTED,
        never_reported_confirmed="true",
        state_checked_never_reported="Read the FY2020 10-K after listing.",
        first_appearance_date="2019-05-01",
    )
    assert response.status_code == 400
    assert "inconsistent" in response.json()["error"]
    assert not any(row.get("state") for row in _rows_for(workspace, group))


def test_never_reported_is_refused_when_no_evidence_exists_at_all(
    client: TestClient, workspace: Path, evidence
) -> None:
    evidence()  # an empty evidence file: nothing for this metric
    group = _include(client, "Adjusted Active Consumers")

    response = _state(
        client,
        group,
        state=adjudicate.NEVER_REPORTED,
        never_reported_confirmed="true",
        state_checked_never_reported="Read the first 10-K after listing.",
        first_appearance_date="2019-05-01",
    )
    assert response.status_code == 400
    assert "no evidence has been computed" in response.json()["error"]
    assert not any(row.get("state") for row in _rows_for(workspace, group))


def test_a_posted_eligibility_cannot_walk_past_the_never_reported_guard(
    client: TestClient, workspace: Path, evidence
) -> None:
    """The two §5 facts are read from the evidence file, never from the request."""
    evidence(_evidence_row(status=pipeline_metrics.NOT_DETERMINABLE, in_listing=False))
    group = _include(client, "Adjusted Active Consumers")

    response = _state(
        client,
        group,
        state=adjudicate.NEVER_REPORTED,
        never_reported_confirmed="true",
        state_checked_never_reported="Checked.",
        first_appearance_date="2019-05-01",
        # Forged: every field the guard reads, posted as the reviewer wishes it
        # were. None of them reaches `commit_state_ruling`, which has no
        # parameter for any of them.
        never_reported_eligible="true",
        in_listing_document="true",
        n_appearances="0",
        listing_evidence_computed="true",
    )
    assert response.status_code == 400
    assert "not found in this issuer" in response.json()["error"]
    assert not any(row.get("state") for row in _rows_for(workspace, group))


def test_never_reported_is_refused_until_the_pre_first_report_check_is_confirmed(
    client: TestClient, workspace: Path, evidence
) -> None:
    evidence(_never_reported_row())
    group = _include(client, "Adjusted Active Consumers")

    response = _state(
        client,
        group,
        state=adjudicate.NEVER_REPORTED,
        state_checked_never_reported="Read the first 10-K after listing.",
        first_appearance_date="2019-05-01",
    )
    assert response.status_code == 400
    error = response.json()["error"]
    assert "label adopted before the first report" in error
    assert "Super League" in error
    assert not any(row.get("state") for row in _rows_for(workspace, group))


def test_never_reported_is_refused_without_a_record_of_what_was_checked(
    client: TestClient, workspace: Path, evidence
) -> None:
    evidence(_never_reported_row())
    group = _include(client, "Adjusted Active Consumers")

    response = _state(
        client,
        group,
        state=adjudicate.NEVER_REPORTED,
        never_reported_confirmed="true",
        first_appearance_date="2019-05-01",
    )
    assert response.status_code == 400
    assert "what you checked is required" in response.json()["error"]
    assert not any(row.get("state") for row in _rows_for(workspace, group))


def test_never_reported_is_refused_without_a_date(
    client: TestClient, workspace: Path, evidence
) -> None:
    """It is a §7.2 Mover, and §7.2 drops a Mover it cannot place in time."""
    evidence(_never_reported_row())
    group = _include(client, "Adjusted Active Consumers")

    response = _state(
        client,
        group,
        state=adjudicate.NEVER_REPORTED,
        never_reported_confirmed="true",
        state_checked_never_reported="Read the first 10-K after listing.",
    )
    assert response.status_code == 400
    assert "A date is required for NEVER_REPORTED" in response.json()["error"]
    assert not any(row.get("state") for row in _rows_for(workspace, group))


def test_never_reported_refuses_a_reported_direction(
    client: TestClient, workspace: Path, evidence
) -> None:
    """§7.3 conditions on the final two REPORTED periods, and there are none."""
    evidence(_never_reported_row())
    group = _include(client, "Adjusted Active Consumers")

    response = _state(
        client,
        group,
        state=adjudicate.NEVER_REPORTED,
        never_reported_confirmed="true",
        state_checked_never_reported="Read the first 10-K after listing.",
        first_appearance_date="2019-05-01",
        direction_at_last_report="DETERIORATING",
    )
    assert response.status_code == 400
    assert "reported* periods" in response.json()["error"]
    assert not any(row.get("state") for row in _rows_for(workspace, group))


def test_never_reported_commits_once_the_listing_evidence_is_met(
    client: TestClient, workspace: Path, evidence
) -> None:
    evidence(_never_reported_row())
    group = _include(client, "Adjusted Active Consumers")

    response = _state(
        client,
        group,
        state=adjudicate.NEVER_REPORTED,
        never_reported_confirmed="true",
        state_checked_never_reported=(
            "Read the FY2020 10-K and both EX-99.1 releases; no similar metric reported."
        ),
        first_appearance_date="2019-05-01",
        rationale="In the prospectus KPI table; in nothing filed since (§5).",
    )
    assert response.status_code == 200
    assert response.json()["state"] == "NEVER_REPORTED"

    rows = _rows_for(workspace, group)
    assert len(rows) == 3
    for row in rows:
        assert row["state"] == "NEVER_REPORTED"
        assert row["state_reviewer"] == "JL"
        assert row["first_appearance_date"] == "2019-05-01"
        assert row["direction_at_last_report"] == "UNDETERMINED"
        assert row["state_checked"].startswith("Read the FY2020 10-K")
        # The §4 ruling underneath is untouched, as with every other state.
        assert row["include"] == "yes"


def test_the_page_shows_the_listing_evidence_and_says_nothing_was_reported(
    client: TestClient, evidence
) -> None:
    evidence(_never_reported_row())
    group = _include(client, "Adjusted Active Consumers")
    body = client.get(f"/adjudicate/state/{group.gid}").text

    assert 'value="NEVER_REPORTED" disabled' not in body
    assert "The phrase occurs in the listing document and in" in body
    assert "<strong>0</strong> subsequent documents" in body
    assert "has filed nothing containing it since" in body
    assert "Super League" in body


def test_the_page_blocks_never_reported_and_names_the_reason(
    client: TestClient, evidence
) -> None:
    evidence(
        _evidence_row(
            status=pipeline_metrics.ABSENCE_TEST_NOT_MET,
            vector=[True, True, False],
            trailing=1,
            in_listing=True,
        )
    )
    group = _include(client, "Adjusted Active Consumers")
    body = client.get(f"/adjudicate/state/{group.gid}").text

    assert 'value="NEVER_REPORTED" disabled' in body
    assert "appears 2 time(s) in the filed corpus afterwards" in body
    # Every other state is still offered: the two guards are separate, and one
    # refusing must not disable the other.
    for state in ("ALIVE", "REDEFINED", "RENAMED", "ABSORBED", "NOT_DETERMINABLE"):
        assert f'value="{state}"' in body


def test_a_never_reported_ruling_reads_back_as_a_mover(
    client: TestClient, workspace: Path, evidence
) -> None:
    """The round trip. A state the analysis cannot read is a ruling thrown away."""
    evidence(_never_reported_row())
    group = _include(client, "Adjusted Active Consumers")
    assert _state(
        client,
        group,
        state=adjudicate.NEVER_REPORTED,
        never_reported_confirmed="true",
        state_checked_never_reported="Read the FY2020 10-K; nothing similar reported.",
        first_appearance_date="2019-05-01",
        rationale="In the prospectus KPI table; in nothing filed since (§5).",
    ).status_code == 200

    ledger = pipeline_analysis.read_ledger(workspace / adjudicate.LEDGER_NAME)
    assert ledger.available is True, ledger.reason
    assert ledger.invalid == ()
    assert len(ledger.rows) == 1
    row = ledger.rows[0]
    assert row.state == "NEVER_REPORTED"
    assert row.is_never_reported is True
    assert row.is_discontinued is False
    assert row.moved is True
    assert row.direction == "UNDETERMINED"
    # The listing document is its first appearance and its last, because there
    # is no other, so §7.2 can place it in time from that field alone.
    assert row.move_date.isoformat() == "2019-05-01"

    rates = pipeline_analysis.base_rate(ledger.rows, [])
    variants = {v["key"]: v for v in rates["variants"]}
    assert variants["never_reported_as_abandonment"]["abandonment"]["abandoned"] == 1
    assert variants["never_reported_as_not_determinable"]["abandonment"]["abandoned"] == 0
    assert rates["difference_pp"] == pytest.approx(100.0)


def test_a_benign_label_may_be_recorded_against_never_reported(
    client: TestClient, workspace: Path, evidence
) -> None:
    """§7.4 re-runs the primary with benign moves removed, and this is a move."""
    evidence(_never_reported_row())
    group = _include(client, "Adjusted Active Consumers")
    assert _state(
        client,
        group,
        state=adjudicate.NEVER_REPORTED,
        never_reported_confirmed="true",
        state_checked_never_reported="Read the FY2020 10-K; the segment was sold.",
        first_appearance_date="2019-05-01",
        benign_label="BENIGN_BUSINESS_DISPOSAL",
        benign_detail="The measured business was disposed of before the first report.",
        rationale="In the prospectus; in nothing filed since (§5).",
    ).status_code == 200
    for row in _rows_for(workspace, group):
        assert row["benign"] == "true"
        assert row["benign_label"] == "BENIGN_BUSINESS_DISPOSAL"


def test_the_two_checked_boxes_do_not_overwrite_each_other(
    client: TestClient, workspace: Path, evidence
) -> None:
    """Both fieldsets post. The armed state decides which one is read."""
    evidence(_never_reported_row())
    group = _include(client, "Adjusted Active Consumers")
    response = _state(
        client,
        group,
        state=adjudicate.NEVER_REPORTED,
        never_reported_confirmed="true",
        state_checked="",  # the §6 box, untouched by the reviewer
        state_checked_never_reported="Read the FY2020 10-K after listing.",
        first_appearance_date="2019-05-01",
    )
    assert response.status_code == 200
    for row in _rows_for(workspace, group):
        assert row["state_checked"] == "Read the FY2020 10-K after listing."


# --- NOT_DETERMINABLE is never a soft exclude ------------------------------


def test_not_determinable_is_available_even_when_the_absence_test_blocks(
    client: TestClient, workspace: Path, evidence
) -> None:
    evidence(_evidence_row(status=pipeline_metrics.ABSENCE_TEST_NOT_MET, trailing=1))
    group = _include(client, "Adjusted Active Consumers")

    response = _state(
        client,
        group,
        state=adjudicate.NOT_DETERMINABLE,
        rationale="The filed record does not settle it (§5).",
    )
    assert response.status_code == 200
    for row in _rows_for(workspace, group):
        assert row["state"] == "NOT_DETERMINABLE"
        # It is a state, not a retraction of the §4 inclusion.
        assert row["include"] == "yes"
        assert row["benign"] == "false"


# --- RENAMED carries the alias ---------------------------------------------


def test_renamed_requires_the_new_name(client: TestClient, workspace: Path) -> None:
    group = _include(client, "Adjusted Active Consumers")
    response = _state(client, group, state=adjudicate.RENAMED, renamed_to="")
    assert response.status_code == 400
    assert "traced rename" in response.json()["error"]
    assert not any(row.get("state") for row in _rows_for(workspace, group))


def test_renamed_stores_the_new_name_so_section_6_can_be_re_run(
    client: TestClient, workspace: Path
) -> None:
    group = _include(client, "Adjusted Active Consumers")
    response = _state(
        client,
        group,
        state=adjudicate.RENAMED,
        renamed_to="Nights and Seats Booked",
        rationale="Same definition, new label; traced (§5).",
    )
    assert response.status_code == 200
    for row in _rows_for(workspace, group):
        assert row["state"] == "RENAMED"
        assert row["renamed_to"] == "Nights and Seats Booked"


# --- REDEFINED carries the substantive/cosmetic call -----------------------


def test_redefined_requires_an_explicit_substantive_call(
    client: TestClient, workspace: Path
) -> None:
    group = _include(client, "Adjusted Active Consumers")
    response = _state(client, group, state=adjudicate.REDEFINED)
    assert response.status_code == 400
    assert "substantive" in response.json()["error"]
    assert not any(row.get("state") for row in _rows_for(workspace, group))


@pytest.mark.parametrize("answer, written", [("true", "true"), ("false", "false")])
def test_substantive_is_recorded_as_the_reviewer_set_it(
    client: TestClient, workspace: Path, answer: str, written: str
) -> None:
    group = _include(client, "Adjusted Active Consumers")
    response = _state(
        client,
        group,
        state=adjudicate.REDEFINED,
        substantive=answer,
        state_change_date="2023-03-01",
        rationale="Population counted changed from TTM to three months (§5).",
    )
    assert response.status_code == 200
    for row in _rows_for(workspace, group):
        assert row["state"] == "REDEFINED"
        assert row["substantive"] == written


def test_substantive_is_left_blank_where_the_question_does_not_arise(
    client: TestClient, workspace: Path
) -> None:
    group = _include(client, "Adjusted Active Consumers")
    assert _state(client, group, state=adjudicate.ALIVE).status_code == 200
    for row in _rows_for(workspace, group):
        assert row["substantive"] == ""


# --- §7.2 needs a date for a Mover -----------------------------------------


def test_a_mover_state_is_refused_without_a_date(
    client: TestClient, workspace: Path
) -> None:
    group = _include(client, "Adjusted Active Consumers")
    response = _state(client, group, state=adjudicate.REDEFINED, substantive="true")
    assert response.status_code == 400
    assert "§7.2" in response.json()["error"]
    assert not any(row.get("state") for row in _rows_for(workspace, group))


def test_a_cosmetic_redefinition_needs_no_date(
    client: TestClient, workspace: Path
) -> None:
    """A cosmetic change is not a move, so §7.2 asks nothing of it."""
    group = _include(client, "Adjusted Active Consumers")
    response = _state(client, group, state=adjudicate.REDEFINED, substantive="false")
    assert response.status_code == 200


@pytest.mark.parametrize("field", ["state_change_date", "first_appearance_date"])
def test_a_date_that_is_not_a_date_is_refused(
    client: TestClient, workspace: Path, field: str
) -> None:
    group = _include(client, "Adjusted Active Consumers")
    response = _state(client, group, **{field: "sometime in 2023"})
    assert response.status_code == 400
    assert field in response.json()["error"]
    assert not any(row.get("state") for row in _rows_for(workspace, group))


# --- §7.4 benign causes are labels, not prose ------------------------------


def test_a_benign_cause_is_recorded_as_a_label_beside_the_rationale(
    client: TestClient, workspace: Path
) -> None:
    group = _include(client, "Adjusted Active Consumers")
    response = _state(
        client,
        group,
        state=adjudicate.REDEFINED,
        substantive="true",
        state_change_date="2023-03-01",
        benign_label="BENIGN_SEGMENT_RECLASSIFICATION",
        benign_detail="Segments restated in the FY2023 10-K.",
        rationale="Arithmetic changed with the segment restatement (§5).",
    )
    assert response.status_code == 200
    for row in _rows_for(workspace, group):
        assert row["benign"] == "true"
        assert row["benign_label"] == "BENIGN_SEGMENT_RECLASSIFICATION"
        assert row["benign_detail"] == "Segments restated in the FY2023 10-K."
        # The label is its own field, not folded into the reviewer's sentence.
        assert "BENIGN_" not in row["state_rationale"]


def test_a_benign_label_is_only_recorded_where_section_7_4_asks_for_one(
    client: TestClient, workspace: Path
) -> None:
    group = _include(client, "Adjusted Active Consumers")
    response = _state(
        client, group, state=adjudicate.ALIVE, benign_label="BENIGN_BUSINESS_DISPOSAL"
    )
    assert response.status_code == 400
    assert "§7.4" in response.json()["error"]


def test_an_unnamed_benign_cause_is_refused(
    client: TestClient, workspace: Path
) -> None:
    group = _include(client, "Adjusted Active Consumers")
    response = _state(
        client,
        group,
        state=adjudicate.REDEFINED,
        substantive="true",
        state_change_date="2023-03-01",
        benign_label="BENIGN_OTHER",
        benign_detail="",
    )
    assert response.status_code == 400
    assert "has to say what it was" in response.json()["error"]


def test_an_invented_benign_label_is_refused(
    client: TestClient, workspace: Path
) -> None:
    group = _include(client, "Adjusted Active Consumers")
    response = _state(
        client,
        group,
        state=adjudicate.REDEFINED,
        substantive="true",
        state_change_date="2023-03-01",
        benign_label="BENIGN_MANAGEMENT_PREFERRED_NOT_TO",
    )
    assert response.status_code == 400
    assert "benign-cause label" in response.json()["error"]


# --- the defect this section exists to pin: neither pass blanks the other ---


def test_a_section_4_ruling_survives_a_later_section_5_ruling(
    client: TestClient, workspace: Path
) -> None:
    group = _group_for("Adjusted Active Consumers")
    assert _rule(
        client,
        group,
        verdict=adjudicate.INCLUDE,
        rationale="Quantitative and company-defined (§4).",
    ).status_code == 200

    assert _state(
        client,
        group,
        state=adjudicate.ALIVE,
        rationale="Reported in the most recent annual report (§5).",
    ).status_code == 200

    rows = _rows_for(workspace, group)
    assert len(rows) == 3
    for row in rows:
        # The §5 ruling landed …
        assert row["state"] == "ALIVE"
        assert row["state_reviewer"] == "JL"
        assert row["state_rationale"].startswith("Reported in the most recent")
        # … and the §4 ruling on the same row is untouched.
        assert row["include"] == "yes"
        assert row["reviewer"] == "JL"
        assert row["review_date"]
        assert row["rationale"] == "Quantitative and company-defined (§4)."
        # As are the locator's own columns.
        assert row["defining_sentence"]
        assert row["accession"]


def test_a_section_5_ruling_survives_a_later_section_4_re_ruling(
    client: TestClient, workspace: Path
) -> None:
    group = _group_for("Adjusted Active Consumers")
    assert _rule(client, group, verdict=adjudicate.INCLUDE).status_code == 200
    assert _state(
        client,
        group,
        state=adjudicate.RENAMED,
        renamed_to="Nights and Seats Booked",
        first_appearance_date="2019-05-01",
        rationale="Same definition, new label (§5).",
    ).status_code == 200

    # The §4 ruling is revised afterwards — a different rationale, same verdict.
    assert _rule(
        client,
        group,
        verdict=adjudicate.INCLUDE,
        rationale="On a second reading, still a company-defined metric (§4).",
    ).status_code == 200

    rows = _rows_for(workspace, group)
    assert len(rows) == 3
    for row in rows:
        assert row["rationale"] == "On a second reading, still a company-defined metric (§4)."
        # The §5 ruling is still there, whole.
        assert row["state"] == "RENAMED"
        assert row["renamed_to"] == "Nights and Seats Booked"
        assert row["first_appearance_date"] == "2019-05-01"
        assert row["state_rationale"] == "Same definition, new label (§5)."
        assert row["metric_id"] == "1000-adjusted-active-consumers"


def test_the_state_columns_appear_only_once_one_is_written(
    client: TestClient, workspace: Path
) -> None:
    """A §4-only ledger keeps exactly the locator's header."""
    group = _include(client, "Adjusted Active Consumers")
    path = workspace / adjudicate.LEDGER_NAME
    with path.open(newline="", encoding="utf-8") as handle:
        assert csv.DictReader(handle).fieldnames == list(pipeline_metrics.LEDGER_FIELDS)

    assert _state(client, group, state=adjudicate.ALIVE).status_code == 200
    with path.open(newline="", encoding="utf-8") as handle:
        assert csv.DictReader(handle).fieldnames == list(
            adjudicate.LEDGER_FIELDS_EXTENDED
        )


def test_a_column_added_by_hand_is_carried_through_both_passes(
    workspace: Path, client: TestClient
) -> None:
    group = _include(client, "Adjusted Active Consumers")
    path = workspace / adjudicate.LEDGER_NAME

    # Someone adds a column and a note to one row, straight into the CSV.
    rows = _ledger(workspace)
    fields = list(rows[0].keys()) + ["note_to_self"]
    rows[0]["note_to_self"] = "check the 2022 10-K again"
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, restval="")
        writer.writeheader()
        writer.writerows(rows)

    assert _state(client, group, state=adjudicate.ALIVE).status_code == 200
    after = _ledger(workspace)
    assert "note_to_self" in after[0]
    assert after[0]["note_to_self"] == "check the 2022 10-K again"
    assert after[0]["state"] == "ALIVE"


def test_a_row_written_by_hand_is_not_lost_by_a_state_ruling(
    client: TestClient, workspace: Path
) -> None:
    group = _include(client, "Gross Booking Value")
    path = workspace / adjudicate.LEDGER_NAME
    rows = _ledger(workspace)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()), restval="")
        writer.writeheader()
        writer.writerow(
            {
                **{field: "" for field in rows[0]},
                "candidate_id": "handwritten0000",
                "metric_name": "Ruled by hand earlier",
                "include": "yes",
                "reviewer": "JL",
                "review_date": "2026-08-01",
                "rationale": "Written straight into the ledger.",
            }
        )
        writer.writerows(rows)

    assert _state(client, group, state=adjudicate.ALIVE).status_code == 200
    after = _ledger(workspace)
    assert after[0]["candidate_id"] == "handwritten0000"
    assert after[0]["rationale"] == "Written straight into the ledger."


# --- the round trip into the analysis --------------------------------------


def test_a_state_ruling_is_read_back_by_pipeline_analysis(
    client: TestClient, workspace: Path, evidence
) -> None:
    evidence(_evidence_row(status=pipeline_metrics.ABSENCE_TEST_MET, trailing=5))
    group = _include(client, "Adjusted Active Consumers")
    assert _state(
        client,
        group,
        state=adjudicate.DISCONTINUED,
        rename_confirmed="true",
        state_checked="Read FY2025 EX-99.1; nothing similar introduced.",
        state_change_date="2024-03-01",
        last_appearance_date="2023-03-01",
        first_appearance_date="2019-05-01",
        direction_at_last_report="DETERIORATING",
        benign_label="BENIGN_BUSINESS_DISPOSAL",
        benign_detail="The measured business was sold in FY2023.",
        rationale="Absent 5 periods; no rename; disposal disclosed (§6, §7.4).",
    ).status_code == 200

    ledger = pipeline_analysis.read_ledger(workspace / adjudicate.LEDGER_NAME)
    assert ledger.available is True, ledger.reason
    assert ledger.invalid == ()

    # Three occurrence rows, one metric — because metric_id is written.
    assert len(ledger.rows) == 1
    row = ledger.rows[0]
    assert row.metric_id == "1000-adjusted-active-consumers"
    assert row.cik == 1000
    assert row.state == "DISCONTINUED"
    assert row.is_discontinued is True
    assert row.moved is True
    assert row.direction == "DETERIORATING"
    assert row.state_change_date.isoformat() == "2024-03-01"
    assert row.last_appearance_date.isoformat() == "2023-03-01"
    assert row.first_appearance_date.isoformat() == "2019-05-01"
    assert row.move_date.isoformat() == "2024-03-01"
    assert row.benign is True
    assert row.benign_label == "BENIGN_BUSINESS_DISPOSAL"
    assert row.benign_detail == "The measured business was sold in FY2023."
    assert row.raw["absence_periods"] == "5"


def test_a_substantive_redefinition_reads_back_as_a_mover(
    client: TestClient, workspace: Path
) -> None:
    group = _include(client, "Adjusted Active Consumers")
    assert _state(
        client,
        group,
        state=adjudicate.REDEFINED,
        substantive="true",
        state_change_date="2023-03-01",
        rationale="Population counted changed (§5).",
    ).status_code == 200
    row = pipeline_analysis.read_ledger(workspace / adjudicate.LEDGER_NAME).rows[0]
    assert row.is_substantive_redefinition is True
    assert row.moved is True


def test_a_cosmetic_redefinition_does_not_read_back_as_a_mover(
    client: TestClient, workspace: Path
) -> None:
    """METHOD.md §5: cosmetic rewording is not REDEFINED, so it is not a Mover."""
    group = _include(client, "Adjusted Active Consumers")
    assert _state(
        client,
        group,
        state=adjudicate.REDEFINED,
        substantive="false",
        rationale="Reworded only; the calculation is identical (§5).",
    ).status_code == 200
    row = pipeline_analysis.read_ledger(workspace / adjudicate.LEDGER_NAME).rows[0]
    assert row.substantive is False
    assert row.is_substantive_redefinition is False
    assert row.moved is False


def test_a_section_4_exclusion_still_reads_back_as_an_exclusion(
    client: TestClient, workspace: Path
) -> None:
    """§5 writing beside §4 must not turn an excluded row into a metric."""
    _rule(client, _group_for("net income"), verdict=adjudicate.EXCLUDE, rationale="GAAP (§4).")
    group = _include(client, "Adjusted Active Consumers")
    _state(client, group, state=adjudicate.ALIVE)

    ledger = pipeline_analysis.read_ledger(workspace / adjudicate.LEDGER_NAME)
    assert len(ledger.ruled_out) == 1
    assert [row.state for row in ledger.rows] == ["ALIVE"]


def test_the_vocabulary_matches_the_analysis_exactly(workspace: Path) -> None:
    """A state this tool can write that the analysis cannot read is a lost ruling."""
    assert adjudicate.TERMINAL_STATES == pipeline_analysis.TERMINAL_STATES
    assert adjudicate.DIRECTIONS == pipeline_analysis.DIRECTIONS
    assert set(adjudicate.STATE_LABEL) == set(adjudicate.TERMINAL_STATES)
    assert set(adjudicate.STATE_KEY) == set(adjudicate.TERMINAL_STATES)
    assert set(adjudicate.STATE_RATIONALE_PRESETS) == set(adjudicate.TERMINAL_STATES)
    # One key each, and none of them collides with the navigation keys.
    keys = list(adjudicate.STATE_KEY.values())
    assert len(set(keys)) == len(keys)
    assert not set(keys) & {"s", "b", "r", "t", "?"}


@pytest.mark.parametrize(
    "name", ["Adjusted Active Consumers", "net income", "Gross Booking Value", "a/b — c"]
)
def test_the_slug_matches_pipeline_analysis(workspace: Path, name: str) -> None:
    assert adjudicate._slugify(name) == pipeline_analysis.slugify(name)


# --- provenance, and no proposal -------------------------------------------


@pytest.mark.parametrize(
    "fields, fragment",
    [
        ({"reviewer": ""}, "initials"),
        ({"reviewer": "12345"}, "initials"),
        ({"rationale": ""}, "rationale"),
        ({"rationale": "   "}, "rationale"),
        ({"state": ""}, "terminal state"),
        ({"state": "GONE"}, "terminal state"),
        ({"state": "ABSENCE_TEST_MET"}, "terminal state"),
        ({"rationale": "x" * 401}, "400 characters"),
    ],
)
def test_an_incomplete_state_ruling_writes_nothing(
    client: TestClient, workspace: Path, fields: dict[str, str], fragment: str
) -> None:
    group = _include(client, "Adjusted Active Consumers")
    response = _state(client, group, **fields)
    assert response.status_code == 400
    assert fragment in response.json()["error"]
    assert not any(row.get("state") for row in _rows_for(workspace, group))


def test_the_mechanical_absence_status_is_not_a_terminal_state(
    workspace: Path,
) -> None:
    assert pipeline_metrics.ABSENCE_TEST_MET not in adjudicate.TERMINAL_STATES


def test_the_state_page_makes_no_proposal(client: TestClient, evidence) -> None:
    evidence(_evidence_row(status=pipeline_metrics.ABSENCE_TEST_MET, trailing=5))
    group = _include(client, "Adjusted Active Consumers")
    body = client.get(f"/adjudicate/state/{group.gid}").text
    assert "no machine proposal" in body
    assert "proposed ALIVE" not in body
    assert "proposed DISCONTINUED" not in body
    assert "adj-proposal" not in body
    # Nothing is pre-selected, and the rationale box arrives empty.
    assert "></textarea>" in body


def test_the_state_pass_offers_no_bulk_action(client: TestClient, evidence) -> None:
    evidence(_evidence_row(status=pipeline_metrics.ABSENCE_TEST_MET, trailing=5))
    group = _include(client, "Adjusted Active Consumers")
    pages = [
        client.get(f"/adjudicate/state/{group.gid}").text,
        client.get("/adjudicate/state/done").text,
    ]
    for body in pages:
        lowered = body.lower()
        for phrase in (
            "accept all",
            "approve all",
            "apply to all",
            "auto-approve",
            "rule the rest",
            "state the rest",
        ):
            assert phrase not in lowered


def test_the_section_6_evidence_is_the_primary_evidence_on_the_page(
    client: TestClient, evidence
) -> None:
    evidence(
        _evidence_row(
            status=pipeline_metrics.ABSENCE_TEST_MET,
            trailing=3,
            vector=[True, True, False, False, False],
        )
    )
    group = _include(client, "Adjusted Active Consumers")
    body = client.get(f"/adjudicate/state/{group.gid}").text

    assert "METHOD.md §6 — the four-period absence test" in body
    assert "ABSENCE_TEST_MET" in body
    assert "Synthetic §6 evidence for a test" in body
    assert "<code>11000</code>" in body  # the presence vector, oldest first
    assert "197" in body  # documents searched
    assert "necessary condition" in body
    # It is above the definitions, not below them.
    assert body.index("four-period absence test") < body.index("definition 1 of")


def test_the_page_says_so_when_no_section_6_evidence_exists(
    client: TestClient, evidence
) -> None:
    evidence()
    group = _include(client, "Adjusted Active Consumers")
    body = client.get(f"/adjudicate/state/{group.gid}").text
    assert "NOT COMPUTED" in body
    assert "pipeline.build_evidence" in body
    assert 'value="DISCONTINUED" disabled' in body


def test_the_audit_log_records_the_state_and_the_evidence_it_was_made_against(
    client: TestClient, workspace: Path, evidence
) -> None:
    evidence(_evidence_row(status=pipeline_metrics.ABSENCE_TEST_MET, trailing=5))
    group = _include(client, "Adjusted Active Consumers")
    _state(
        client,
        group,
        state=adjudicate.DISCONTINUED,
        rename_confirmed="true",
        state_checked="Read FY2025 EX-99.1.",
        state_change_date="2024-03-01",
        rationale="Absent 5 periods; no rename found (§6).",
        rationale_source="free_text",
    )
    lines = (workspace / adjudicate.LOG_NAME).read_text(encoding="utf-8").splitlines()
    entry = json.loads(lines[-1])
    assert entry["pass"] == "state"
    assert entry["state"] == "DISCONTINUED"
    assert entry["reviewer"] == "JL"
    assert entry["n_occurrences"] == 3
    assert entry["absence_evidence"]["status"] == pipeline_metrics.ABSENCE_TEST_MET
    assert entry["absence_evidence"]["trailing_absent_periods"] == "5"
    assert entry["fields"]["state_checked"] == "Read FY2025 EX-99.1."
    # No proposal is logged, because none was made.
    assert "proposal" not in entry


def test_the_state_pass_only_writes_the_two_permitted_filenames(
    client: TestClient, workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    group = _include(client, "Adjusted Active Consumers")
    seen: list[str] = []
    original = adjudicate._write_target

    def recording(filename: str) -> Path:
        seen.append(filename)
        return original(filename)

    monkeypatch.setattr(adjudicate, "_write_target", recording)
    assert _state(client, group, state=adjudicate.ALIVE).status_code == 200
    assert set(seen) <= {adjudicate.LEDGER_NAME, adjudicate.LOG_NAME}
    assert adjudicate.LEDGER_NAME in seen


def test_a_cross_origin_state_post_is_refused(client: TestClient, workspace: Path) -> None:
    group = _include(client, "Adjusted Active Consumers")
    response = client.post(
        f"/adjudicate/state/{group.gid}",
        json={"state": "ALIVE", "reviewer": "JL", "rationale": "x"},
        headers={"origin": "https://evil.example"},
    )
    assert response.status_code == 403
    assert not any(row.get("state") for row in _rows_for(workspace, group))


def test_reading_a_state_page_writes_nothing_new(
    client: TestClient, workspace: Path
) -> None:
    group = _include(client, "Adjusted Active Consumers")
    before = (workspace / adjudicate.LEDGER_NAME).read_bytes()
    client.get("/adjudicate/state")
    client.get(f"/adjudicate/state/{group.gid}")
    client.get("/adjudicate/state/done")
    assert (workspace / adjudicate.LEDGER_NAME).read_bytes() == before


# --- driving it -------------------------------------------------------------


def test_a_plain_form_post_assigns_a_state_without_javascript(
    client: TestClient, workspace: Path
) -> None:
    group = _include(client, "Adjusted Active Consumers")
    response = client.post(
        f"/adjudicate/state/{group.gid}",
        content=(
            "state=ALIVE&reviewer=JL&rationale=Still+reported+in+the+FY2025+10-K+%28%C2%A75%29."
        ),
        headers={"content-type": "application/x-www-form-urlencoded"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"].startswith("/adjudicate/state")
    assert {row["state"] for row in _rows_for(workspace, group)} == {"ALIVE"}


def test_a_failed_state_post_re_renders_the_page_with_the_error(
    client: TestClient, workspace: Path
) -> None:
    group = _include(client, "Adjusted Active Consumers")
    response = client.post(
        f"/adjudicate/state/{group.gid}",
        content="state=RENAMED&reviewer=JL&rationale=Renamed.&renamed_to=",
        headers={"content-type": "application/x-www-form-urlencoded"},
    )
    assert response.status_code == 200
    assert "traced rename" in response.text
    assert not any(row.get("state") for row in _rows_for(workspace, group))


def test_resume_skips_metrics_that_already_carry_a_state(
    client: TestClient, workspace: Path
) -> None:
    first = _include(client, "Adjusted Active Consumers")
    second = _include(client, "Gross Booking Value")
    assert adjudicate.load_state_board().total == 2

    landing = client.get("/adjudicate/state", follow_redirects=False)
    assert landing.status_code == 303
    assert landing.headers["location"] == f"/adjudicate/state/{first.gid}"

    assert _state(client, first, state=adjudicate.ALIVE).status_code == 200
    resumed = client.get("/adjudicate/state", follow_redirects=False)
    assert resumed.headers["location"] == f"/adjudicate/state/{second.gid}"

    assert _state(client, second, state=adjudicate.ALIVE).status_code == 200
    done = client.get("/adjudicate/state", follow_redirects=False)
    assert done.headers["location"] == "/adjudicate/state/done"

    summary = client.get("/adjudicate/state/done")
    assert "Every included metric has a terminal state" in summary.text
    assert "2 of 2" in summary.text


def test_a_partially_stated_metric_reads_as_mixed(
    client: TestClient, workspace: Path
) -> None:
    """One occurrence carrying a state and two not is offered again, not counted."""
    group = _include(client, "Adjusted Active Consumers")
    path = workspace / adjudicate.LEDGER_NAME
    rows = _ledger(workspace)
    fields = list(rows[0].keys()) + ["state", "state_reviewer"]
    rows[0]["state"] = "ALIVE"
    rows[0]["state_reviewer"] = "JL"
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, restval="")
        writer.writeheader()
        writer.writerows(rows)

    board = adjudicate.load_state_board()
    assert board.status(group) == "MIXED"
    assert any(entry["state"] == "MIXED" for entry in board.tally())


def test_the_state_keyboard_map_is_published_on_the_page(
    client: TestClient,
) -> None:
    group = _include(client, "Adjusted Active Consumers")
    body = client.get(f"/adjudicate/state/{group.gid}").text
    for key in ("<kbd>a</kbd>", "<kbd>e</kbd>", "<kbd>m</kbd>", "<kbd>o</kbd>", "<kbd>d</kbd>", "<kbd>n</kbd>"):
        assert key in body
    assert "1…9" in body
    assert 'data-key="d"' in body
    # A preset does not commit here: §5 usually needs more than a sentence.
    assert '"commit_on_preset": false' in body


def test_the_state_page_shows_the_definitions_side_by_side(
    client: TestClient,
) -> None:
    """The REDEFINED call is unmakeable without both definitions on screen."""
    group = _include(client, "Adjusted Active Consumers")
    body = client.get(f"/adjudicate/state/{group.gid}").text
    assert "trailing twelve months" in body
    assert "trailing three months" in body
    assert "definition 1 of 2" in body
    assert "definition 2 of 2" in body


def test_the_state_page_shows_what_section_4_decided(client: TestClient) -> None:
    group = _group_for("Adjusted Active Consumers")
    _rule(
        client,
        group,
        verdict=adjudicate.INCLUDE,
        rationale="Quantitative and company-defined (§4).",
    )
    body = client.get(f"/adjudicate/state/{group.gid}").text
    assert "Ruled at §4" in body
    assert "Quantitative and company-defined (§4)." in body
    assert f'href="/adjudicate/{group.gid}"' in body
