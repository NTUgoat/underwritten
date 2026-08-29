"""Route handlers, plus the small Markdown renderer /method and /changelog use.

Every handler does the same three things and nothing else: load, shape, render.
No handler computes a statistic. Anything the reader will treat as a finding
comes out of ``data/derived/`` exactly as the pipeline wrote it, or it is not
shown at all.

The Markdown renderer here is deliberately small and local. METHOD.md and
CHANGELOG.md are the two documents it must handle, they are written by hand in
this repository, and adding a dependency to render two known files would put a
third-party parser between the reader and the pre-registration.
"""

from __future__ import annotations

import html
import re
from typing import Any

from fastapi import Request
from fastapi.responses import HTMLResponse, JSONResponse

from . import data

# --------------------------------------------------------------------------
# Markdown - only the constructs METHOD.md and CHANGELOG.md actually use
# --------------------------------------------------------------------------

_INLINE_CODE = re.compile(r"`([^`]+)`")
_BOLD = re.compile(r"\*\*(.+?)\*\*", re.DOTALL)
_EMPHASIS = re.compile(r"(?<!\*)\*(?!\s)(.+?)(?<!\s)\*(?!\*)", re.DOTALL)
_STRIKE = re.compile(r"~~(.+?)~~", re.DOTALL)
_LINK = re.compile(r"\[([^\]]+)\]\(([^)\s]+)\)")
_HEADING = re.compile(r"^(#{1,6})\s+(.*)$")
_UL_ITEM = re.compile(r"^[-*]\s+(.*)$")
_OL_ITEM = re.compile(r"^(\d+)[.)]\s+(.*)$")
_TABLE_RULE = re.compile(r"^\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?$")
_SLUG_STRIP = re.compile(r"[^a-z0-9]+")


def slugify(text: str) -> str:
    return _SLUG_STRIP.sub("-", text.strip().lower()).strip("-") or "section"


def _inline(text: str) -> str:
    """Escape first, then apply inline marks. Never trusts the source as HTML."""
    out = html.escape(text, quote=False)
    out = _INLINE_CODE.sub(lambda m: f"<code>{m.group(1)}</code>", out)
    out = _LINK.sub(
        lambda m: f'<a href="{html.escape(m.group(2), quote=True)}">{m.group(1)}</a>', out
    )
    out = _BOLD.sub(lambda m: f"<strong>{m.group(1)}</strong>", out)
    out = _STRIKE.sub(lambda m: f"<del>{m.group(1)}</del>", out)
    out = _EMPHASIS.sub(lambda m: f"<em>{m.group(1)}</em>", out)
    return out


def _split_row(line: str) -> list[str]:
    stripped = line.strip().removeprefix("|").removesuffix("|")
    return [cell.strip() for cell in stripped.split("|")]


def _is_table_row(line: str) -> bool:
    return line.strip().startswith("|") and line.strip().endswith("|")


def render_markdown(source: str) -> tuple[str, list[dict[str, Any]]]:
    """Return (html, table_of_contents).

    The table of contents lists level-2 headings, which is the granularity a
    reader of METHOD.md navigates by.
    """
    lines = source.replace("\r\n", "\n").split("\n")
    out: list[str] = []
    toc: list[dict[str, Any]] = []
    i = 0
    n = len(lines)

    while i < n:
        line = lines[i]
        stripped = line.strip()

        if not stripped:
            i += 1
            continue

        if stripped in {"---", "***", "___"}:
            out.append('<hr class="doc__rule">')
            i += 1
            continue

        heading = _HEADING.match(stripped)
        if heading:
            level = len(heading.group(1))
            text = heading.group(2).strip()
            anchor = slugify(text)
            if level == 2:
                toc.append({"id": anchor, "text": text})
            out.append(f'<h{level} id="{anchor}">{_inline(text)}</h{level}>')
            i += 1
            continue

        if _is_table_row(line) and i + 1 < n and _TABLE_RULE.match(lines[i + 1].strip()):
            header = _split_row(line)
            i += 2
            body: list[list[str]] = []
            while i < n and _is_table_row(lines[i]):
                body.append(_split_row(lines[i]))
                i += 1
            cells = "".join(f"<th>{_inline(c)}</th>" for c in header)
            rows = "".join(
                "<tr>" + "".join(f"<td>{_inline(c)}</td>" for c in r) + "</tr>"
                for r in body
            )
            out.append(
                '<div class="scroller"><table class="doc__table">'
                f"<thead><tr>{cells}</tr></thead><tbody>{rows}</tbody></table></div>"
            )
            continue

        if stripped.startswith(">"):
            quote: list[str] = []
            while i < n and lines[i].strip().startswith(">"):
                quote.append(lines[i].strip().lstrip(">").strip())
                i += 1
            out.append(f'<blockquote><p>{_inline(" ".join(quote).strip())}</p></blockquote>')
            continue

        ordered = _OL_ITEM.match(stripped)
        unordered = _UL_ITEM.match(stripped)
        if ordered or unordered:
            tag = "ol" if ordered else "ul"
            items: list[str] = []
            while i < n:
                current = lines[i]
                current_stripped = current.strip()
                match_o = _OL_ITEM.match(current_stripped)
                match_u = _UL_ITEM.match(current_stripped)
                if match_o and tag == "ol":
                    items.append(match_o.group(2))
                elif match_u and tag == "ul":
                    items.append(match_u.group(1))
                elif current_stripped and current.startswith((" ", "\t")) and items:
                    items[-1] = f"{items[-1]} {current_stripped}"
                else:
                    break
                i += 1
            body = "".join(f"<li>{_inline(item)}</li>" for item in items)
            out.append(f"<{tag}>{body}</{tag}>")
            continue

        paragraph: list[str] = []
        while i < n and lines[i].strip():
            candidate = lines[i].strip()
            if (
                _HEADING.match(candidate)
                or candidate in {"---", "***", "___"}
                or candidate.startswith(">")
                or _is_table_row(lines[i])
                or (_UL_ITEM.match(candidate) and paragraph)
                or (_OL_ITEM.match(candidate) and paragraph)
            ):
                break
            paragraph.append(candidate)
            i += 1
        if paragraph:
            out.append(f'<p>{_inline(" ".join(paragraph))}</p>')

    return "\n".join(out), toc


# --------------------------------------------------------------------------
# shared shaping helpers
# --------------------------------------------------------------------------


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _query(request: Request, key: str) -> str:
    return str(request.query_params.get(key, "") or "").strip()


def _context(request: Request, **extra: Any) -> dict[str, Any]:
    base: dict[str, Any] = {"request": request}
    base.update(extra)
    return base


def _sources(*datasets: data.Dataset) -> list[data.Source]:
    return data.sources_for(*datasets)


# --------------------------------------------------------------------------
# handlers
# --------------------------------------------------------------------------


def register(app, templates) -> None:
    """Attach every route. Kept in one place so the route table is readable."""

    def render(name: str, context: dict[str, Any]) -> HTMLResponse:
        # Current Starlette takes the Request first. Every context built by
        # _context() carries it, so the handlers stay free of the plumbing.
        return templates.TemplateResponse(context["request"], name, context)

    @app.get("/healthz")
    def healthz() -> JSONResponse:
        """Liveness probe. Touches no file and renders no template deliberately:
        the deployment must be promotable before any data has been written."""
        return JSONResponse({"status": "ok"})

    @app.get("/", response_class=HTMLResponse)
    def index(request: Request) -> HTMLResponse:
        finding = data.finding()
        board = data.scoreboard()
        cohort = data.cohort_frozen()
        funnel = data.cohort_funnel()
        # What has actually been done, in four stages. Published because the
        # front page otherwise reads as an empty result rather than a complete
        # instrument whose reading has not started.
        status = data.study_status()
        return render(
            "index.html",
            _context(
                request,
                page_id="finding",
                page_title="The finding",
                finding=finding.payload if isinstance(finding.payload, dict) else {},
                board=board.payload if isinstance(board.payload, dict) else {},
                cohort_rows=cohort.rows,
                # The realised n is a count of committed rows, not an estimate,
                # so it can be published before any analysis has run.
                frozen_n=len(cohort.rows) if cohort.available else None,
                funnel=funnel.payload if isinstance(funnel.payload, dict) else {},
                status=status,
                sources=_sources(finding, board, cohort, funnel, *status["sources"]),
            ),
        )

    @app.get("/note", response_class=HTMLResponse)
    def note(request: Request) -> HTMLResponse:
        note_dataset = data.note()
        payload = note_dataset.payload if isinstance(note_dataset.payload, dict) else {}
        return render(
            "note.html",
            _context(
                request,
                page_id="note",
                page_title="The note",
                note=payload,
                sections=payload.get("sections") or [],
                revisions=payload.get("revisions") or [],
                sources=_sources(note_dataset),
            ),
        )

    @app.get("/cohort", response_class=HTMLResponse)
    def cohort(request: Request) -> HTMLResponse:
        ledger = data.metrics()
        board = data.scoreboard()
        frozen = data.cohort_frozen()

        rows = data.metric_list(ledger)
        # "Available" on the Dataset means the file parsed, not that anyone has
        # ruled on anything: build_analysis writes metrics.json with an empty
        # list and available=false while the adjudication ledger is unwritten.
        # The page needs the second question, because an empty ledger and a
        # filter that excluded everything are different things to tell a reader.
        has_rulings = bool(rows)
        sector = _query(request, "sector")
        arm = _query(request, "arm")
        state = _query(request, "state")
        metric_id = _query(request, "metric")

        filtered = data.filter_metrics(rows, sector=sector, arm=arm, state=state)
        selected = data.find_metric(rows, metric_id)

        # The sector and arm facets come from the frozen cohort as well as the
        # ledger, so both filters work against the committed company list before
        # a single metric has been adjudicated.
        issuers = data.filter_issuers(frozen.rows, sector=sector, arm=arm)

        return render(
            "cohort.html",
            _context(
                request,
                page_id="cohort",
                page_title="The ledger",
                ledger_available=has_rulings,
                rows=filtered,
                total_rows=len(rows),
                facets=data.merge_facets(
                    data.facets(rows), data.cohort_facets(frozen.rows)
                ),
                tally=data.state_tally(filtered),
                selected=selected,
                filters={"sector": sector, "arm": arm, "state": state},
                board=board.payload if isinstance(board.payload, dict) else {},
                cohort_rows=issuers,
                total_cohort_rows=len(frozen.rows),
                cohort_available=frozen.available,
                sources=_sources(ledger, board, frozen),
            ),
        )

    @app.get("/positions", response_class=HTMLResponse)
    def positions(request: Request) -> HTMLResponse:
        dataset = data.positions()
        payload = dataset.payload if isinstance(dataset.payload, dict) else {}
        return render(
            "positions.html",
            _context(
                request,
                page_id="positions",
                page_title="Positions",
                positions=payload.get("positions") or [],
                as_at_value=payload.get("as_at"),
                sources=_sources(dataset),
            ),
        )

    @app.get("/resolved", response_class=HTMLResponse)
    def resolved(request: Request) -> HTMLResponse:
        dataset = data.resolved()
        payload = dataset.payload if isinstance(dataset.payload, dict) else {}
        return render(
            "resolved.html",
            _context(
                request,
                page_id="resolved",
                page_title="Resolved cases",
                cases=payload.get("cases") or [],
                sources=_sources(dataset),
            ),
        )

    @app.get("/method", response_class=HTMLResponse)
    def method(request: Request) -> HTMLResponse:
        document = data.method_document()
        body, toc = render_markdown(document.payload or "")
        specs = data.spec_log()
        exclusions = data.cohort_exclusions()
        funnel = data.cohort_funnel()
        finding = data.finding()
        applied = finding.get("exclusions_applied") if finding.available else None
        return render(
            "method.html",
            _context(
                request,
                page_id="method",
                page_title="Method",
                document_html=body,
                document_available=document.available,
                toc=toc,
                spec_rows=specs.rows,
                spec_available=specs.available,
                exclusion_rows=exclusions.rows,
                exclusions_available=exclusions.available,
                exclusions_applied=applied if isinstance(applied, dict) else {},
                funnel=funnel.payload if isinstance(funnel.payload, dict) else {},
                sources=_sources(document, specs, exclusions, funnel, finding),
            ),
        )

    def _coverage_context() -> dict[str, Any]:
        """Per-issuer corpus coverage, and every document that could not be read."""
        dataset = data.corpus_coverage()
        if not dataset.available or not isinstance(dataset.payload, dict):
            return {"available": False, "issuers": [], "gaps": [], "documents_read": 0}
        payload = dataset.payload
        per_issuer = payload.get("per_issuer") or []
        gaps = [
            {**gap, "issuer": entry.get("name", ""), "cik": entry.get("cik")}
            for entry in per_issuer
            for gap in (entry.get("gaps") or [])
        ]
        return {
            "available": True,
            "issuers": per_issuer,
            "gaps": gaps,
            "documents_read": payload.get("documents_read", 0),
            "documents_failed": payload.get("documents_failed", 0),
        }

    @app.get("/provenance", response_class=HTMLResponse)
    def provenance(request: Request) -> HTMLResponse:
        dataset = data.manifest()
        payload = dataset.payload if isinstance(dataset.payload, dict) else {}
        documents = payload.get("documents") or []
        return render(
            "provenance.html",
            _context(
                request,
                page_id="provenance",
                page_title="Provenance",
                manifest_available=dataset.available,
                as_at_value=payload.get("as_at"),
                n_documents=_int_or_none(payload.get("n_documents")),
                documents=documents if isinstance(documents, list) else [],
                # Each manifest declares which pipeline stage produced it, so
                # the page can name the stages present instead of publishing one
                # stage's count as though it covered everything read.
                stages=payload.get("stages") or [],
                coverage=_coverage_context(),
                manifest_errors=payload.get("errors") or [],
                sources=_sources(dataset),
            ),
        )

    @app.get("/changelog", response_class=HTMLResponse)
    def changelog(request: Request) -> HTMLResponse:
        document = data.changelog_document()
        body, toc = render_markdown(document.payload or "")
        return render(
            "changelog.html",
            _context(
                request,
                page_id="changelog",
                page_title="Changelog",
                document_html=body,
                document_available=document.available,
                toc=toc,
                sources=_sources(document),
            ),
        )


__all__ = ["register", "render_markdown", "slugify"]
