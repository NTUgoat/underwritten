"""METHOD.md §9 enforced mechanically rather than remembered.

Each constraint below is one a person genuinely might drop on a third draft at
one in the morning. The dangerous one is the kill-criteria count: a position
that quietly ships with two has no published falsifier, which is the single
thing this publication claims every position always carries.
"""

from __future__ import annotations

import pytest

from pipeline.compile_note import NoteError
from pipeline.compile_positions import compile_positions

HEAD = """---
as_at: 2026-09-06
author: Jex Lin
---

## Lemonade, Inc. (LMND) | cik=1691421 | OPEN | opened=2026-09-06

### Stance
{stance}

### Variant perception
The market treats the loss ratio as structural.

### What would have to be true
- Loss ratio keeps improving
- Growth does not stall

### Hurdle
| line | bps | source |
|---|---|---|
| US 10-year risk-free | 420 | Damodaran, January 2026 |
| Equity risk premium | 480 | {erp_source} |
total: {total}
edition: {edition}

### Expected spread
central={central} low={low} high={high}

### Downside
A stress case where the loss ratio reverts.

### Kill criteria
{kills}

### Disclosure
The author holds no position in Lemonade.
"""

GOOD_KILLS = (
    "1. 2027-06-30 | machine | Gross loss ratio exceeds 75% in any 10-Q\n"
    "2. 2027-12-31 | machine | Customer count declines two consecutive quarters\n"
    "3. 2028-06-30 | manual | Management stops reporting in force premium"
)

DEFAULTS = dict(
    stance="Own Lemonade for the loss-ratio inflection.",
    erp_source="Damodaran, January 2026",
    total="900",
    edition="Damodaran, January 2026",
    central="350",
    low="120",
    high="640",
    kills=GOOD_KILLS,
)


def write(tmp_path, **overrides):
    path = tmp_path / "positions.md"
    path.write_text(HEAD.format(**{**DEFAULTS, **overrides}), encoding="utf-8")
    return path


def test_compliant_position_compiles(tmp_path):
    data = compile_positions(write(tmp_path))
    position = data["positions"][0]
    assert position["ticker"] == "LMND"
    assert position["cik"] == 1691421
    assert position["status"] == "OPEN"
    assert position["hurdle"]["total_bps"] == 900
    assert len(position["hurdle"]["lines"]) == 2
    assert position["expected_spread_bps"]["central"] == 350
    assert len(position["kill_criteria"]) == 3
    assert position["horizons_years"] == [10, 20]


def test_stance_over_eight_words_fails(tmp_path):
    path = write(
        tmp_path,
        stance="Own Lemonade for the loss ratio inflection that the market has not priced.",
    )
    with pytest.raises(NoteError, match="stance is"):
        compile_positions(path)


def test_uncited_hurdle_line_fails(tmp_path):
    with pytest.raises(NoteError, match="cites no source"):
        compile_positions(write(tmp_path, erp_source=""))


def test_hurdle_that_does_not_sum_fails(tmp_path):
    """An arithmetic slip in a published cost of capital is not survivable."""
    with pytest.raises(NoteError, match="sum to"):
        compile_positions(write(tmp_path, total="1120"))


def test_missing_damodaran_edition_fails(tmp_path):
    path = tmp_path / "positions.md"
    path.write_text(
        HEAD.format(**DEFAULTS).replace("edition: Damodaran, January 2026\n", ""),
        encoding="utf-8",
    )
    with pytest.raises(NoteError, match="edition"):
        compile_positions(path)


def test_incoherent_spread_fails(tmp_path):
    with pytest.raises(NoteError, match="incoherent"):
        compile_positions(write(tmp_path, central="350", low="640", high="120"))


@pytest.mark.parametrize("kills,n", [
    (GOOD_KILLS.rsplit("\n", 1)[0], 2),
    (GOOD_KILLS + "\n4. 2029-01-01 | manual | A fourth", 4),
])
def test_wrong_number_of_kill_criteria_fails(tmp_path, kills, n):
    with pytest.raises(NoteError, match="kill criteria"):
        compile_positions(write(tmp_path, kills=kills))


@pytest.mark.parametrize(
    "phrase",
    ["target price", "price target", "IRR", "MOIC", "Sharpe ratio"],
)
def test_forbidden_vocabulary_fails(tmp_path, phrase):
    """§9 excludes these deliberately - they describe a different investor."""
    path = tmp_path / "positions.md"
    path.write_text(
        HEAD.format(**DEFAULTS).replace(
            "A stress case where the loss ratio reverts.",
            f"A stress case, and our {phrase} implies more.",
        ),
        encoding="utf-8",
    )
    with pytest.raises(NoteError, match="different kind of investor"):
        compile_positions(path)


def test_alpha_matches_as_a_word_not_a_substring(tmp_path):
    """'Alphabet' must not trip the benchmark-relative-alpha check."""
    path = tmp_path / "positions.md"
    path.write_text(
        HEAD.format(**DEFAULTS).replace(
            "A stress case where the loss ratio reverts.",
            "A stress case, as Alphabet's own filings show for shareholders.",
        ),
        encoding="utf-8",
    )
    assert compile_positions(path)["positions"]


def test_kill_criteria_carry_dates_and_machine_flag(tmp_path):
    kills = compile_positions(write(tmp_path))["positions"][0]["kill_criteria"]
    assert [k["by_date"] for k in kills] == ["2027-06-30", "2027-12-31", "2028-06-30"]
    assert [k["machine_checkable"] for k in kills] == [True, True, False]
    assert all(k["status"] == "NOT_TRIGGERED" for k in kills)
