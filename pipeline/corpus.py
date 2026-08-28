"""The issuer's filed corpus: every document, as text, organised into periods.

METHOD.md §6 defines the corpus as *every document the issuer filed or furnished
to EDGAR* - 10-K, 10-Q, 20-F, 40-F, 8-K and 6-K, **including furnished
earnings-release exhibits (EX-99.x)**. That last clause is not decoration. The
whole point of the four-period absence test is to defeat the objection that a
metric did not disappear but merely *moved* - most often out of the annual
report and into a quarterly earnings release, which a foreign private issuer
furnishes on 6-K rather than filing on 10-Q. A corpus built from primary
documents alone would manufacture exactly the false DISCONTINUED verdicts the
test exists to prevent, so exhibit discovery here is mandatory, not optional.

Three rules govern this module:

1. **Whitespace is normalised through `outcomes.normalise_whitespace`.** SEC HTML
   is full of `&nbsp;` (U+00A0) sitting inside dates and inside multi-word
   phrases. A pattern written with an ordinary space silently never matches, and
   the failure looks like "the phrase is absent" rather than like an error. That
   bug already cost this project once; see `tests/test_outcomes.py`.
2. **One unreadable document never aborts an issuer.** Every failure is caught,
   recorded as a `DocumentFailure`, and carried alongside the corpus - because a
   gap in coverage is evidence *against* being able to call a metric absent, and
   the absence test in `metrics.py` reads these records to decide when the answer
   must be NOT_DETERMINABLE.
3. **Nothing here interprets a filing.** This module fetches, decodes, and
   organises. Locating metrics and running the §6 test live in `metrics.py`.
"""

from __future__ import annotations

import html as html_module
import logging
import re
import warnings
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Iterable, Sequence

from bs4 import BeautifulSoup, XMLParsedAsHTMLWarning

# A handful of documents filed with a .htm extension are actually XML. Reading
# them with an HTML parser is deliberate here - this module wants text out of
# whatever the issuer filed, not a faithful tree - so bs4's advisory warning is
# noise that would otherwise fire hundreds of times per issuer.
warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)

from . import config
from .edgar import (
    EdgarClient,
    accession_no_dashes,
    document_url,
    normalise_cik,
)
from .filings import Filing, complete_index, of_forms
from .outcomes import normalise_whitespace

logger = logging.getLogger(__name__)


# --- What counts as a corpus document --------------------------------------

# Forms whose EX-99.x exhibits carry furnished earnings releases (METHOD.md §6).
EXHIBIT_FORMS: tuple[str, ...] = ("8-K", "6-K", "8-K/A", "6-K/A")

# EDGAR labels the exhibit type in the submission header as EX-99, EX-99.1, ...
EXHIBIT_TYPE_PATTERN = re.compile(r"^ex[\s\-_.]*99", re.IGNORECASE)

# Fallback when the submission header is unreadable. Filing agents name exhibit
# files inconsistently - "d147144dex991.htm", "ex-99_1.htm", "a8-kex991.htm" -
# so this is deliberately loose. It is only ever a fallback: the header is
# authoritative, and a loose fallback over-collects rather than under-collects,
# which is the safe direction for an absence test.
EXHIBIT_FILENAME_PATTERN = re.compile(r"ex(?:hibit)?[\s\-_.]*99", re.IGNORECASE)

# Extensions we can turn into text. Everything else (graphics, XBRL instance
# documents, PDFs) carries no prose the absence test could read.
TEXT_EXTENSIONS: tuple[str, ...] = (".htm", ".html", ".txt")

# EDGAR generates these into every filing directory. They are navigation, not
# filed content, and the full-submission .txt duplicates every other document.
_GENERATED_SUFFIXES = ("-index.html", "-index.htm", "-index-headers.html")
_XBRL_VIEWER = re.compile(r"^R\d+\.htm$", re.IGNORECASE)

# A document larger than this is not parsed. The only things this size on EDGAR
# are full-submission .txt files with uuencoded graphics inline. Recorded as a
# failure rather than skipped silently, so it shows up in the coverage report.
MAX_DOCUMENT_BYTES = 64 * 1024 * 1024

# Periods are anchored on periodic reports only. An amendment re-reports a
# period that already has an anchor and must not create a second one.
PERIOD_ANCHOR_FORMS: tuple[str, ...] = ("10-K", "20-F", "40-F", "10-Q")

# Extracted text is cached content-addressed by the source document's SHA-256,
# so it can never go stale against the bytes it was derived from.
TEXT_CACHE = config.RAW / "text"


# --- Records ---------------------------------------------------------------


@dataclass(frozen=True)
class Document:
    """One readable document inside one filing. Immutable."""

    cik: int
    accession: str
    form: str
    filing_date: date
    report_date: date | None
    filename: str
    doc_type: str
    is_primary: bool
    url: str
    sha256: str
    n_bytes: int
    text: str

    @property
    def n_chars(self) -> int:
        return len(self.text)


@dataclass(frozen=True)
class DocumentFailure:
    """A document that could not be read, and why.

    Never discarded. A failure inside the window where a metric looks absent is
    the difference between DISCONTINUED and NOT_DETERMINABLE.
    """

    cik: int
    accession: str
    form: str
    filing_date: date
    filename: str
    url: str
    stage: str  # "index" | "fetch" | "size" | "parse"
    error: str


@dataclass(frozen=True)
class ReportingPeriod:
    """One reporting period, anchored on a periodic report the issuer filed.

    Derived from the issuer's own filing rhythm rather than from the calendar,
    because the rhythm differs (10-Q filers report four times a year, many 20-F
    filers twice) and the §6 test counts *reporting periods*, not months.
    """

    index: int
    label: str
    anchor_accession: str
    anchor_form: str
    anchor_filing_date: date
    period_end: date | None
    start: date
    end: date
    accessions: tuple[str, ...]


@dataclass(frozen=True)
class Corpus:
    """Everything an issuer filed, as text, with its gaps recorded."""

    cik: int
    documents: tuple[Document, ...]
    failures: tuple[DocumentFailure, ...]
    periods: tuple[ReportingPeriod, ...]

    @property
    def n_documents(self) -> int:
        return len(self.documents)

    @property
    def n_failures(self) -> int:
        return len(self.failures)

    def documents_by_accession(self) -> dict[str, tuple[Document, ...]]:
        grouped: dict[str, list[Document]] = {}
        for doc in self.documents:
            grouped.setdefault(doc.accession, []).append(doc)
        return {key: tuple(value) for key, value in grouped.items()}

    def failures_by_accession(self) -> dict[str, tuple[DocumentFailure, ...]]:
        grouped: dict[str, list[DocumentFailure]] = {}
        for failure in self.failures:
            grouped.setdefault(failure.accession, []).append(failure)
        return {key: tuple(value) for key, value in grouped.items()}

    def coverage(self) -> dict[str, int | str]:
        """A publishable summary of what was read and what was not."""
        exhibits = sum(1 for d in self.documents if not d.is_primary)
        return {
            "cik": self.cik,
            "documents": len(self.documents),
            "exhibits": exhibits,
            "primary_documents": len(self.documents) - exhibits,
            "characters": sum(d.n_chars for d in self.documents),
            "failures": len(self.failures),
            "periods": len(self.periods),
        }


# --- Text extraction -------------------------------------------------------

_TAG_HINT = re.compile(r"<[a-zA-Z!/?]")
_TAG_STRIP = re.compile(r"<[^>]{0,4000}>")


def looks_like_markup(raw: str) -> bool:
    """Whether the payload should go through an HTML parser."""
    return bool(_TAG_HINT.search(raw[:4096]))


def _decode(raw: bytes | str) -> str:
    if isinstance(raw, str):
        return raw
    return raw.decode("utf-8", errors="replace")


def html_to_text(raw: bytes | str) -> str:
    """Document bytes to whitespace-normalised text.

    The separator matters. SEC filing agents split phrases across formatting
    tags - `<b>Nights</b><span>and Experiences Booked</span>` - so extracting
    with no separator would concatenate words and destroy the phrase. A single
    space between text nodes is what `outcomes._filing_text` already does, and
    both must agree or the same document would read differently in two places.

    Never raises. A document that defeats every parser degrades to a regex tag
    strip rather than taking an issuer's whole corpus down with it.
    """
    text = _decode(raw)

    if not looks_like_markup(text):
        return normalise_whitespace(html_module.unescape(text)).strip()

    for parser in ("lxml", "html.parser"):
        try:
            soup = BeautifulSoup(text, parser)
            for tag in soup(["script", "style"]):
                tag.decompose()
            return normalise_whitespace(soup.get_text(" ", strip=True)).strip()
        except Exception as exc:  # noqa: BLE001 - fall through to the next parser
            logger.debug("HTML parse with %s failed: %s", parser, exc)

    stripped = _TAG_STRIP.sub(" ", text)
    return normalise_whitespace(html_module.unescape(stripped)).strip()


def _text_cache_path(sha256: str) -> Path:
    return TEXT_CACHE / sha256[:2] / sha256[2:4] / f"{sha256}.txt"


def _cached_text(sha256: str) -> str | None:
    path = _text_cache_path(sha256)
    if not path.exists():
        return None
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:  # a broken cache entry is recoverable, not fatal
        logger.warning("Text cache unreadable at %s: %s", path, exc)
        return None


def _store_text(sha256: str, text: str) -> None:
    path = _text_cache_path(sha256)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    except OSError as exc:  # the text cache is an optimisation only
        logger.warning("Could not write text cache at %s: %s", path, exc)


# --- Filing directory and exhibit discovery --------------------------------


def filing_directory_url(cik: int | str, accession: str) -> str:
    cik_plain = str(int(normalise_cik(cik)))
    return (
        f"{config.SEC_WWW}/Archives/edgar/data/{cik_plain}/"
        f"{accession_no_dashes(accession)}"
    )


def filing_index_json_url(cik: int | str, accession: str) -> str:
    return f"{filing_directory_url(cik, accession)}/index.json"


def submission_header_url(cik: int | str, accession: str) -> str:
    return f"{filing_directory_url(cik, accession)}/{accession}-index-headers.html"


def parse_directory(payload: object) -> tuple[tuple[str, int], ...]:
    """`(filename, size)` for every file in a filing directory index.json.

    The `type` field in index.json is the icon EDGAR shows in its file browser
    ("text.gif"), not the document type - so it is deliberately ignored here.
    Document types come from the submission header instead.
    """
    if not isinstance(payload, dict):
        return ()
    items = payload.get("directory", {}).get("item", [])
    if not isinstance(items, list):
        return ()

    out: list[tuple[str, int]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        raw_size = str(item.get("size") or "").strip()
        size = int(raw_size) if raw_size.isdigit() else 0
        out.append((name, size))
    return tuple(out)


_HEADER_TYPE = re.compile(r"<TYPE>\s*([^\r\n<]+)")
_HEADER_FILENAME = re.compile(r"<FILENAME>\s*([^\r\n<]+)")


def parse_submission_header(raw: str) -> dict[str, str]:
    """`{lowercased filename: EDGAR document type}` from an index-headers page.

    The page carries the filing's SGML header twice: once inside an HTML comment
    (submission level only) and once HTML-escaped in a `<PRE>` block with one
    `<DOCUMENT>` stanza per file. Unescaping first and then splitting on
    `<DOCUMENT>` reads the per-file stanzas and cannot stray across them.
    """
    text = html_module.unescape(raw)
    types: dict[str, str] = {}
    for chunk in text.split("<DOCUMENT>")[1:]:
        type_match = _HEADER_TYPE.search(chunk)
        name_match = _HEADER_FILENAME.search(chunk)
        if not type_match or not name_match:
            continue
        filename = name_match.group(1).strip()
        if filename:
            types[filename.lower()] = type_match.group(1).strip()
    return types


def is_readable_filename(filename: str) -> bool:
    lowered = filename.lower()
    if not lowered.endswith(TEXT_EXTENSIONS):
        return False
    if any(lowered.endswith(suffix) for suffix in _GENERATED_SUFFIXES):
        return False
    return not _XBRL_VIEWER.match(filename)


def is_exhibit_99(filename: str, doc_type: str | None) -> bool:
    """Whether this file is an EX-99.x exhibit.

    The header type is authoritative when present, because filing agents name
    the file whatever they like - "q4earningsrelease.htm" is a real EX-99.1.
    The filename pattern is the fallback for when the header cannot be read.
    """
    if doc_type:
        return bool(EXHIBIT_TYPE_PATTERN.match(doc_type.strip()))
    return bool(EXHIBIT_FILENAME_PATTERN.search(filename))


def exhibit_files(
    client: EdgarClient, filing: Filing
) -> tuple[tuple[tuple[str, str], ...], tuple[DocumentFailure, ...]]:
    """EX-99.x files in one filing, as `(filename, doc_type)` pairs.

    Returns `((), failures)` when the filing directory cannot be listed, so the
    caller records a coverage gap rather than assuming the filing had no
    exhibits. Those two things must never be confused.

    A readable directory with an unreadable submission header still yields
    exhibits, via the filename fallback - but the header failure is *also*
    returned, because the fallback cannot see an exhibit whose filename does not
    announce itself. An unenumerable 8-K is precisely the gap through which a
    metric could still be reported while looking absent, so it is recorded and
    left for the §6 test to weigh, not swallowed.
    """
    def gap(filename: str, url: str, stage: str, exc: BaseException) -> DocumentFailure:
        return DocumentFailure(
            cik=filing.cik,
            accession=filing.accession,
            form=filing.form,
            filing_date=filing.filing_date,
            filename=filename,
            url=url,
            stage=stage,
            error=f"{type(exc).__name__}: {exc}",
        )

    index_url = filing_index_json_url(filing.cik, filing.accession)
    try:
        entries = parse_directory(client.fetch_json(index_url))
    except Exception as exc:  # noqa: BLE001 - one bad index must not stop a run
        return (), (gap("index.json", index_url, "index", exc),)

    # Header types are authoritative; the filename pattern is the fallback.
    failures: list[DocumentFailure] = []
    header_url = submission_header_url(filing.cik, filing.accession)
    try:
        types = parse_submission_header(client.fetch_text(header_url))
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "Submission header unavailable for %s; falling back to filename "
            "matching, which can miss an oddly named exhibit: %s",
            filing.accession,
            exc,
        )
        failures.append(gap(header_url.rsplit("/", 1)[-1], header_url, "header", exc))
        types = {}

    found: list[tuple[str, str]] = []
    for filename, size in entries:
        if size == 0 or not is_readable_filename(filename):
            continue
        if filename.lower() == f"{filing.accession}.txt":
            continue  # the full submission duplicates every other document
        doc_type = types.get(filename.lower())
        if is_exhibit_99(filename, doc_type):
            found.append((filename, doc_type or "EX-99"))

    return tuple(found), tuple(failures)


# --- Document loading ------------------------------------------------------


def load_document(
    client: EdgarClient,
    filing: Filing,
    filename: str,
    doc_type: str,
    *,
    is_primary: bool,
) -> tuple[Document | None, DocumentFailure | None]:
    """Fetch and decode one document. Returns exactly one of (document, failure)."""
    url = document_url(filing.cik, filing.accession, filename)

    def fail(stage: str, error: str) -> tuple[None, DocumentFailure]:
        logger.warning(
            "Corpus gap: CIK %s %s %s %s (%s) - %s",
            filing.cik,
            filing.form,
            filing.accession,
            filename,
            stage,
            error,
        )
        return None, DocumentFailure(
            cik=filing.cik,
            accession=filing.accession,
            form=filing.form,
            filing_date=filing.filing_date,
            filename=filename,
            url=url,
            stage=stage,
            error=error,
        )

    try:
        record = client.fetch(url)
    except Exception as exc:  # noqa: BLE001
        return fail("fetch", f"{type(exc).__name__}: {exc}")

    if record.n_bytes > MAX_DOCUMENT_BYTES:
        return fail("size", f"{record.n_bytes} bytes exceeds {MAX_DOCUMENT_BYTES}")
    if record.n_bytes == 0:
        return fail("fetch", "empty document")

    text = _cached_text(record.sha256)
    if text is None:
        try:
            text = html_to_text(record.path.read_bytes())
        except Exception as exc:  # noqa: BLE001 - html_to_text should not raise
            return fail("parse", f"{type(exc).__name__}: {exc}")
        _store_text(record.sha256, text)

    if not text:
        return fail("parse", "document produced no text")

    return (
        Document(
            cik=filing.cik,
            accession=filing.accession,
            form=filing.form,
            filing_date=filing.filing_date,
            report_date=filing.report_date,
            filename=filename,
            doc_type=doc_type,
            is_primary=is_primary,
            url=url,
            sha256=record.sha256,
            n_bytes=record.n_bytes,
            text=text,
        ),
        None,
    )


def _filing_documents(
    client: EdgarClient,
    filing: Filing,
    *,
    include_exhibits: bool,
    exhibit_forms: Sequence[str],
) -> tuple[tuple[Document, ...], tuple[DocumentFailure, ...]]:
    documents: list[Document] = []
    failures: list[DocumentFailure] = []

    # Some older filings carry no primaryDocument. The complete submission text
    # file is a superset of the filing and is the right fallback: over-reading
    # risks nothing, under-reading manufactures a false absence.
    primary_name = filing.primary_document or f"{filing.accession}.txt"
    document, failure = load_document(
        client, filing, primary_name, filing.form, is_primary=True
    )
    if document is not None:
        documents.append(document)
    if failure is not None:
        failures.append(failure)

    wanted = {form.upper() for form in exhibit_forms}
    if include_exhibits and filing.form.upper() in wanted:
        exhibits, exhibit_failures = exhibit_files(client, filing)
        failures.extend(exhibit_failures)
        for filename, doc_type in exhibits:
            if filename == primary_name:
                continue
            document, failure = load_document(
                client, filing, filename, doc_type, is_primary=False
            )
            if document is not None:
                documents.append(document)
            if failure is not None:
                failures.append(failure)

    return tuple(documents), tuple(failures)


# --- Reporting periods -----------------------------------------------------


def _period_label(anchor: Filing) -> str:
    if anchor.report_date is not None:
        return f"{anchor.report_date.isoformat()}/{anchor.form.upper()}"
    return f"filed-{anchor.filing_date.isoformat()}/{anchor.form.upper()}"


def reporting_periods(filings: Sequence[Filing]) -> tuple[ReportingPeriod, ...]:
    """Group an issuer's filings into the periods its own reports define.

    Each periodic report anchors one period; the window runs from the day after
    the previous anchor was filed to the day this one was filed. Everything else
    the issuer filed - 8-K, 6-K, exhibits - falls into whichever window contains
    its filing date.

    Two boundary decisions, both taken in the direction that avoids inventing an
    empty period (an empty period reads as absence and would corrupt the §6
    test): filings before the first anchor join the first period, and filings
    after the last anchor extend the last period rather than opening a new one
    that no periodic report has closed yet.
    """
    ordered = sorted(filings, key=lambda f: (f.filing_date, f.accession))
    if not ordered:
        return ()

    anchor_forms = {form.upper() for form in PERIOD_ANCHOR_FORMS}
    anchors: list[Filing] = []
    seen_report_dates: set[date] = set()
    for filing in ordered:
        if filing.form.upper() not in anchor_forms:
            continue
        if filing.report_date is not None:
            if filing.report_date in seen_report_dates:
                continue  # a re-filed period is not a second period
            seen_report_dates.add(filing.report_date)
        anchors.append(filing)

    if not anchors:
        return ()

    first_date = ordered[0].filing_date
    last_date = ordered[-1].filing_date

    windows: list[tuple[Filing, date, date]] = []
    previous_end: date | None = None
    for anchor in anchors:
        start = previous_end if previous_end is not None else min(first_date, anchor.filing_date)
        end = anchor.filing_date
        windows.append((anchor, start, end))
        previous_end = end

    # Extend the final window so nothing filed after the last periodic report
    # falls outside every period.
    last_anchor, last_start, last_end = windows[-1]
    windows[-1] = (last_anchor, last_start, max(last_end, last_date))

    buckets: list[list[str]] = [[] for _ in windows]
    for filing in ordered:
        placed = False
        for i, (_, start, end) in enumerate(windows):
            if start <= filing.filing_date <= end:
                buckets[i].append(filing.accession)
                placed = True
                break
        if not placed:
            # Before the first window: join the first period.
            buckets[0].append(filing.accession)

    return tuple(
        ReportingPeriod(
            index=i,
            label=_period_label(anchor),
            anchor_accession=anchor.accession,
            anchor_form=anchor.form,
            anchor_filing_date=anchor.filing_date,
            period_end=anchor.report_date,
            start=start,
            end=end,
            accessions=tuple(dict.fromkeys(buckets[i])),
        )
        for i, (anchor, start, end) in enumerate(windows)
    )


# --- Building the corpus ---------------------------------------------------


def build(
    client: EdgarClient,
    cik: int | str,
    filings: Iterable[Filing] | None = None,
    *,
    forms: Sequence[str] = config.CORPUS_FORMS,
    include_exhibits: bool = True,
    exhibit_forms: Sequence[str] = EXHIBIT_FORMS,
) -> Corpus:
    """Fetch, decode, and organise an issuer's entire filed corpus.

    `filings` defaults to `filings.complete_index`, which follows the
    submissions overflow files. Passing a truncated index here would truncate
    the corpus, and a truncated corpus manufactures false DISCONTINUED verdicts
    - so the default is the complete one and callers must opt out deliberately.
    """
    cik_int = int(str(cik).lstrip("0") or "0")
    index = list(filings) if filings is not None else complete_index(client, cik_int)
    selected = of_forms(index, forms)

    documents: list[Document] = []
    failures: list[DocumentFailure] = []

    for filing in sorted(selected, key=lambda f: (f.filing_date, f.accession)):
        filing_docs, filing_failures = _filing_documents(
            client,
            filing,
            include_exhibits=include_exhibits,
            exhibit_forms=exhibit_forms,
        )
        documents.extend(filing_docs)
        failures.extend(filing_failures)

    return Corpus(
        cik=cik_int,
        documents=tuple(documents),
        failures=tuple(failures),
        periods=reporting_periods(selected),
    )
