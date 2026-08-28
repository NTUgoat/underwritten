"""Offline tests for corpus construction, candidate location, and the §6 test.

Every fixture is inline. Nothing here touches the network, and nothing here
depends on a cached document, so the suite is the same on a fresh clone.

Three properties are load-bearing and each has its own section below:

1. `&nbsp;` never defeats a match. SEC HTML writes U+00A0 inside dates and
   inside multi-word metric names. The bug it causes is invisible - it reads as
   "the phrase is absent", which is the one conclusion this study must not reach
   by accident. `tests/test_outcomes.py` guards the same property for the
   adverse-event extractor; this file guards it for the corpus and the metrics.
2. The machine never writes a ruling, and never destroys one.
3. A thin corpus yields NOT_DETERMINABLE, never a met absence test.
"""

from __future__ import annotations

import csv
from datetime import date, timedelta

import pytest

from pipeline import config
from pipeline.corpus import (
    Corpus,
    Document,
    DocumentFailure,
    ReportingPeriod,
    exhibit_files,
    html_to_text,
    is_exhibit_99,
    is_readable_filename,
    parse_directory,
    parse_submission_header,
    reporting_periods,
)
from pipeline.filings import Filing
from pipeline.metrics import (
    ABSENCE_TEST_MET,
    ABSENCE_TEST_NOT_MET,
    NOT_DETERMINABLE,
    RULING_FIELDS,
    MetricCandidate,
    PhraseError,
    absence_test,
    is_lenient_eligible,
    locate,
    locate_in_document,
    phrase_pattern,
    read_rulings,
    split_tolerant_pattern,
    write_candidates,
)

BASE_DATE = date(2021, 3, 31)


# --- fixtures --------------------------------------------------------------


def make_document(
    accession: str,
    text: str,
    *,
    form: str = "10-Q",
    filing_date: date | None = None,
    filename: str = "doc.htm",
    doc_type: str | None = None,
    is_primary: bool = True,
) -> Document:
    return Document(
        cik=1,
        accession=accession,
        form=form,
        filing_date=filing_date or BASE_DATE,
        report_date=None,
        filename=filename,
        doc_type=doc_type or form,
        is_primary=is_primary,
        url=f"https://www.sec.gov/Archives/edgar/data/1/{accession}/{filename}",
        sha256="0" * 64,
        n_bytes=len(text),
        text=text,
    )


def make_corpus(
    period_documents: list[list[Document]],
    failures: list[DocumentFailure] | None = None,
) -> Corpus:
    """A corpus whose periods are exactly the buckets given, in order."""
    periods: list[ReportingPeriod] = []
    documents: list[Document] = []
    for i, docs in enumerate(period_documents):
        accessions = tuple(dict.fromkeys(d.accession for d in docs))
        anchor_date = BASE_DATE + timedelta(days=91 * i)
        periods.append(
            ReportingPeriod(
                index=i,
                label=f"P{i}",
                anchor_accession=accessions[0] if accessions else f"none-{i}",
                anchor_form="10-Q",
                anchor_filing_date=anchor_date,
                period_end=None,
                start=anchor_date - timedelta(days=90),
                end=anchor_date,
                accessions=accessions,
            )
        )
        documents.extend(docs)
    return Corpus(
        cik=1,
        documents=tuple(documents),
        failures=tuple(failures or ()),
        periods=tuple(periods),
    )


def make_failure(accession: str) -> DocumentFailure:
    return DocumentFailure(
        cik=1,
        accession=accession,
        form="8-K",
        filing_date=BASE_DATE,
        filename="ex991.htm",
        url="https://www.sec.gov/x",
        stage="fetch",
        error="HTTP 500",
    )


# ===========================================================================
# 1. Text extraction - the &nbsp; property
# ===========================================================================


def test_nbsp_inside_a_metric_name_survives_extraction():
    """The bug this whole module is shaped around.

    A filing writes `Nights&nbsp;and&nbsp;Experiences&nbsp;Booked`. Extracted
    without normalisation the text holds U+00A0, a pattern written with an
    ordinary space misses it, and the metric reads as absent.
    """
    html = "<p>Nights&nbsp;and&nbsp;Experiences&nbsp;Booked were 100 million.</p>"
    text = html_to_text(html)

    assert "\xa0" not in text
    assert "Nights and Experiences Booked" in text
    assert phrase_pattern("Nights and Experiences Booked").search(text)


def test_tags_do_not_concatenate_adjacent_words():
    """`<b>Gross</b><span>Bookings</span>` must not become "GrossBookings"."""
    text = html_to_text("<td><b>Gross</b><span>Bookings</span></td>")
    assert text == "Gross Bookings"


def test_entities_are_decoded_in_both_paths():
    assert html_to_text("<p>Alpha&amp;Beta</p>") == "Alpha&Beta"
    assert html_to_text("Alpha&amp;Beta") == "Alpha&Beta"


def test_plain_text_passes_through_normalised():
    assert html_to_text("line one\n\nline   two") == "line one line two"


def test_script_and_style_content_is_dropped():
    text = html_to_text("<style>p{color:red}</style><p>Key Metrics</p>")
    assert "color" not in text
    assert "Key Metrics" in text


def test_malformed_markup_does_not_raise():
    assert "Active Consumers" in html_to_text("<p><b>Active Consumers</td></p")


def test_bytes_input_is_decoded():
    assert html_to_text(b"<p>Bookings</p>") == "Bookings"


# ===========================================================================
# 2. Exhibit discovery - EX-99.x on 8-K and 6-K (METHOD.md §6)
# ===========================================================================

# Reduction of https://www.sec.gov/Archives/edgar/data/1559720/
# 000119312521056952/index.json - Airbnb's Q4 2020 earnings 8-K.
DIRECTORY_JSON = {
    "directory": {
        "item": [
            {"name": "0001193125-21-056952-index.html", "type": "text.gif", "size": ""},
            {"name": "0001193125-21-056952.txt", "type": "text.gif", "size": "900000"},
            {"name": "d147144d8k.htm", "type": "text.gif", "size": "18722"},
            {"name": "d147144dex991.htm", "type": "text.gif", "size": "427777"},
            {"name": "g147144dsp034.jpg", "type": "image2.gif", "size": "191584"},
        ],
        "name": "/Archives/edgar/data/1559720/000119312521056952",
    }
}

SUBMISSION_HEADER = """<HTML><HEAD><TITLE>SEC EDGAR Submission</TITLE>
<!--
<SEC-HEADER>hdr.sgml
<TYPE>8-K
<ITEMS>2.02
-->
</HEAD><BODY><PRE>
&lt;DOCUMENT&gt;
&lt;TYPE&gt;8-K
&lt;SEQUENCE&gt;1
&lt;FILENAME&gt;d147144d8k.htm
&lt;/DOCUMENT&gt;
&lt;DOCUMENT&gt;
&lt;TYPE&gt;EX-99.1
&lt;SEQUENCE&gt;2
&lt;FILENAME&gt;d147144dex991.htm
&lt;/DOCUMENT&gt;
&lt;DOCUMENT&gt;
&lt;TYPE&gt;GRAPHIC
&lt;SEQUENCE&gt;3
&lt;FILENAME&gt;g147144dsp034.jpg
&lt;/DOCUMENT&gt;
</PRE></BODY></HTML>"""


def test_directory_listing_reads_names_and_sizes():
    entries = parse_directory(DIRECTORY_JSON)
    names = [name for name, _ in entries]

    assert "d147144dex991.htm" in names
    # The index.json `type` field is the browser icon ("text.gif"), never the
    # document type. Relying on it would classify every file identically.
    assert dict(entries)["d147144dex991.htm"] == 427777


def test_directory_listing_tolerates_a_malformed_payload():
    assert parse_directory(None) == ()
    assert parse_directory({"directory": {"item": "not a list"}}) == ()


def test_submission_header_maps_filenames_to_document_types():
    types = parse_submission_header(SUBMISSION_HEADER)

    assert types["d147144dex991.htm"] == "EX-99.1"
    assert types["d147144d8k.htm"] == "8-K"
    assert types["g147144dsp034.jpg"] == "GRAPHIC"


def test_header_type_finds_an_exhibit_the_filename_would_hide():
    """A real EX-99.1 named "q4earningsrelease.htm" is why the header is read."""
    assert is_exhibit_99("q4earningsrelease.htm", "EX-99.1")
    assert not is_exhibit_99("q4earningsrelease.htm", None)


@pytest.mark.parametrize(
    "filename",
    ["d147144dex991.htm", "ex-99_1.htm", "a8-kex991.htm", "tm2133456d1_ex99-1.htm"],
)
def test_filename_fallback_recognises_common_exhibit_names(filename):
    assert is_exhibit_99(filename, None)


def test_non_exhibits_are_not_collected():
    assert not is_exhibit_99("d147144d8k.htm", "8-K")
    assert not is_exhibit_99("d147144dex311.htm", "EX-31.1")


@pytest.mark.parametrize(
    "filename,readable",
    [
        ("d147144dex991.htm", True),
        ("release.txt", True),
        ("g147144dsp034.jpg", False),
        ("0001193125-21-056952-index.html", False),
        ("0001193125-21-056952-index-headers.html", False),
        ("R2.htm", False),
    ],
)
def test_readable_filenames(filename, readable):
    assert is_readable_filename(filename) is readable


class _StubClient:
    """Answers the two URLs `exhibit_files` asks for, and nothing else."""

    def __init__(self, *, header: str | None = SUBMISSION_HEADER):
        self._header = header

    def fetch_json(self, url):
        assert url.endswith("/index.json")
        return DIRECTORY_JSON

    def fetch_text(self, url):
        if self._header is None:
            raise OSError("header unavailable")
        return self._header


def _earnings_8k() -> Filing:
    return Filing(
        cik=1559720,
        accession="0001193125-21-056952",
        form="8-K",
        filing_date=date(2021, 2, 25),
        report_date=date(2021, 2, 25),
        primary_document="d147144d8k.htm",
        items=("2.02", "9.01"),
        size=0,
        is_xbrl=False,
    )


def test_exhibit_files_finds_the_furnished_earnings_release():
    found, failures = exhibit_files(_StubClient(), _earnings_8k())

    assert failures == ()
    assert found == (("d147144dex991.htm", "EX-99.1"),)


def test_a_failed_header_still_yields_exhibits_but_records_the_gap():
    """The filename fallback cannot see an exhibit named "release.htm", so an
    unreadable header is a coverage gap even when it recovers something."""
    found, failures = exhibit_files(_StubClient(header=None), _earnings_8k())

    assert [name for name, _ in found] == ["d147144dex991.htm"]
    assert [f.stage for f in failures] == ["header"]


class _BrokenDirectory(_StubClient):
    def fetch_json(self, url):
        raise OSError("HTTP 500")


def test_an_unreadable_directory_still_yields_exhibits_from_the_header():
    """Either source alone suffices; the failure is still recorded."""
    found, failures = exhibit_files(_BrokenDirectory(), _earnings_8k())

    assert [name for name, _ in found] == ["d147144dex991.htm"]
    assert [f.stage for f in failures] == ["index"]
    assert "HTTP 500" in failures[0].error


def test_a_filing_that_cannot_be_enumerated_at_all_is_a_recorded_gap():
    """No exhibits found must never be confused with no exhibits filed."""
    found, failures = exhibit_files(
        _BrokenDirectory(header=None), _earnings_8k()
    )

    assert found == ()
    assert sorted(f.stage for f in failures) == ["header", "index"]


# EDGAR's index.json is not always complete. For accession 0001193125-22-245113
# (Oaktree Strategic Income II, 8-K of 2022-09-15) it lists only the primary
# document, while the submission header lists two EX-99 exhibits that sec.gov
# serves at 24 KB and 29 KB. Reading index.json alone loses them - and losing a
# furnished exhibit is exactly how a still-reported metric reads as absent.
INCOMPLETE_DIRECTORY_JSON = {
    "directory": {
        "item": [
            {"name": "0001193125-22-245113-index.html", "type": "text.gif", "size": ""},
            {"name": "0001193125-22-245113.txt", "type": "text.gif", "size": ""},
            {"name": "d693479d8k.htm", "type": "text.gif", "size": "42956"},
        ]
    }
}

INCOMPLETE_DIRECTORY_HEADER = """<PRE>
&lt;DOCUMENT&gt;
&lt;TYPE&gt;8-K
&lt;FILENAME&gt;d693479d8k.htm
&lt;/DOCUMENT&gt;
&lt;DOCUMENT&gt;
&lt;TYPE&gt;EX-99.1
&lt;FILENAME&gt;d693479dex991.htm
&lt;/DOCUMENT&gt;
&lt;DOCUMENT&gt;
&lt;TYPE&gt;EX-99.2
&lt;FILENAME&gt;d693479dex992.htm
&lt;/DOCUMENT&gt;
</PRE>"""


def test_exhibits_the_directory_listing_omits_are_still_collected():
    class Oaktree(_StubClient):
        def fetch_json(self, url):
            return INCOMPLETE_DIRECTORY_JSON

        def fetch_text(self, url):
            return INCOMPLETE_DIRECTORY_HEADER

    found, failures = exhibit_files(Oaktree(), _earnings_8k())

    assert failures == ()
    assert [name for name, _ in found] == [
        "d693479dex991.htm",
        "d693479dex992.htm",
    ]


def test_a_blank_size_is_unknown_not_empty():
    """Treating a blank size as zero bytes would drop real documents."""
    sizes = dict(parse_directory(INCOMPLETE_DIRECTORY_JSON))

    assert sizes["0001193125-22-245113.txt"] is None
    assert sizes["d693479d8k.htm"] == 42956


# ===========================================================================
# 3. Reporting periods
# ===========================================================================


def _filing(form: str, filed: date, accession: str, report: date | None = None) -> Filing:
    return Filing(
        cik=1,
        accession=accession,
        form=form,
        filing_date=filed,
        report_date=report,
        primary_document="d.htm",
        items=(),
        size=0,
        is_xbrl=False,
    )


def test_periods_are_anchored_on_periodic_reports():
    filings = [
        _filing("10-Q", date(2022, 5, 10), "a", date(2022, 3, 31)),
        _filing("8-K", date(2022, 8, 1), "b"),
        _filing("10-Q", date(2022, 8, 9), "c", date(2022, 6, 30)),
    ]
    periods = reporting_periods(filings)

    assert [p.anchor_accession for p in periods] == ["a", "c"]
    # The 8-K filed between the two reports belongs to the later period.
    assert "b" in periods[1].accessions
    assert "b" not in periods[0].accessions


def test_an_amendment_does_not_open_a_second_period():
    filings = [
        _filing("10-K", date(2023, 2, 15), "a", date(2022, 12, 31)),
        _filing("10-K/A", date(2023, 4, 1), "b", date(2022, 12, 31)),
        _filing("10-Q", date(2023, 5, 5), "c", date(2023, 3, 31)),
    ]
    periods = reporting_periods(filings)

    assert [p.anchor_accession for p in periods] == ["a", "c"]


def test_filings_after_the_last_report_extend_it_rather_than_opening_a_gap():
    """An empty trailing period would read as absence and corrupt the §6 test."""
    filings = [
        _filing("10-Q", date(2024, 5, 1), "a", date(2024, 3, 31)),
        _filing("8-K", date(2024, 9, 30), "b"),
    ]
    periods = reporting_periods(filings)

    assert len(periods) == 1
    assert set(periods[0].accessions) == {"a", "b"}


def test_a_corpus_with_no_periodic_report_defines_no_periods():
    assert reporting_periods([_filing("8-K", date(2024, 1, 1), "a")]) == ()


# ===========================================================================
# 4. Phrase matching
# ===========================================================================


@pytest.mark.parametrize(
    "haystack",
    [
        "Nights and Experiences Booked",
        "NIGHTS AND EXPERIENCES BOOKED",
        "nights and experiences booked",
        "Nights  and   Experiences Booked",
        "Nights\xa0and\xa0Experiences Booked",
        "Nights\nand\nExperiences Booked",
        "Night and Experience Booked",
        "Nights-and-Experiences-Booked",
    ],
)
def test_phrase_matches_across_case_whitespace_and_number(haystack):
    assert phrase_pattern("Nights and Experiences Booked").search(haystack)


@pytest.mark.parametrize(
    "phrase,haystack",
    [
        ("Active Consumer", "we had 10 million Active Consumers"),
        ("Active Consumers", "we had 10 million Active Consumer accounts"),
        ("Delivery", "Deliveries grew 20%"),
        ("Deliveries", "Delivery volume grew"),
        ("Gross Bookings", "Gross Booking value"),
    ],
)
def test_simple_plural_and_singular_both_match(phrase, haystack):
    assert phrase_pattern(phrase).search(haystack)


@pytest.mark.parametrize(
    "haystack",
    ["Superactive Consumers", "Active Consumership", "ActiveConsumers"],
)
def test_word_boundaries_prevent_matching_inside_a_longer_word(haystack):
    assert not phrase_pattern("Active Consumer").search(haystack)


@pytest.mark.parametrize(
    "phrase,haystack",
    [
        # Regression: stem truncation used to emit `Rat(?:es?)?`, `Sal(?:es?)?`,
        # `Us(?:es?)?` - each of which matched a bare non-word stem, and "us" is
        # an ordinary English pronoun. A false appearance suppresses a real
        # absence run, so these must not match.
        ("Attach Rates", "Our Attach Rat improved slightly this quarter."),
        ("Adjusted Sales", "Adjusted Sal figures were strong."),
        ("Active Uses", "This helps Active us understand the business."),
        ("Net Revenues", "Net Revenu was restated."),
        ("Average Prices", "Average Pric moved higher."),
        ("Treasury Shares", "Treasury Shar were retired."),
    ],
)
def test_plural_tolerance_never_matches_a_truncated_stem(phrase, haystack):
    assert not phrase_pattern(phrase).search(haystack)


@pytest.mark.parametrize(
    "phrase,haystack",
    [
        ("Attach Rates", "our Attach Rate was 40%"),
        ("Attach Rate", "our Attach Rates were 40%"),
        ("Net Revenues", "Net Revenue grew 20%"),
        ("Adjusted Sales", "Adjusted Sale volume"),
        ("Gross Bookings", "Grosses Booking"),
    ],
)
def test_plural_tolerance_still_reaches_the_real_word_forms(phrase, haystack):
    assert phrase_pattern(phrase).search(haystack)


def test_every_alternative_in_a_token_pattern_is_a_whole_word_form():
    """No bare stems - each alternative must be reachable by adding/removing a
    plural ending, never by chopping a word in half."""
    from pipeline.metrics import _surface_forms

    assert _surface_forms("Uses") == {"Uses", "Use"}
    assert _surface_forms("Cases") == {"Cases", "Case"}
    assert _surface_forms("Losses") == {"Losses", "Losse", "Loss"}
    assert _surface_forms("Gross") == {"Gross", "Grosses"}
    assert "Rat" not in _surface_forms("Rates")
    assert "Us" not in _surface_forms("Uses")


def test_plural_tolerance_can_be_switched_off():
    assert not phrase_pattern("Active Consumer", plural_tolerant=False).search(
        "Active Consumers"
    )


def test_split_tag_tolerance_recovers_a_word_broken_by_formatting():
    """`<b>N</b>ights` extracts as "N ights"; the strict pattern cannot see it."""
    broken = html_to_text("<p><b>N</b>ights and Experiences Booked</p>")

    assert not phrase_pattern("Nights and Experiences Booked").search(broken)
    assert split_tolerant_pattern("Nights and Experiences Booked").search(broken)


def test_short_phrases_are_never_matched_leniently():
    assert not is_lenient_eligible("GMV")
    assert not is_lenient_eligible("Take Rate")
    assert is_lenient_eligible("Adjusted Active Consumers")


def test_an_empty_phrase_is_rejected_loudly():
    with pytest.raises(PhraseError):
        phrase_pattern("   ")


# ===========================================================================
# 5. Candidate location (METHOD.md §4) - mechanical, decides nothing
# ===========================================================================

S1_EXCERPT = (
    "Key Operating Metrics. In addition to the measures presented in our "
    "consolidated financial statements, we use the following key operating "
    "metrics to evaluate our business. We define Nights and Experiences Booked "
    "as the total number of nights and seats booked on our platform in a "
    "period, net of cancellations. We calculate Gross Booking Value as the "
    "dollar value of bookings on our platform in a period. Adjusted EBITDA is "
    "defined as net income adjusted for interest, taxes, depreciation and "
    "amortization. Revenue increased 30% year over year."
)


def _s1_document() -> Document:
    return make_document(
        "0001193125-20-000001",
        S1_EXCERPT,
        form="S-1",
        filing_date=date(2020, 11, 16),
        filename="d81668ds1.htm",
        doc_type="S-1",
    )


def test_we_define_yields_the_name_and_the_verbatim_sentence():
    candidates = locate_in_document(_s1_document())
    hit = next(c for c in candidates if c.locator == "we_define")

    assert hit.metric_name == "Nights and Experiences Booked"
    assert hit.defining_sentence.startswith("We define Nights and Experiences Booked as")
    assert hit.defining_sentence.endswith("net of cancellations.")


def test_we_calculate_is_located():
    candidates = locate_in_document(_s1_document())
    hit = next(c for c in candidates if c.locator == "we_calculate")

    assert hit.metric_name == "Gross Booking Value"


def test_is_defined_as_captures_only_the_capitalised_name():
    candidates = locate_in_document(_s1_document())
    hit = next(c for c in candidates if c.locator == "is_defined_as")

    assert hit.metric_name == "Adjusted EBITDA"


def test_section_heading_is_located_with_following_context():
    candidates = locate_in_document(_s1_document())
    hit = next(c for c in candidates if c.locator == "heading_key_operating_metrics")

    assert hit.metric_name == "Key Operating Metrics"
    assert "evaluate our business" in hit.defining_sentence


def test_char_offset_points_at_the_span_in_the_document_text():
    document = _s1_document()
    for candidate in locate_in_document(document):
        assert document.text[candidate.char_offset : candidate.char_offset + 4]
        assert candidate.sentence_char_offset <= candidate.char_offset
        assert (
            candidate.defining_sentence
            in document.text[candidate.sentence_char_offset :]
        )


def test_provenance_travels_with_every_candidate():
    for candidate in locate_in_document(_s1_document()):
        assert candidate.accession == "0001193125-20-000001"
        assert candidate.form == "S-1"
        assert candidate.filing_date == "2020-11-16"
        assert candidate.document == "d81668ds1.htm"
        assert candidate.url.startswith("https://www.sec.gov/")


def test_a_candidate_cannot_carry_a_ruling():
    """The dataclass has no field a ruling could be written into."""
    fields = set(MetricCandidate.__dataclass_fields__)
    assert fields.isdisjoint(set(RULING_FIELDS))


def test_nbsp_in_the_source_does_not_hide_a_definition():
    html = (
        "<p>We&nbsp;define&nbsp;Adjusted&nbsp;Active&nbsp;Consumers as buyers who "
        "transacted in the trailing twelve months.</p>"
    )
    document = make_document("acc-1", html_to_text(html))
    candidates = locate_in_document(document)

    assert any(c.metric_name == "Adjusted Active Consumers" for c in candidates)


def test_locate_orders_candidates_deterministically():
    docs = [
        make_document("acc-2", "We define Take Rate as revenue over volume.",
                      filing_date=date(2023, 2, 1)),
        make_document("acc-1", "We define Take Rate as revenue over bookings.",
                      filing_date=date(2022, 2, 1)),
    ]
    assert [c.accession for c in locate(docs)] == ["acc-1", "acc-2"]
    assert locate(docs) == locate(list(reversed(docs)))


def test_an_identical_repeated_sentence_collapses_to_one_candidate():
    """Boilerplate repeated verbatim is one thing to rule on, not two."""
    text = "We define Take Rate as X. We define Take Rate as X."
    candidates = [
        c for c in locate_in_document(make_document("a", text))
        if c.locator == "we_define"
    ]
    assert len(candidates) == 1


def test_two_different_definitions_of_the_same_name_are_both_kept():
    """A changed definition is the §5 REDEFINED signal - never collapse it."""
    text = (
        "We define Take Rate as revenue over bookings. "
        "We define Take Rate as revenue over gross merchandise value."
    )
    candidates = [
        c for c in locate_in_document(make_document("a", text))
        if c.locator == "we_define"
    ]
    assert len(candidates) == 2
    assert len({c.char_offset for c in candidates}) == 2


# ===========================================================================
# 6. The adjudication ledger - the machine never rules, and never overrules
# ===========================================================================


def _ledger_rows(path):
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def test_ruling_columns_are_written_empty():
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "metrics_candidates.csv"
        write_candidates(locate_in_document(_s1_document()), path)
        rows = _ledger_rows(path)

    assert rows
    for row in rows:
        for field in RULING_FIELDS:
            assert row[field] == "", f"{field} was written by the machine"


def test_a_human_ruling_survives_a_rerun_of_the_locator(tmp_path):
    path = tmp_path / "metrics_candidates.csv"
    candidates = locate_in_document(_s1_document())
    write_candidates(candidates, path)

    rows = _ledger_rows(path)
    target = rows[0]["candidate_id"]
    for row in rows:
        if row["candidate_id"] == target:
            row.update(
                include="yes",
                reviewer="JL",
                review_date="2026-08-28",
                rationale="Company-defined, quantitative, operating.",
            )
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)

    write_candidates(candidates, path)

    after = {row["candidate_id"]: row for row in _ledger_rows(path)}
    assert after[target]["include"] == "yes"
    assert after[target]["reviewer"] == "JL"
    assert after[target]["rationale"].startswith("Company-defined")


def test_a_ruling_is_preserved_when_the_locator_stops_finding_its_candidate(tmp_path):
    """Changing a regex must never silently discard a human's reading work."""
    path = tmp_path / "metrics_candidates.csv"
    candidates = locate_in_document(_s1_document())
    write_candidates(candidates, path)

    rows = _ledger_rows(path)
    orphan = rows[0]["candidate_id"]
    rows[0].update(include="no", reviewer="JL", review_date="2026-08-28",
                   rationale="Market-size estimate, not an operating metric.")
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)

    survivors = tuple(c for c in candidates if c.candidate_id != orphan)
    write_candidates(survivors, path)

    kept = {row["candidate_id"] for row in _ledger_rows(path)}
    assert orphan in kept
    assert read_rulings(path)[orphan]["include"] == "no"


def test_unruled_rows_are_regenerated_not_accumulated(tmp_path):
    path = tmp_path / "metrics_candidates.csv"
    candidates = locate_in_document(_s1_document())
    write_candidates(candidates, path)
    write_candidates(candidates[:1], path)

    assert len(_ledger_rows(path)) == 1


# ===========================================================================
# 7. The §6 absence test
# ===========================================================================

PRESENT = "Nights and Experiences Booked were 100 million in the quarter."
ABSENT = "Revenue increased 30% year over year and margins improved."
PHRASE = "Nights and Experiences Booked"


def _periods(pattern: str) -> Corpus:
    """One document per period; "1" means the phrase is in it, "0" means not."""
    return make_corpus(
        [
            [make_document(f"acc-{i}", PRESENT if flag == "1" else ABSENT)]
            for i, flag in enumerate(pattern)
        ]
    )


def test_a_metric_reported_throughout_does_not_meet_the_test():
    result = absence_test(_periods("111111"), PHRASE)

    assert result.status == ABSENCE_TEST_NOT_MET
    assert result.presence_vector == (True,) * 6
    assert result.trailing_absent_periods == 0


def test_four_consecutive_absent_periods_meet_the_test():
    result = absence_test(_periods("1110000"), PHRASE)

    assert result.status == ABSENCE_TEST_MET
    assert result.trailing_absent_periods == 4
    assert result.required_periods == config.DISCONTINUATION_PERIODS


def test_three_absent_periods_do_not_meet_the_test():
    result = absence_test(_periods("111000"), PHRASE)

    assert result.status == ABSENCE_TEST_NOT_MET
    assert result.trailing_absent_periods == 3
    assert "3 consecutive reporting period" in result.reason


def test_a_metric_that_came_back_does_not_meet_the_test():
    result = absence_test(_periods("10000100"), PHRASE)

    assert result.status == ABSENCE_TEST_NOT_MET
    assert result.max_absent_run == 4  # published as evidence, not as a verdict
    assert result.trailing_absent_periods == 2


def test_evidence_carries_first_and_last_appearance_verbatim():
    result = absence_test(_periods("1100000"), PHRASE)

    assert result.first_appearance is not None
    assert result.last_appearance is not None
    assert result.first_appearance.accession == "acc-0"
    assert result.last_appearance.accession == "acc-1"
    assert "Nights and Experiences Booked" in result.last_appearance.context
    assert result.last_appearance.filing_date == BASE_DATE
    assert result.last_appearance.match_mode == "strict"


def test_the_summary_row_is_publishable_and_carries_no_ruling():
    row = absence_test(_periods("1110000"), PHRASE).as_row()

    assert row["presence_vector"] == "1110000"
    assert row["status"] == ABSENCE_TEST_MET
    assert "DISCONTINUED" not in str(row["status"])
    assert row["last_appearance_accession"] == "acc-2"


# -- the guards that must return NOT_DETERMINABLE ---------------------------


def test_a_phrase_that_never_appears_is_not_determinable():
    result = absence_test(_periods("00000000"), PHRASE)

    assert result.status == NOT_DETERMINABLE
    assert "appears nowhere" in result.reason


def test_a_corpus_with_no_reporting_periods_is_not_determinable():
    result = absence_test(make_corpus([]), PHRASE)

    assert result.status == NOT_DETERMINABLE


def test_an_empty_period_inside_the_absent_run_is_not_determinable():
    """Absence from a period holding no document is absence of evidence."""
    corpus = make_corpus(
        [
            [make_document("acc-0", PRESENT)],
            [make_document("acc-1", ABSENT)],
            [],  # nothing retrieved for this period
            [make_document("acc-3", ABSENT)],
            [make_document("acc-4", ABSENT)],
            [make_document("acc-5", ABSENT)],
        ]
    )
    result = absence_test(corpus, PHRASE)

    assert result.status == NOT_DETERMINABLE
    assert "no readable document" in result.reason


def test_an_unreadable_document_inside_the_absent_run_is_not_determinable():
    corpus = make_corpus(
        [
            [make_document("acc-0", PRESENT)],
            [make_document("acc-1", ABSENT)],
            [make_document("acc-2", ABSENT)],
            [make_document("acc-3", ABSENT)],
            [make_document("acc-4", ABSENT)],
        ],
        failures=[make_failure("acc-3")],
    )
    result = absence_test(corpus, PHRASE)

    assert result.status == NOT_DETERMINABLE
    assert "could not be read" in result.reason


def test_a_gap_outside_the_absent_run_does_not_block_the_test():
    corpus = make_corpus(
        [
            [make_document("acc-0", PRESENT)],
            [make_document("acc-1", PRESENT)],
            [make_document("acc-2", ABSENT)],
            [make_document("acc-3", ABSENT)],
            [make_document("acc-4", ABSENT)],
            [make_document("acc-5", ABSENT)],
        ],
        failures=[make_failure("acc-0")],
    )
    assert absence_test(corpus, PHRASE).status == ABSENCE_TEST_MET


def test_no_code_path_returns_a_terminal_state_from_method_section_5():
    """DISCONTINUED is a human ruling. The test only reports its precondition."""
    for pattern in ("111111", "1110000", "000000", "10000100", "1"):
        assert absence_test(_periods(pattern), PHRASE).status in {
            ABSENCE_TEST_MET,
            ABSENCE_TEST_NOT_MET,
            NOT_DETERMINABLE,
        }


# -- what the corpus boundary is for ---------------------------------------


def test_an_appearance_in_a_furnished_exhibit_keeps_the_metric_alive():
    """The objection §6 exists to defeat: the metric moved to an EX-99.1.

    Without the furnished earnings release in the corpus, periods 3-6 look
    empty of the phrase and the test would be met on a metric still reported.
    """
    corpus = make_corpus(
        [
            [make_document("acc-0", PRESENT)],
            [make_document("acc-1", ABSENT)],
            [make_document("acc-2", ABSENT)],
            [make_document("acc-3", ABSENT)],
            [
                make_document("acc-4", ABSENT),
                make_document(
                    "acc-4",
                    PRESENT,
                    form="8-K",
                    filename="ex991.htm",
                    doc_type="EX-99.1",
                    is_primary=False,
                ),
            ],
        ]
    )
    result = absence_test(corpus, PHRASE)

    assert result.status == ABSENCE_TEST_NOT_MET
    assert result.last_appearance is not None
    assert result.last_appearance.doc_type == "EX-99.1"


def test_a_traced_rename_counts_as_an_appearance():
    """METHOD.md §6 tests the phrase "and every traced rename of it"."""
    corpus = make_corpus(
        [
            [make_document("acc-0", PRESENT)],
            [make_document("acc-1", "Total Nights Booked reached 110 million.")],
            [make_document("acc-2", "Total Nights Booked reached 120 million.")],
            [make_document("acc-3", "Total Nights Booked reached 130 million.")],
            [make_document("acc-4", "Total Nights Booked reached 140 million.")],
        ]
    )

    assert absence_test(corpus, PHRASE).status == ABSENCE_TEST_MET
    assert (
        absence_test(corpus, PHRASE, aliases=("Total Nights Booked",)).status
        == ABSENCE_TEST_NOT_MET
    )


def test_nbsp_in_a_later_filing_does_not_manufacture_an_absence():
    """The whole point, end to end, on documents built from real-shaped HTML."""
    later = html_to_text(
        "<p>Nights&nbsp;and&nbsp;Experiences&nbsp;Booked grew to 120 million.</p>"
    )
    corpus = make_corpus(
        [
            [make_document("acc-0", PRESENT)],
            [make_document("acc-1", ABSENT)],
            [make_document("acc-2", ABSENT)],
            [make_document("acc-3", ABSENT)],
            [make_document("acc-4", later)],
        ]
    )
    result = absence_test(corpus, PHRASE)

    assert result.status == ABSENCE_TEST_NOT_MET
    assert result.trailing_absent_periods == 0


def test_required_periods_must_be_positive():
    with pytest.raises(ValueError):
        absence_test(_periods("1000000"), PHRASE, required_periods=0)


def test_overlapping_heading_patterns_yield_one_candidate():
    """"Key Business Metrics" contains "Business Metrics" - that is one heading."""
    document = make_document(
        "acc-h",
        "Key Business Metrics We review the following measures to run the business.",
    )
    headings = [c for c in locate_in_document(document) if c.locator.startswith("heading")]

    assert [c.metric_name for c in headings] == ["Key Business Metrics"]


def test_a_heading_repeated_into_its_own_definition_is_not_doubled():
    document = make_document(
        "acc-d",
        "Adjusted EBITDA Adjusted EBITDA is defined as net loss adjusted for taxes.",
    )
    hit = next(c for c in locate_in_document(document) if c.locator == "is_defined_as")

    assert hit.metric_name == "Adjusted EBITDA"
    assert hit.defining_sentence.startswith("Adjusted EBITDA Adjusted EBITDA is defined")


def test_the_ledger_is_written_atomically(tmp_path):
    """A half-written ledger would lose rulings; the write goes via a sibling."""
    path = tmp_path / "metrics_candidates.csv"
    write_candidates(locate_in_document(_s1_document()), path)

    assert path.exists()
    assert not (tmp_path / "metrics_candidates.csv.partial").exists()
    assert list(tmp_path.iterdir()) == [path]


def test_periods_partition_the_filings_exactly():
    """Windows touch at their boundaries; first-window-wins keeps the partition
    exact. Double-counting would inflate a period's document count and could
    mask a real absence."""
    filings = [
        _filing("10-Q", date(2022, 5, 10), "a", date(2022, 3, 31)),
        _filing("8-K", date(2022, 5, 10), "b"),          # on the boundary
        _filing("8-K", date(2022, 6, 1), "c"),
        _filing("10-Q", date(2022, 8, 9), "d", date(2022, 6, 30)),
        _filing("10-K", date(2023, 2, 15), "e", date(2022, 12, 31)),
        _filing("8-K", date(2023, 6, 1), "f"),           # after the last anchor
    ]
    periods = reporting_periods(filings)
    assigned = [a for p in periods for a in p.accessions]

    assert sorted(assigned) == ["a", "b", "c", "d", "e", "f"]
    assert len(assigned) == len(set(assigned))          # nothing counted twice
    assert all(p.accessions for p in periods)           # no empty period
    # A filing on a shared boundary date belongs to the earlier period.
    assert "b" in periods[0].accessions
