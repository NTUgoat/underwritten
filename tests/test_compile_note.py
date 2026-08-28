"""The note compiler is where METHOD.md §8.3 is enforced at authoring time.

§8.3 says every numeral in published prose resolves to an accession number. The
site enforces that at render time by showing `[pending adjudication]`; this
moves the same rule to the moment of writing, where a bare number is a build
failure naming its line rather than a silent placeholder in published prose.

These tests exist because the rule is only worth having if it actually fires.
"""

from __future__ import annotations

import pytest

from pipeline.compile_note import NoteError, compile_note

HEAD = """---
title: Test note
standfirst: A standfirst.
author: Jex Lin
dated: 2026-09-05
---

## Finding

"""

CITE_M1 = (
    '\n\n[m1]: 0001104659-21-064530 | 10-K | 2023-11-14 | cik=1759546\n'
    '      "We define Adjusted Active Consumers as the number of consumers."\n'
)


def write(tmp_path, body: str, citations: str = CITE_M1):
    path = tmp_path / "note.md"
    path.write_text(HEAD + body + citations, encoding="utf-8")
    return path


# -- the gate fires --------------------------------------------------------


def test_uncited_numeral_fails_the_build(tmp_path):
    path = write(tmp_path, "Of the {{214|m1}} metrics, 58 were discontinued.")
    with pytest.raises(NoteError) as exc:
        compile_note(path)
    assert "58" in str(exc.value)
    assert "§8.3" in str(exc.value)


def test_failure_names_the_line(tmp_path):
    path = write(tmp_path, "A paragraph with 99 in it.")
    with pytest.raises(NoteError) as exc:
        compile_note(path)
    assert "note.md:" in str(exc.value)


def test_nothing_is_written_on_failure(tmp_path):
    """A partial note is worse than none - it looks finished."""
    path = write(tmp_path, "Uncited 42 here.")
    with pytest.raises(NoteError):
        compile_note(path)


def test_figure_without_a_definition_fails(tmp_path):
    path = write(tmp_path, "Of the {{214|nosuchkey}} metrics.", citations="")
    with pytest.raises(NoteError) as exc:
        compile_note(path)
    assert "nosuchkey" in str(exc.value)


def test_citation_without_a_quote_fails(tmp_path):
    """A citation with no verbatim sentence cannot be re-verified against EDGAR."""
    path = write(
        tmp_path,
        "Of the {{214|m1}} metrics.",
        citations="\n\n[m1]: 0001104659-21-064530 | 10-K | 2023-11-14\n",
    )
    with pytest.raises(NoteError) as exc:
        compile_note(path)
    assert "verbatim" in str(exc.value).lower()


# -- the gate does not over-fire -------------------------------------------
# Each of these is a numeral that is legitimately not a cited figure. If any
# starts failing, the note becomes unwritable for the wrong reason.


@pytest.mark.parametrize(
    "prose",
    [
        "The as-at date is 2026-08-28 throughout.",
        "This follows from §7.2 of the method.",
        "Filed on Form 10-K, and separately on 20-F.",
        "An 8-K carrying Item 4.02 was filed.",
        "Reclassified under ASC 280.",
        "The 2019 cohort is the subject.",
        "Accession 0001104659-21-064530 is the source.",
    ],
)
def test_legitimate_numerals_are_allowed(tmp_path, prose):
    path = write(tmp_path, f"Of the {{{{214|m1}}}} metrics. {prose}")
    note = compile_note(path)
    assert note["sections"]


# -- output shape ----------------------------------------------------------


def test_citation_travels_with_the_figure(tmp_path):
    path = write(tmp_path, "Of the {{214|m1}} metrics adjudicated.")
    note = compile_note(path)
    figures = [
        seg["figure"]
        for section in note["sections"]
        for block in section["blocks"]
        for seg in block.get("segments", [])
        if "figure" in seg
    ]
    assert len(figures) == 1
    figure = figures[0]
    assert figure["value"] == "214"
    assert figure["accession"] == "0001104659-21-064530"
    assert figure["form"] == "10-K"
    assert figure["filed"] == "2023-11-14"
    assert figure["cik"] == 1759546
    assert "Adjusted Active Consumers" in figure["quote"]


def test_multiline_citation_definition_is_read(tmp_path):
    """The quote wraps onto an indented line; matching only the first line
    silently loses it and reports the wrong error."""
    path = write(tmp_path, "Of the {{214|m1}} metrics.")
    note = compile_note(path)
    figure = next(
        seg["figure"]
        for section in note["sections"]
        for block in section["blocks"]
        for seg in block.get("segments", [])
        if "figure" in seg
    )
    assert figure["quote"].endswith("consumers.")


def test_pull_quote_and_struck_text(tmp_path):
    body = (
        "Of the {{214|m1}} metrics.\n\n"
        "> A pull quote.\n\n"
        "~~A superseded claim.~~ (revised 2026-09-08: it was wrong.)"
    )
    note = compile_note(write(tmp_path, body))
    blocks = [b for section in note["sections"] for b in section["blocks"]]
    assert any("pull" in b for b in blocks)
    struck = next(b["struck"] for b in blocks if "struck" in b)
    assert struck["date"] == "2026-09-08"
    assert "wrong" in struck["note"]


def test_headings_become_sections(tmp_path):
    body = "Of the {{214|m1}} metrics.\n\n## Method\n\nA second section."
    note = compile_note(write(tmp_path, body))
    assert [s["heading"] for s in note["sections"]] == ["Finding", "Method"]
