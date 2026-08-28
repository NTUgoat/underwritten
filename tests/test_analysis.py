"""Tests for the pre-registered analysis stage. All offline, no network.

Three things these tests exist to hold down, in order of importance:

1. **The empty-ledger path is the primary tested path.** The human adjudication
   ledger does not exist yet, so the normal state of this stage is "no rulings".
   That must produce well-formed output marked unavailable with a stated reason,
   never a fabricated number and never a crash.
2. **A terminal state is never inferred.** `metrics.py` produces
   `ABSENCE_TEST_MET`, which is mechanical evidence and not a §5 state. The
   analysis must refuse it.
3. **The statistics are what METHOD.md pre-registers.** Exact binomial rather
   than the normal approximation, Fisher's exact test on the 2x2, and a
   10,000-resample bootstrap that reproduces under the fixed seed.
"""

from __future__ import annotations

import csv
import json
import math
from datetime import date, timedelta

import pytest

from pipeline import analysis, config
from pipeline import metrics as metrics_module
from pipeline.analysis import (
    DETERIORATING,
    IMPROVING,
    KEEPER,
    MOVER,
    NO_METRICS_DEFINED,
    TERMINAL_STATES,
    UNDETERMINED,
    AnalysisError,
    AnalysisInputs,
    CohortRow,
    Projection,
    SpecRun,
    append_spec_runs,
    assert_no_forbidden_words,
    base_rate,
    bootstrap_difference,
    classify_issuers,
    clopper_pearson,
    counter_test,
    counting_events,
    fisher_exact_2x2,
    forbidden_word_findings,
    mann_whitney,
    primary_test,
    projection_arm,
    read_ledger,
    read_projections,
    realised_annual_revenue,
    run,
    sensitivity,
    spec_line,
    walk_strings,
)
from pipeline.filings import Filing
from pipeline.outcomes import EXCL_MECHANICAL_DELISTING, EXCL_WARRANT, AdverseEvent

# --------------------------------------------------------------------------
# fixtures and builders
# --------------------------------------------------------------------------

LEDGER_COLUMNS = (
    "cik",
    "metric_name",
    "state",
    "substantive",
    "direction_at_last_report",
    "state_change_date",
    "last_appearance_date",
    "first_appearance_date",
    "benign",
    "benign_label",
    "benign_detail",
    "reviewer",
    "review_date",
    "rationale",
)


def write_ledger(path, rows, *, columns=LEDGER_COLUMNS):
    """Write a synthetic adjudication ledger. Missing keys are written blank."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(columns))
        writer.writeheader()
        for row in rows:
            writer.writerow({column: row.get(column, "") for column in columns})
    return path


def metric(cik, name, state, **overrides):
    row = {"cik": str(cik), "metric_name": name, "state": state}
    row.update({k: str(v) for k, v in overrides.items()})
    return row


def cohort_row(cik, *, name="Issuer", arm="IPO", listing="2020-01-01"):
    return CohortRow(
        cik=cik,
        name=name,
        ticker="TKR",
        arm=arm,
        sic="7372",
        sic_description="Services-Prepackaged Software",
        listing_date=date.fromisoformat(listing),
    )


def filing(form, when, items=(), accession="0000000000-00-000000"):
    return Filing(
        cik=1,
        accession=accession,
        form=form,
        filing_date=when,
        report_date=None,
        primary_document="doc.htm",
        items=items,
        size=0,
        is_xbrl=False,
    )


def event(form, when, *, items=(), reason="Late annual report", excluded=None):
    return AdverseEvent(
        filing=filing(form, when, items),
        reason=reason,
        excluded_as=excluded,
        exclusion_evidence="fixture" if excluded else "",
    )


@pytest.fixture
def empty_ledger(tmp_path):
    return read_ledger(tmp_path / "metrics.csv", cohort_ciks=[1, 2, 3])


def make_inputs(*, cohort, ledger, events=None, outcome_available=False, **kwargs):
    return AnalysisInputs(
        as_at=config.AS_AT_DATE,
        generated="2026-08-28T00:00:00+00:00",
        cohort=tuple(cohort),
        ledger=ledger,
        events=events or {},
        outcome_available=outcome_available,
        outcome_reason="" if outcome_available else analysis.OUTCOME_SKIPPED_REASON,
        **kwargs,
    )


# ==========================================================================
# 1. Exact binomial intervals - METHOD.md §7.1
# ==========================================================================


@pytest.mark.parametrize(
    "k, n, low, high",
    [
        # Published Clopper-Pearson values. k=0 and k=n are the closed-form
        # cases (1 - (alpha/2)**(1/n) and its mirror); the rest come from the
        # beta quantiles the interval is defined by.
        (0, 10, 0.0, 0.30850),
        (1, 10, 0.00253, 0.44502),
        (5, 10, 0.18709, 0.81291),
        (10, 10, 0.69150, 1.0),
        (3, 20, 0.03207, 0.37893),
    ],
)
def test_clopper_pearson_matches_published_values(k, n, low, high):
    interval = clopper_pearson(k, n)
    assert interval.ci_low == pytest.approx(low, abs=5e-5)
    assert interval.ci_high == pytest.approx(high, abs=5e-5)
    assert interval.share == pytest.approx(k / n)
    assert interval.n == n


def test_exact_interval_is_not_the_normal_approximation():
    """0 of 20 has a zero-width Wald interval and a real exact one."""
    interval = clopper_pearson(0, 20)
    assert interval.ci_low == 0.0
    assert interval.ci_high > 0.16  # the normal approximation would say 0.0


def test_interval_is_never_outside_the_unit_range():
    for k in range(8):
        interval = clopper_pearson(k, 7)
        assert 0.0 <= interval.ci_low <= interval.ci_high <= 1.0


def test_a_share_without_a_denominator_is_not_published():
    interval = clopper_pearson(0, 0)
    assert interval.share is None
    assert interval.ci_low is None and interval.ci_high is None
    assert interval.n == 0


@pytest.mark.parametrize("k, n", [(-1, 5), (3, 2), (1, -1)])
def test_impossible_counts_raise_rather_than_return_a_figure(k, n):
    with pytest.raises(ValueError):
        clopper_pearson(k, n)


def test_proportion_dict_carries_its_n_and_its_method():
    payload = clopper_pearson(2, 9).as_dict()
    assert payload["n"] == 9
    assert payload["k"] == 2
    assert "Clopper-Pearson" in payload["interval_method"]


# ==========================================================================
# 2. Fisher's exact test - METHOD.md §7.2
# ==========================================================================


def test_fisher_exact_tea_tasting_table():
    """[[3,1],[1,3]]: P(k) = C(4,k)^2 / 70, observed P(3) = 16/70.

    Tables no more probable than the observed one are k in {0, 1, 3, 4},
    summing to (1 + 16 + 16 + 1) / 70 = 34/70.
    """
    assert fisher_exact_2x2(3, 1, 1, 3) == pytest.approx(34 / 70)


def test_fisher_exact_hand_computed_asymmetric_table():
    """[[8,2],[1,5]]: n=16, row1=10, col1=9, C(16,9)=11440.

    Numerators over the support k=3..9 are 120, 1260, 3780, 4200, 1800, 270, 10.
    Observed a=8 gives 270, so the two-sided sum is (120 + 270 + 10)/11440.
    """
    assert fisher_exact_2x2(8, 2, 1, 5) == pytest.approx(400 / 11440)


@pytest.mark.parametrize(
    "table, expected",
    [
        ((1, 9, 11, 3), 0.002759456185220083),
        ((4, 15, 13, 15), 0.12199164871245709),
        ((0, 19, 5, 23), 0.07165082835758135),
        ((2, 17, 9, 19), 0.15896847396850283),
    ],
)
def test_fisher_exact_regression_tables(table, expected):
    assert fisher_exact_2x2(*table) == pytest.approx(expected, rel=1e-12)


def test_fisher_exact_is_symmetric_under_transposition():
    assert fisher_exact_2x2(4, 15, 13, 15) == pytest.approx(
        fisher_exact_2x2(4, 13, 15, 15)
    )


def test_fisher_exact_of_an_independent_table_is_one():
    assert fisher_exact_2x2(5, 5, 5, 5) == pytest.approx(1.0)


def test_fisher_exact_refuses_an_empty_table():
    with pytest.raises(ValueError):
        fisher_exact_2x2(0, 0, 0, 0)


@pytest.mark.parametrize("table", [(-1, 1, 1, 1), (1, 1, -2, 1)])
def test_fisher_exact_refuses_negative_cells(table):
    with pytest.raises(ValueError):
        fisher_exact_2x2(*table)


# ==========================================================================
# 3. The bootstrap - METHOD.md §7.2, reproducible under the fixed seed
# ==========================================================================

KEEPER_ARM = [0, 0, 0, 1, 0, 1, 0, 0, 0, 0]
MOVER_ARM = [1, 1, 0, 1, 1, 0, 1, 1, 0, 1, 1, 0]


def test_bootstrap_is_reproducible_under_the_fixed_seed():
    first = bootstrap_difference(KEEPER_ARM, MOVER_ARM)
    second = bootstrap_difference(KEEPER_ARM, MOVER_ARM)
    assert first == second
    assert first.seed == config.RANDOM_SEED


def test_bootstrap_moves_with_the_seed():
    fixed = bootstrap_difference(KEEPER_ARM, MOVER_ARM)
    other = bootstrap_difference(KEEPER_ARM, MOVER_ARM, seed=config.RANDOM_SEED + 1)
    assert (fixed.ci_low_pp, fixed.ci_high_pp) != (other.ci_low_pp, other.ci_high_pp)
    assert fixed.difference_pp == other.difference_pp  # the point estimate is not resampled


def test_bootstrap_uses_the_preregistered_resample_count():
    assert bootstrap_difference(KEEPER_ARM, MOVER_ARM).resamples == 10_000
    assert config.BOOTSTRAP_RESAMPLES == 10_000


def test_bootstrap_interval_brackets_the_observed_difference():
    result = bootstrap_difference(KEEPER_ARM, MOVER_ARM)
    observed = (sum(MOVER_ARM) / len(MOVER_ARM) - sum(KEEPER_ARM) / len(KEEPER_ARM)) * 100
    assert result.difference_pp == pytest.approx(observed)
    assert result.ci_low_pp <= result.difference_pp <= result.ci_high_pp


def test_bootstrap_carries_the_size_of_both_arms():
    result = bootstrap_difference(KEEPER_ARM, MOVER_ARM).as_dict()
    assert result["n_baseline"] == len(KEEPER_ARM)
    assert result["n_comparison"] == len(MOVER_ARM)


def test_bootstrap_on_an_empty_arm_is_unavailable_with_a_reason():
    result = bootstrap_difference([], MOVER_ARM)
    assert result.difference_pp is None
    assert result.ci_low_pp is None
    assert "undefined" in result.reason


def test_bootstrap_refuses_zero_resamples():
    with pytest.raises(ValueError):
        bootstrap_difference(KEEPER_ARM, MOVER_ARM, resamples=0)


def test_percentile_interpolates_between_neighbours():
    assert analysis._percentile([0.0, 1.0, 2.0, 3.0], 0.5) == pytest.approx(1.5)
    assert analysis._percentile([5.0], 0.9) == pytest.approx(5.0)


def test_mann_whitney_carries_its_n():
    result = mann_whitney([0, 0, 1], [2, 3, 4, 1])
    assert result["n_baseline"] == 3 and result["n_comparison"] == 4
    assert result["u"] is not None and 0.0 <= result["p"] <= 1.0


def test_mann_whitney_on_an_empty_arm_states_why_it_did_not_run():
    result = mann_whitney([], [1, 2])
    assert result["u"] is None and result["p"] is None
    assert result["reason"]


# ==========================================================================
# 4. The adjudication ledger - absent, malformed, or partly usable
# ==========================================================================


def test_missing_ledger_is_unavailable_with_a_stated_reason(tmp_path):
    ledger = read_ledger(tmp_path / "metrics.csv")
    assert ledger.available is False
    assert "has not been written yet" in ledger.reason
    assert ledger.rows == ()


def test_header_only_ledger_is_unavailable_not_a_zero(tmp_path):
    ledger = read_ledger(write_ledger(tmp_path / "metrics.csv", []))
    assert ledger.available is False
    assert "no METHOD.md §5 terminal state" in ledger.reason


def test_unreadable_ledger_degrades_with_the_error_recorded(tmp_path):
    path = tmp_path / "metrics.csv"
    path.write_bytes(b"cik,metric_name,state\n1,\xff\xfe bad bytes,ALIVE\n")
    ledger = read_ledger(path)
    assert ledger.available is False
    assert "could not be read" in ledger.reason


def test_absence_test_evidence_is_not_accepted_as_a_terminal_state(tmp_path):
    """The core integrity rule. §6 evidence is not a §5 state."""
    assert metrics_module.ABSENCE_TEST_MET not in TERMINAL_STATES
    assert metrics_module.ABSENCE_TEST_MET not in analysis.LEDGER_STATES
    path = write_ledger(
        tmp_path / "metrics.csv",
        [metric(1, "Active Riders", metrics_module.ABSENCE_TEST_MET)],
    )
    ledger = read_ledger(path)
    assert ledger.available is False
    assert len(ledger.invalid) == 1
    assert "ABSENCE_TEST_MET" in ledger.invalid[0]["problem"]


def test_not_determinable_is_a_terminal_state_and_absence_test_met_is_not():
    assert "NOT_DETERMINABLE" in TERMINAL_STATES
    assert metrics_module.NOT_DETERMINABLE == "NOT_DETERMINABLE"
    assert metrics_module.ABSENCE_TEST_NOT_MET not in analysis.LEDGER_STATES


def test_unparseable_rows_are_counted_never_dropped_in_silence(tmp_path):
    path = write_ledger(
        tmp_path / "metrics.csv",
        [
            metric(1, "Good Metric", "ALIVE"),
            metric("", "No CIK", "ALIVE"),
            metric(1, "", "ALIVE"),
            metric(1, "Bad State", "VANISHED"),
        ],
    )
    ledger = read_ledger(path)
    assert ledger.available is True
    assert len(ledger.rows) == 1
    assert len(ledger.invalid) == 3
    assert {row["row"] for row in ledger.invalid} == {"3", "4", "5"}
    assert ledger.as_dict()["n_invalid_rows"] == 3


def test_rows_outside_the_frozen_cohort_are_counted_never_dropped(tmp_path):
    path = write_ledger(
        tmp_path / "metrics.csv",
        [metric(1, "In Cohort", "ALIVE"), metric(999, "Not In Cohort", "ALIVE")],
    )
    ledger = read_ledger(path, cohort_ciks=[1])
    assert len(ledger.rows) == 1
    assert len(ledger.off_cohort) == 1
    assert ledger.off_cohort[0]["cik"] == "999"


def test_unknown_direction_is_recorded_as_undetermined(tmp_path):
    path = write_ledger(
        tmp_path / "metrics.csv",
        [
            metric(1, "A", "ALIVE", direction_at_last_report="sideways"),
            metric(1, "B", "ALIVE"),
            metric(1, "C", "ALIVE", direction_at_last_report="improving"),
        ],
    )
    rows = read_ledger(path).rows
    assert [row.direction for row in rows] == [UNDETERMINED, UNDETERMINED, IMPROVING]


def test_a_redefinition_is_substantive_unless_the_reviewer_says_otherwise(tmp_path):
    path = write_ledger(
        tmp_path / "metrics.csv",
        [
            metric(1, "Default", "REDEFINED"),
            metric(1, "Cosmetic", "REDEFINED", substantive="FALSE"),
        ],
    )
    rows = read_ledger(path).rows
    assert rows[0].substantive is True and rows[0].moved is True
    assert rows[1].substantive is False and rows[1].moved is False


def test_move_date_falls_back_to_the_last_appearance(tmp_path):
    path = write_ledger(
        tmp_path / "metrics.csv",
        [
            metric(1, "Dated", "DISCONTINUED", state_change_date="2023-02-01"),
            metric(1, "Undated", "DISCONTINUED", last_appearance_date="2022-05-05"),
            metric(1, "Neither", "DISCONTINUED"),
        ],
    )
    rows = read_ledger(path).rows
    assert rows[0].move_date == date(2023, 2, 1)
    assert rows[1].move_date == date(2022, 5, 5)
    assert rows[2].move_date is None


# ==========================================================================
# 5. The Keeper / Mover split - METHOD.md §7.2
# ==========================================================================


@pytest.fixture
def split_ledger(tmp_path):
    path = write_ledger(
        tmp_path / "metrics.csv",
        [
            metric(1, "Kept A", "ALIVE", direction_at_last_report="IMPROVING"),
            metric(1, "Kept B", "RENAMED", direction_at_last_report="DETERIORATING"),
            metric(
                2,
                "Dropped",
                "DISCONTINUED",
                direction_at_last_report="DETERIORATING",
                state_change_date="2022-03-01",
                first_appearance_date="2020-01-01",
            ),
            metric(2, "Kept C", "ALIVE", direction_at_last_report="IMPROVING"),
            metric(
                3,
                "Benignly dropped",
                "DISCONTINUED",
                direction_at_last_report="IMPROVING",
                state_change_date="2023-01-05",
                benign="TRUE",
                benign_label="ASC 280 segment reclassification",
                benign_detail="Reported segments were reorganised.",
            ),
            metric(4, "Redefined", "REDEFINED", state_change_date="2021-06-01"),
            metric(5, "None defined", NO_METRICS_DEFINED),
        ],
    )
    return read_ledger(path, cohort_ciks=[1, 2, 3, 4, 5])


def test_keepers_have_no_discontinuation_and_no_substantive_redefinition(split_ledger):
    groups = {b.cik: b.group for b in classify_issuers(split_ledger.rows)}
    assert groups == {1: KEEPER, 2: MOVER, 3: MOVER, 4: MOVER}


def test_an_issuer_with_no_metric_defined_is_in_neither_arm(split_ledger):
    assert 5 not in {b.cik for b in classify_issuers(split_ledger.rows)}
    assert split_ledger.no_metrics_ciks == (5,)


def test_removing_benign_labels_moves_an_issuer_back_to_keepers(split_ledger):
    groups = {b.cik: b.group for b in classify_issuers(split_ledger.rows, drop_benign=True)}
    assert groups[3] == KEEPER
    assert groups[2] == MOVER


def test_a_cosmetic_redefinition_does_not_make_a_mover(tmp_path):
    path = write_ledger(
        tmp_path / "metrics.csv",
        [metric(7, "Reworded", "REDEFINED", substantive="FALSE")],
    )
    behaviours = classify_issuers(read_ledger(path).rows)
    assert behaviours[0].group == KEEPER


# ==========================================================================
# 6. §7.1 - the base rate
# ==========================================================================


def test_base_rate_reports_every_state_with_its_denominator(split_ledger):
    rates = base_rate(split_ledger.rows, [cohort_row(i) for i in (1, 2, 3, 4, 5)])
    assert rates["available"] is True
    assert rates["denominator"] == 6  # the NO_METRICS_DEFINED row is not a metric
    states = {row["state"]: row for row in rates["states"]}
    assert set(states) == set(TERMINAL_STATES)
    assert states["DISCONTINUED"]["n"] == 2
    assert states["DISCONTINUED"]["share"] == pytest.approx(2 / 6)
    for row in rates["states"]:
        assert row["denominator"] == 6
        assert row["ci_low"] is not None and row["ci_high"] is not None


def test_base_rate_is_also_reported_per_issuer(split_ledger):
    rates = base_rate(split_ledger.rows, [cohort_row(i) for i in (1, 2, 3, 4)])
    per_issuer = {row["cik"]: row for row in rates["by_issuer"]}
    assert set(per_issuer) == {1, 2, 3, 4}
    assert per_issuer[2]["denominator"] == 2
    discontinued = next(
        row for row in per_issuer[2]["states"] if row["state"] == "DISCONTINUED"
    )
    assert discontinued["n"] == 1 and discontinued["share"] == pytest.approx(0.5)


def test_base_rate_without_rulings_is_unavailable(empty_ledger):
    rates = base_rate(empty_ledger.rows, [cohort_row(1)])
    assert rates["available"] is False
    assert rates["denominator"] == 0
    assert rates["reason"]


# ==========================================================================
# 7. §7.2 - the confirmatory test, its timing rule and Amendment 1
# ==========================================================================


@pytest.fixture
def primary_fixture(split_ledger):
    behaviours = classify_issuers(split_ledger.rows)
    listing = {
        1: date(2020, 1, 1),
        2: date(2020, 1, 1),
        3: date(2020, 1, 1),
        4: date(2020, 1, 1),
    }
    return behaviours, listing


def test_an_event_before_the_move_date_does_not_count(primary_fixture):
    behaviours, listing = primary_fixture
    events = {2: (event("NT 10-K", date(2021, 1, 1)),)}  # before the 2022-03-01 move
    result = primary_test(
        behaviours, listing_dates=listing, events=events, outcome_available=True
    ).as_dict()
    scored = {row["cik"]: row for row in result["per_issuer"]}
    assert scored[2]["n_adverse_events"] == 0


def test_an_event_after_the_move_date_counts(primary_fixture):
    behaviours, listing = primary_fixture
    events = {2: (event("NT 10-K", date(2023, 1, 1)),)}
    result = primary_test(
        behaviours, listing_dates=listing, events=events, outcome_available=True
    ).as_dict()
    scored = {row["cik"]: row for row in result["per_issuer"]}
    assert scored[2]["n_adverse_events"] == 1
    assert result["movers"]["adverse"] == 1


def test_keepers_are_scored_from_the_median_offset_from_listing(primary_fixture):
    behaviours, listing = primary_fixture
    result = primary_test(
        behaviours, listing_dates=listing, events={}, outcome_available=True
    ).as_dict()
    offsets = sorted(
        (d - date(2020, 1, 1)).days for d in (date(2021, 6, 1), date(2022, 3, 1), date(2023, 1, 5))
    )
    assert result["timing"]["median_offset_days"] == offsets[1]
    keeper = next(row for row in result["per_issuer"] if row["group"] == KEEPER)
    assert keeper["threshold"] == (
        date(2020, 1, 1) + timedelta(days=offsets[1])
    ).isoformat()


def test_both_arms_carry_their_n_beside_every_figure(primary_fixture):
    behaviours, listing = primary_fixture
    result = primary_test(
        behaviours, listing_dates=listing, events={}, outcome_available=True
    ).as_dict()
    assert result["keepers"]["n"] == 1 and result["movers"]["n"] == 3
    assert result["bootstrap"]["n_baseline"] == 1
    assert result["bootstrap"]["n_comparison"] == 3
    assert result["mann_whitney"]["n_baseline"] == 1
    assert sum(result["fisher_table"][k] for k in (
        "keepers_adverse", "keepers_not_adverse", "movers_adverse", "movers_not_adverse"
    )) == 4


def test_the_warrant_restatement_is_excluded_from_the_primary(primary_fixture):
    behaviours, listing = primary_fixture
    events = {
        2: (
            event(
                "8-K",
                date(2023, 5, 4),
                items=("4.02",),
                reason="Non-reliance",
                excluded=EXCL_WARRANT,
            ),
        )
    }
    excluded_run = primary_test(
        behaviours, listing_dates=listing, events=events, outcome_available=True
    ).as_dict()
    restored_run = primary_test(
        behaviours,
        listing_dates=listing,
        events=events,
        outcome_available=True,
        restore_warrants=True,
    ).as_dict()
    assert excluded_run["movers"]["adverse"] == 0
    assert restored_run["movers"]["adverse"] == 1
    assert restored_run["warrant_restatements_restored"] is True


def test_the_mechanical_despac_delisting_is_never_restored(primary_fixture):
    behaviours, listing = primary_fixture
    events = {
        2: (
            event(
                "25-NSE",
                date(2023, 5, 4),
                reason="Delisting notification",
                excluded=EXCL_MECHANICAL_DELISTING,
            ),
        )
    }
    for restore in (False, True):
        result = primary_test(
            behaviours,
            listing_dates=listing,
            events=events,
            outcome_available=True,
            restore_warrants=restore,
        ).as_dict()
        assert result["movers"]["adverse"] == 0


def test_counting_events_honours_both_amendment_one_exclusions():
    candidates = [
        event("NT 10-K", date(2023, 1, 1)),
        event("8-K", date(2021, 5, 4), items=("4.02",), excluded=EXCL_WARRANT),
        event("25-NSE", date(2020, 6, 3), excluded=EXCL_MECHANICAL_DELISTING),
    ]
    assert len(counting_events(candidates)) == 1
    assert len(counting_events(candidates, restore_warrants=True)) == 2


def test_a_mover_that_cannot_be_placed_in_time_is_excluded_and_counted(tmp_path):
    path = write_ledger(
        tmp_path / "metrics.csv",
        [
            metric(1, "Kept", "ALIVE"),
            metric(2, "Dropped, dated", "DISCONTINUED", state_change_date="2022-03-01"),
            metric(3, "Dropped, undated", "DISCONTINUED"),
        ],
    )
    behaviours = classify_issuers(read_ledger(path).rows)
    listing = {1: date(2020, 1, 1), 2: date(2020, 1, 1), 3: date(2020, 1, 1)}
    result = primary_test(
        behaviours, listing_dates=listing, events={}, outcome_available=True
    ).as_dict()
    assert result["n_excluded_untimed"] == 1
    assert result["excluded_untimed"][0]["cik"] == 3
    assert result["movers"]["n"] == 1


def test_primary_is_unavailable_when_the_outcome_has_not_been_extracted(primary_fixture):
    behaviours, listing = primary_fixture
    result = primary_test(
        behaviours,
        listing_dates=listing,
        events={},
        outcome_available=False,
        outcome_reason="the extraction has not run",
    )
    assert result.available is False
    assert "extraction has not run" in result.reason
    assert "keepers" not in result.as_dict()


def test_primary_is_unavailable_without_any_ruling(empty_ledger):
    result = primary_test(
        (), listing_dates={}, events={}, outcome_available=True
    )
    assert result.available is False
    assert "ledger" in result.reason.lower()


def test_primary_is_unavailable_when_no_mover_carries_a_date(tmp_path):
    path = write_ledger(
        tmp_path / "metrics.csv",
        [metric(1, "Kept", "ALIVE"), metric(2, "Dropped", "DISCONTINUED")],
    )
    result = primary_test(
        classify_issuers(read_ledger(path).rows),
        listing_dates={1: date(2020, 1, 1), 2: date(2020, 1, 1)},
        events={},
        outcome_available=True,
    )
    assert result.available is False
    assert "median offset" in result.reason
    assert result.as_dict()["n_movers"] == 1


# ==========================================================================
# 8. §7.3 - the counter-hypothesis, published either way
# ==========================================================================


def test_undetermined_direction_is_an_explicit_published_third_category(tmp_path):
    path = write_ledger(
        tmp_path / "metrics.csv",
        [
            metric(1, "A", "DISCONTINUED", direction_at_last_report=DETERIORATING),
            metric(1, "B", "ALIVE", direction_at_last_report=DETERIORATING),
            metric(1, "C", "ALIVE", direction_at_last_report=IMPROVING),
            metric(1, "D", "DISCONTINUED"),
            metric(1, "E", "ALIVE", direction_at_last_report="not stated"),
        ],
    )
    result = counter_test(read_ledger(path).rows)
    assert result["undetermined"]["n"] == 2
    assert result["undetermined"]["discontinued"] == 1
    # every metric lands in exactly one category, nothing is dropped
    assert (
        result["improving"]["n"] + result["deteriorating"]["n"] + result["undetermined"]["n"]
        == result["denominator"]
        == 5
    )


def test_counter_test_difference_is_deteriorating_minus_improving(tmp_path):
    path = write_ledger(
        tmp_path / "metrics.csv",
        [
            metric(1, "A", "DISCONTINUED", direction_at_last_report=DETERIORATING),
            metric(1, "B", "ALIVE", direction_at_last_report=DETERIORATING),
            metric(1, "C", "ALIVE", direction_at_last_report=IMPROVING),
            metric(1, "D", "ALIVE", direction_at_last_report=IMPROVING),
        ],
    )
    result = counter_test(read_ledger(path).rows)
    assert result["deteriorating"]["share"] == pytest.approx(0.5)
    assert result["improving"]["share"] == pytest.approx(0.0)
    assert result["difference_pp"] == pytest.approx(50.0)
    assert result["verdict"] in ("WEAKENED", "SURVIVED")


def test_counter_test_verdict_is_not_determinable_without_both_directions(tmp_path):
    path = write_ledger(
        tmp_path / "metrics.csv", [metric(1, "A", "ALIVE", direction_at_last_report=IMPROVING)]
    )
    result = counter_test(read_ledger(path).rows)
    assert result["verdict"] == "NOT_DETERMINABLE"
    assert result["difference_pp"] is None


def test_counter_test_without_rulings_is_unavailable(empty_ledger):
    result = counter_test(empty_ledger.rows)
    assert result["available"] is False
    assert result["verdict"] == "NOT_DETERMINABLE"


# ==========================================================================
# 9. §7.4 - both pre-registered sensitivities, both deltas
# ==========================================================================


def test_sensitivity_publishes_both_deltas(split_ledger):
    listing = {cik: date(2020, 1, 1) for cik in (1, 2, 3, 4)}
    events = {
        3: (
            event(
                "8-K",
                date(2024, 1, 1),
                items=("4.02",),
                reason="Non-reliance",
                excluded=EXCL_WARRANT,
            ),
        )
    }
    result = sensitivity(
        split_ledger.rows,
        listing_dates=listing,
        events=events,
        outcome_available=True,
    )
    assert result["available"] is True
    assert set(result["runs"]) == {
        "as_preregistered",
        "benign_removed",
        "warrant_restatements_restored",
    }
    deltas = result["primary_difference_deltas_pp"]
    assert set(deltas) == {"benign_removed", "warrant_restatements_restored"}
    assert deltas["warrant_restatements_restored"] is not None


def test_benign_labels_leave_both_the_numerator_and_the_denominator(split_ledger):
    result = sensitivity(
        split_ledger.rows,
        listing_dates={cik: date(2020, 1, 1) for cik in (1, 2, 3, 4)},
        events={},
        outcome_available=True,
    )
    assert result["primary"]["n"] == 6 and result["primary"]["discontinued"] == 2
    assert result["benign_removed"]["n"] == 5 and result["benign_removed"]["discontinued"] == 1
    assert result["n_benign_removed"] == 1
    assert result["benign_labels"] == ["ASC 280 segment reclassification"]
    assert result["delta_pp"] == pytest.approx((1 / 5 - 2 / 6) * 100)


def test_sensitivity_labels_avoid_the_forbidden_vocabulary(split_ledger):
    result = sensitivity(
        split_ledger.rows,
        listing_dates={cik: date(2020, 1, 1) for cik in (1, 2, 3, 4)},
        events={},
        outcome_available=True,
    )
    assert not analysis.FORBIDDEN_PATTERN.search(result["primary"]["label"])
    assert not analysis.FORBIDDEN_PATTERN.search(result["benign_removed"]["label"])


def test_sensitivity_without_rulings_is_unavailable(empty_ledger):
    result = sensitivity(
        empty_ledger.rows, listing_dates={}, events={}, outcome_available=False
    )
    assert result["available"] is False and result["reason"]


# ==========================================================================
# 10. §7.5 - the de-SPAC projection arm
# ==========================================================================


def test_projection_arm_degrades_to_zero_coverage_when_nothing_is_transcribed():
    cohort = [
        cohort_row(1, arm="DESPAC"),
        cohort_row(2, arm="DESPAC"),
        cohort_row(3, arm="IPO"),
    ]
    result = projection_arm((), {}, cohort)
    assert result["available"] is False
    assert result["coverage"]["n_despac_issuers"] == 2
    assert result["coverage"]["n_with_transcribed_projections"] == 0
    assert result["coverage"]["rate"] == 0.0
    assert "hand" in result["reason"]


def test_missing_projection_file_reads_as_no_projections(tmp_path):
    assert read_projections(tmp_path / "projections.csv") == ()


def test_projections_are_read_not_parsed_from_a_filing(tmp_path):
    path = tmp_path / "projections.csv"
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["cik", "fiscal_year", "projected_revenue", "accession", "page", "caption"],
        )
        writer.writeheader()
        writer.writerow(
            {
                "cik": "1",
                "fiscal_year": "2022",
                "projected_revenue": "1,000,000",
                "accession": "0001104659-21-064530",
                "page": "F-12",
                "caption": "Certain Unaudited Prospective Financial Information",
            }
        )
        writer.writerow({"cik": "not a cik", "fiscal_year": "2022", "projected_revenue": "1"})
    rows = read_projections(path)
    assert len(rows) == 1
    assert rows[0].fiscal_year == "FY2022"
    assert rows[0].projected_revenue == pytest.approx(1_000_000.0)


def test_realisation_ratio_and_coverage_rate():
    cohort = [cohort_row(1, arm="DESPAC"), cohort_row(2, arm="DESPAC")]
    projections = (
        Projection(1, "FY2022", 1_000.0, "0001-21-000001", "F-12", "Prospective"),
        Projection(1, "FY2023", 2_000.0, "0001-21-000001", "F-12", "Prospective"),
    )
    realised = {1: {"FY2022": 500.0}}
    result = projection_arm(projections, realised, cohort)
    assert result["coverage"]["rate"] == pytest.approx(0.5)
    assert result["realisation"]["n_projections"] == 2
    assert result["realisation"]["n_pairs"] == 1
    assert result["realisation"]["median_ratio"] == pytest.approx(0.5)
    unmatched = next(p for p in result["per_projection"] if p["fiscal_year"] == "FY2023")
    assert unmatched["realised_revenue"] is None
    assert unmatched["realisation_ratio"] is None


def test_realised_revenue_takes_annual_periods_only():
    facts = {
        "facts": {
            "us-gaap": {
                "Revenues": {
                    "units": {
                        "USD": [
                            {"start": "2022-01-01", "end": "2022-12-31", "val": 900, "form": "10-K"},
                            {"start": "2022-01-01", "end": "2022-03-31", "val": 200, "form": "10-Q"},
                            {"start": "2023-01-01", "end": "2023-12-31", "val": 1100, "form": "10-K"},
                        ]
                    }
                }
            }
        }
    }
    assert realised_annual_revenue(facts) == {"FY2022": 900.0, "FY2023": 1100.0}


def test_realised_revenue_of_an_empty_payload_is_empty():
    assert realised_annual_revenue({}) == {}
    assert realised_annual_revenue({"facts": {}}) == {}


# ==========================================================================
# 11. §7.6 - the specification counter
# ==========================================================================


def test_spec_log_is_append_only_and_the_numbering_continues(tmp_path):
    path = tmp_path / "spec_log.csv"
    first = append_spec_runs(
        [SpecRun("§7.1 base rate", "yes (§7.1)", "ran")], timestamp="2026-08-28T00:00:00+00:00", path=path
    )
    second = append_spec_runs(
        [
            SpecRun("§7.2 primary", "yes (#1)", "ran"),
            SpecRun("§7.3 counter", "yes (§7.3)", "ran"),
        ],
        timestamp="2026-08-28T01:00:00+00:00",
        path=path,
    )
    assert first == 1 and second == 3
    with path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    assert [row["n"] for row in rows] == ["1", "2", "3"]
    assert rows[0]["specification"] == "§7.1 base rate"
    assert list(rows[0]) == list(analysis.SPEC_LOG_FIELDS)


def test_a_specification_that_produced_nothing_is_still_logged(empty_ledger):
    analysed = run(make_inputs(cohort=[cohort_row(1)], ledger=empty_ledger))
    assert len(analysed.spec_runs) == 6
    assert all("NOT RUN" in spec.result for spec in analysed.spec_runs)
    assert all(spec.preregistered for spec in analysed.spec_runs)


def test_the_published_line_names_the_preregistered_specification():
    assert spec_line(6) == "Specifications run: 6. Pre-registered: #1."


# ==========================================================================
# 12. The forbidden vocabulary - METHOD.md §7.2
# ==========================================================================


@pytest.mark.parametrize(
    "text",
    [
        "the discontinuation caused the restatement",
        "metric behaviour predicts later filings",
        "a redefinition leads to an adverse event",
        "a causal reading",
        "this leading to that",
    ],
)
def test_the_forbidden_pattern_catches_the_ruled_out_vocabulary(text):
    assert analysis.FORBIDDEN_PATTERN.search(text)


@pytest.mark.parametrize(
    "text",
    [
        "because the corpus is thin",
        "the lead auditor resigned",
        "unpredictable",
        "association with an interval",
    ],
)
def test_the_forbidden_pattern_does_not_trip_on_ordinary_words(text):
    assert not analysis.FORBIDDEN_PATTERN.search(text)


def test_the_publication_gate_raises_rather_than_publishing():
    payload = {"note": "the discontinuation caused the restatement"}
    with pytest.raises(AnalysisError) as excinfo:
        assert_no_forbidden_words(payload, "finding.json", skip_verbatim=False)
    assert "§7.2" in str(excinfo.value)


def test_no_output_field_contains_a_forbidden_word_on_the_empty_ledger_path(empty_ledger):
    """The primary tested path, checked strictly: values AND keys."""
    analysed = run(make_inputs(cohort=[cohort_row(1), cohort_row(2)], ledger=empty_ledger))
    for name, payload in (
        ("finding.json", dict(analysed.finding)),
        ("scoreboard.json", dict(analysed.scoreboard)),
        ("metrics.json", dict(analysed.metrics)),
    ):
        assert forbidden_word_findings(payload, skip_verbatim=False) == [], name
    for spec in analysed.spec_runs:
        assert forbidden_word_findings(
            [spec.specification, spec.preregistered, spec.result, spec.notes],
            skip_verbatim=False,
        ) == []


def test_no_composed_string_contains_a_forbidden_word_with_a_full_ledger(split_ledger):
    analysed = run(
        make_inputs(
            cohort=[cohort_row(i) for i in (1, 2, 3, 4, 5)],
            ledger=split_ledger,
            events={2: (event("NT 10-K", date(2024, 1, 1)),)},
            outcome_available=True,
        )
    )
    assert forbidden_word_findings(dict(analysed.finding), skip_verbatim=False) == []
    assert forbidden_word_findings(dict(analysed.scoreboard), skip_verbatim=False) == []
    assert forbidden_word_findings(dict(analysed.metrics), skip_verbatim=True) == []


def test_the_only_key_carrying_a_ruled_out_word_is_the_one_the_site_schema_fixes(
    split_ledger,
):
    """`cause_of_death` is `app/data.py`'s documented key, read by cohort.html.

    It is the single exception, it appears only in metrics.json, and the values
    under it are the reviewer's own words rather than this study's prose. Every
    other string anywhere in the published output is held to §7.2.
    """
    analysed = run(
        make_inputs(cohort=[cohort_row(i) for i in (1, 2, 3, 4, 5)], ledger=split_ledger)
    )
    offending_keys = {
        text
        for _, text in walk_strings(dict(analysed.metrics), skip_verbatim=False)
        if analysis.FORBIDDEN_PATTERN.search(text) and text.isidentifier()
    }
    assert offending_keys == {"cause_of_death"}


# ==========================================================================
# 13. The whole run, with and without the ledger
# ==========================================================================


def test_the_empty_ledger_path_is_wellformed_clearly_unavailable_and_serialisable(
    empty_ledger,
):
    analysed = run(make_inputs(cohort=[cohort_row(1), cohort_row(2)], ledger=empty_ledger))
    finding = json.loads(json.dumps(dict(analysed.finding)))

    assert finding["available"] is False
    assert "has not been written yet" in finding["reason"]
    for key in ("base_rate", "counter_test", "sensitivity", "primary_test"):
        assert finding[key]["available"] is False
        assert finding[key]["reason"]
    # nothing is invented in place of the missing rulings
    assert finding["cohort"]["n_metrics"] is None
    assert finding["cohort"]["n_keepers"] is None
    assert finding["cohort"]["n_movers"] is None
    assert "states" not in finding["base_rate"]
    assert "keepers" not in finding["primary_test"]
    assert "finding" not in finding  # no headline sentence without a ledger
    # the realised cohort is a committed public fact and is still published
    assert finding["cohort"]["n_issuers"] == 2


def test_the_exclusion_tally_is_empty_rather_than_zero_when_nothing_was_extracted(
    empty_ledger,
):
    analysed = run(make_inputs(cohort=[cohort_row(1)], ledger=empty_ledger))
    assert dict(analysed.finding)["exclusions_applied"] == {}
    assert dict(analysed.finding)["outcome_variable"]["available"] is False


def test_scoreboard_and_metrics_degrade_without_the_ledger(empty_ledger):
    analysed = run(make_inputs(cohort=[cohort_row(1)], ledger=empty_ledger))
    board = json.loads(json.dumps(dict(analysed.scoreboard)))
    ledger_payload = json.loads(json.dumps(dict(analysed.metrics)))
    assert board["available"] is False and board["issuers"] == [] and board["reason"]
    assert ledger_payload["available"] is False and ledger_payload["metrics"] == []


def test_the_full_run_matches_the_shapes_app_data_documents(split_ledger):
    analysed = run(
        make_inputs(
            cohort=[cohort_row(i) for i in (1, 2, 3, 4, 5)],
            ledger=split_ledger,
            events={2: (event("NT 10-K", date(2024, 1, 1)),)},
            outcome_available=True,
        )
    )
    finding = json.loads(json.dumps(dict(analysed.finding)))
    assert finding["as_at"] == config.AS_AT_DATE
    assert finding["cohort"]["n_metrics"] == 6
    assert finding["cohort"]["n_no_metrics_defined"] == 1
    assert finding["finding"]["segments"][0]["text"].startswith("Of the ")
    assert finding["base_rate"]["denominator"] == 6
    assert finding["primary_test"]["keepers"]["n"] == 1
    assert finding["primary_test"]["bootstrap_ci_pp"][0] <= finding["primary_test"]["bootstrap_ci_pp"][1]
    assert 0.0 <= finding["primary_test"]["fisher_p"] <= 1.0
    assert finding["counter_test"]["verdict"] in ("WEAKENED", "SURVIVED", "NOT_DETERMINABLE")
    assert finding["exclusions_applied"] == {
        EXCL_WARRANT: 0,
        EXCL_MECHANICAL_DELISTING: 0,
    }

    board = json.loads(json.dumps(dict(analysed.scoreboard)))
    assert board["fiscal_years"][0].startswith("FY")
    assert {issuer["group"] for issuer in board["issuers"]} <= {KEEPER, MOVER}
    for issuer in board["issuers"]:
        assert len(issuer["cells"]) == len(board["fiscal_years"])
        assert all(cell["state"] in (
            "ALIVE", "REDEFINED", "INTRODUCED", "DISCONTINUED", "NOT_DETERMINABLE", "NONE"
        ) for cell in issuer["cells"])

    ledger_payload = json.loads(json.dumps(dict(analysed.metrics)))
    assert len(ledger_payload["metrics"]) == 6
    entry = next(row for row in ledger_payload["metrics"] if row["name"] == "Benignly dropped")
    assert entry["state"] == "DISCONTINUED"
    assert entry["direction_at_last_report"] == IMPROVING
    assert entry["cause_of_death"]["benign"] is True
    assert entry["adjudication"]["row"].endswith(":6")
    assert entry["id"] == "3-benignly-dropped"


def test_the_run_never_reads_the_candidate_locator_file(split_ledger, monkeypatch):
    """Candidates are not rulings. The analysis must not read them."""

    def explode(*_args, **_kwargs):  # pragma: no cover - only runs on regression
        raise AssertionError("the analysis read the candidate locator ledger")

    monkeypatch.setattr(metrics_module, "read_rulings", explode)
    run(make_inputs(cohort=[cohort_row(i) for i in (1, 2, 3, 4, 5)], ledger=split_ledger))


def test_every_published_proportion_carries_a_denominator(split_ledger):
    analysed = run(
        make_inputs(
            cohort=[cohort_row(i) for i in (1, 2, 3, 4, 5)],
            ledger=split_ledger,
            events={2: (event("NT 10-K", date(2024, 1, 1)),)},
            outcome_available=True,
        )
    )
    finding = dict(analysed.finding)
    for row in finding["base_rate"]["states"]:
        assert isinstance(row["denominator"], int)
    for arm in ("keepers", "movers"):
        assert isinstance(finding["primary_test"][arm]["n"], int)
    for bucket in ("improving", "deteriorating", "undetermined"):
        assert isinstance(finding["counter_test"][bucket]["n"], int)
    for key in ("primary", "benign_removed"):
        assert isinstance(finding["sensitivity"][key]["n"], int)


def test_a_run_is_reproducible_end_to_end_under_the_fixed_seed(split_ledger):
    inputs = make_inputs(
        cohort=[cohort_row(i) for i in (1, 2, 3, 4, 5)],
        ledger=split_ledger,
        events={2: (event("NT 10-K", date(2024, 1, 1)),)},
        outcome_available=True,
    )
    first = json.dumps(dict(run(inputs).finding), sort_keys=True)
    second = json.dumps(dict(run(inputs).finding), sort_keys=True)
    assert first == second


def test_read_cohort_refuses_to_proceed_without_a_freeze(tmp_path):
    with pytest.raises(AnalysisError) as excinfo:
        analysis.read_cohort(tmp_path / "cohort_frozen.csv")
    assert "freeze" in str(excinfo.value).lower() or "frozen" in str(excinfo.value).lower()


def test_the_analysis_does_no_network_io(monkeypatch, split_ledger):
    """Nothing in analysis.py may fetch. The I/O shell is build_analysis.py."""
    import requests

    def explode(*_args, **_kwargs):  # pragma: no cover - only runs on regression
        raise AssertionError("the analysis attempted a network request")

    monkeypatch.setattr(requests.Session, "get", explode)
    monkeypatch.setattr(requests, "get", explode)
    run(
        make_inputs(
            cohort=[cohort_row(i) for i in (1, 2, 3, 4, 5)],
            ledger=split_ledger,
            events={2: (event("NT 10-K", date(2024, 1, 1)),)},
            outcome_available=True,
        )
    )


def test_the_closed_form_endpoints_are_exact():
    """At k=0 and k=n the Clopper-Pearson bound has a closed form.

    ci_high(0, n) = 1 - (alpha/2)**(1/n) and ci_low(n, n) = (alpha/2)**(1/n).
    These are the two cells most of this study's counts will sit in, so they are
    asserted against the formula rather than against a table.
    """
    alpha_half = (1 - config.CONFIDENCE_LEVEL) / 2
    for n in (3, 7, 20, 214):
        assert clopper_pearson(0, n).ci_high == pytest.approx(
            1 - math.pow(alpha_half, 1 / n), rel=1e-12
        )
        assert clopper_pearson(n, n).ci_low == pytest.approx(
            math.pow(alpha_half, 1 / n), rel=1e-12
        )


def test_a_ledger_holding_only_c4_rows_publishes_no_headline_sentence(tmp_path):
    """An issuer with no metric defined is a real ruling, but it is not a metric."""
    path = write_ledger(
        tmp_path / "metrics.csv",
        [metric(1, "", NO_METRICS_DEFINED), metric(2, "", NO_METRICS_DEFINED)],
    )
    ledger = read_ledger(path, cohort_ciks=[1, 2])
    assert ledger.available is True and ledger.metric_rows == ()
    finding = dict(run(make_inputs(cohort=[cohort_row(1), cohort_row(2)], ledger=ledger)).finding)
    assert "finding" not in finding
    assert finding["cohort"]["n_metrics"] == 0
    assert finding["cohort"]["n_no_metrics_defined"] == 2
    assert finding["base_rate"]["available"] is False


def test_the_descriptive_specifications_run_without_the_outcome_variable(split_ledger):
    """--offline, or a failed extraction: §7.1, §7.3 and the §7.4 shares still stand."""
    finding = dict(
        run(
            make_inputs(
                cohort=[cohort_row(i) for i in (1, 2, 3, 4, 5)],
                ledger=split_ledger,
                outcome_available=False,
            )
        ).finding
    )
    assert finding["base_rate"]["available"] is True
    assert finding["counter_test"]["available"] is True
    assert finding["sensitivity"]["available"] is True
    assert finding["primary_test"]["available"] is False
    assert finding["sensitivity"]["runs"]["as_preregistered"]["available"] is False
    assert (
        finding["sensitivity"]["primary_difference_deltas_pp"][
            "warrant_restatements_restored"
        ]
        is None
    )
    assert finding["exclusions_applied"] == {}


def test_the_power_statement_carries_the_realised_arm_sizes(primary_fixture):
    """METHOD.md §7.2 and §12: the power statement is published beside the result."""
    behaviours, listing = primary_fixture
    note = primary_test(
        behaviours, listing_dates=listing, events={}, outcome_available=True
    ).as_dict()["power_note"]
    assert "1 Keeper(s)" in note and "3 Mover(s)" in note
    assert "underpowered" in note.lower()
    assert not analysis.FORBIDDEN_PATTERN.search(note)


# ==========================================================================
# 14. Composing with the adjudication tool's own ledger schema
# ==========================================================================


def test_a_section_four_only_ledger_is_read_without_inventing_a_terminal_state(tmp_path):
    """`app/adjudicate.py` writes `pipeline.metrics.LEDGER_FIELDS`, which has
    `include` and no `state`. Those rows are included, awaiting a §5 ruling."""
    path = tmp_path / "metrics.csv"
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(metrics_module.LEDGER_FIELDS))
        writer.writeheader()
        writer.writerow(
            {
                "candidate_id": "abc123",
                "cik": "1",
                "accession": "0001104659-21-064530",
                "form": "10-K",
                "filing_date": "2021-02-25",
                "metric_name": "Adjusted Active Consumers",
                "defining_sentence": "We define Adjusted Active Consumers as ...",
                "include": "TRUE",
                "reviewer": "JL",
                "review_date": "2026-08-28",
                "rationale": "company-defined, quantitative, operating",
            }
        )
        writer.writerow({"candidate_id": "def456", "cik": "1", "metric_name": "Market size", "include": "FALSE"})
        writer.writerow({"candidate_id": "ghi789", "cik": "1", "metric_name": "Unread"})
    ledger = read_ledger(path, cohort_ciks=[1])

    assert ledger.available is False
    assert "no METHOD.md §5 terminal state" in ledger.reason
    assert len(ledger.awaiting_state) == 1
    assert len(ledger.ruled_out) == 1
    assert len(ledger.unruled) == 1
    assert ledger.invalid == ()  # a missing state is not a malformed row
    payload = ledger.as_dict()
    assert payload["n_included_awaiting_terminal_state"] == 1
    assert payload["n_ruled_out_at_inclusion"] == 1
    assert payload["n_not_yet_ruled"] == 1


def test_a_candidate_ruled_out_at_inclusion_never_reaches_section_seven(tmp_path):
    path = write_ledger(
        tmp_path / "metrics.csv",
        [
            metric(1, "Kept", "ALIVE", include="TRUE"),
            metric(1, "Market size estimate", "ALIVE", include="FALSE"),
        ],
        columns=LEDGER_COLUMNS + ("include",),
    )
    ledger = read_ledger(path, cohort_ciks=[1])
    assert [row.metric_name for row in ledger.rows] == ["Kept"]
    assert len(ledger.ruled_out) == 1


def test_the_candidate_id_becomes_the_published_metric_id(tmp_path):
    path = write_ledger(
        tmp_path / "metrics.csv",
        [metric(1, "Adjusted Active Consumers", "ALIVE", candidate_id="abc123")],
        columns=LEDGER_COLUMNS + ("candidate_id",),
    )
    assert read_ledger(path).rows[0].metric_id == "abc123"


def test_the_first_appearance_citation_falls_back_to_the_locator_columns(tmp_path):
    path = write_ledger(
        tmp_path / "metrics.csv",
        [
            metric(
                1,
                "Adjusted Active Consumers",
                "ALIVE",
                accession="0001104659-21-064530",
                form="10-K",
                filing_date="2021-02-25",
                defining_sentence="We define Adjusted Active Consumers as ...",
            )
        ],
        columns=LEDGER_COLUMNS + ("accession", "form", "filing_date", "defining_sentence"),
    )
    ledger = read_ledger(path, cohort_ciks=[1])
    entry = analysis.build_metrics_payload(ledger, [cohort_row(1)])["metrics"][0]
    citation = entry["first_appearance"]
    assert citation["accession"] == "0001104659-21-064530"
    assert citation["form"] == "10-K"
    assert citation["filed"] == "2021-02-25"
    assert citation["quote"].startswith("We define")


def test_an_incomplete_citation_is_none_rather_than_a_half_built_one(tmp_path):
    path = write_ledger(tmp_path / "metrics.csv", [metric(1, "Bare", "ALIVE")])
    ledger = read_ledger(path, cohort_ciks=[1])
    entry = analysis.build_metrics_payload(ledger, [cohort_row(1)])["metrics"][0]
    assert entry["first_appearance"] is None
    assert entry["last_appearance"] is None


def test_the_spec_log_records_the_actual_warrant_sensitivity_delta(split_ledger):
    """A regression: the log line must read the key the payload actually uses."""
    analysed = run(
        make_inputs(
            cohort=[cohort_row(i) for i in (1, 2, 3, 4, 5)],
            ledger=split_ledger,
            events={
                3: (
                    event(
                        "8-K",
                        date(2024, 1, 1),
                        items=("4.02",),
                        reason="Non-reliance",
                        excluded=EXCL_WARRANT,
                    ),
                )
            },
            outcome_available=True,
        )
    )
    warrant_line = next(
        spec for spec in analysed.spec_runs if "warrant restatements restored" in spec.specification
    )
    assert "None pp" not in warrant_line.result
    assert warrant_line.result.endswith("with Amendment 1 E1 restored")
    delta = dict(analysed.finding)["sensitivity"]["primary_difference_deltas_pp"][
        "warrant_restatements_restored"
    ]
    assert str(delta) in warrant_line.result


def test_an_unreadable_inclusion_ruling_is_recorded_not_guessed(tmp_path):
    path = write_ledger(
        tmp_path / "metrics.csv",
        [metric(1, "Ambiguous", "ALIVE", include="maybe")],
        columns=LEDGER_COLUMNS + ("include",),
    )
    ledger = read_ledger(path, cohort_ciks=[1])
    assert ledger.rows == ()
    assert len(ledger.invalid) == 1
    assert "neither a yes nor a no" in ledger.invalid[0]["problem"]
