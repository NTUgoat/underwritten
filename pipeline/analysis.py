"""The pre-registered analysis: METHOD.md §7.1 to §7.6, and nothing beyond it.

This module computes the six specifications the pre-registration commits to and
refuses to compute anything else. It is pure: every function takes data and
returns new objects, nothing here touches the network, and nothing here writes a
ruling. The I/O shell is `build_analysis.py`.

**The integrity rule this module is built around.** A terminal state
(METHOD.md §5) comes from the human adjudication ledger and from nowhere else.
`metrics.py` produces `ABSENCE_TEST_MET` - the *mechanical necessary condition*
for `DISCONTINUED` - and that string is deliberately not one of the terminal
states here. No code path in this module maps one onto the other, reads
`metrics_candidates.csv`, or infers a state from a corpus. If the ledger is
absent, every §7 result is emitted `available: false` with a stated reason.
An unavailable result is a publishable result; a fabricated one is not.

**Forbidden words.** METHOD.md §7.2: the words *cause*, *predicts* and
*leads to* do not appear in the findings. `assert_no_forbidden_words` enforces
that at build time over the composed payloads, so the rule fails a run rather
than reaching a reader. Passages copied verbatim out of the human ledger
(rationales, quoted defining sentences, hand-written benign labels) are the
reviewer's record rather than this study's prose, and are walked separately -
see `VERBATIM_SUBTREES`.

--------------------------------------------------------------------------
THE ADJUDICATION LEDGER - data/adjudication/metrics.csv
--------------------------------------------------------------------------

Written by a human, one row per adjudicated metric. It does not exist yet.
Required columns::

    cik                        int, must be a frozen cohort member
    metric_name                the company's own name for it
    state                      one of METHOD.md §5, or NO_METRICS_DEFINED

Optional columns, each with a stated default::

    metric_id                  default: "<cik>-<slugified metric_name>"
    substantive                REDEFINED only. default TRUE (§5 defines
                               REDEFINED as substantive; a FALSE here records a
                               cosmetic change that must not make a Mover)
    direction_at_last_report   IMPROVING | DETERIORATING | UNDETERMINED.
                               default and fallback: UNDETERMINED, which §7.3
                               publishes as an explicit third category
    state_change_date          ISO date of the first discontinuation or
                               redefinition. §7.2 counts adverse events only
                               after this date
    last_appearance_date       ISO date. Used as the §7.2 threshold when
                               state_change_date is blank, because absence
                               begins after the last appearance
    first_appearance_date      ISO date, for the scoreboard's INTRODUCED cell
    benign                     TRUE/FALSE, the §7.4 hand label
    benign_label, benign_detail        free text, verbatim from the reviewer
    absence_periods                    int, the §6 count
    first_appearance_accession/_form/_quote   citation columns
    last_appearance_accession/_form/_quote    citation columns
    reviewer, review_date, rationale   METHOD.md §4 provenance

Anything unparseable is counted and reported, never dropped in silence.
"""

from __future__ import annotations

import csv
import logging
import math
import random
import re
import statistics
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from types import MappingProxyType
from typing import Any, Iterable, Mapping, Sequence

from scipy import stats as _scipy

from . import config
from .outcomes import EXCL_MECHANICAL_DELISTING, EXCL_WARRANT, AdverseEvent

logger = logging.getLogger(__name__)

LEDGER_PATH = config.ADJUDICATION / "metrics.csv"
PROJECTIONS_PATH = config.ADJUDICATION / "projections.csv"
SPEC_LOG_PATH = config.DERIVED / "spec_log.csv"


class AnalysisError(RuntimeError):
    """Raised when a published payload would break a pre-registered rule."""


# ===========================================================================
# Part 0 - vocabulary
# ===========================================================================

#: METHOD.md §5, in publication order. `ABSENCE_TEST_MET` is deliberately NOT
#: here: it is mechanical evidence, not a terminal state.
TERMINAL_STATES: tuple[str, ...] = (
    "ALIVE",
    "REDEFINED",
    "RENAMED",
    "ABSORBED",
    "DISCONTINUED",
    "NOT_DETERMINABLE",
)

#: METHOD.md §3 C4: an issuer with no company-defined metric. Recorded on an
#: issuer-level ledger row; such an issuer has no metric behaviour and so
#: appears in neither §7.2 arm.
NO_METRICS_DEFINED = "NO_METRICS_DEFINED"

LEDGER_STATES: tuple[str, ...] = TERMINAL_STATES + (NO_METRICS_DEFINED,)

IMPROVING = "IMPROVING"
DETERIORATING = "DETERIORATING"
UNDETERMINED = "UNDETERMINED"
DIRECTIONS: tuple[str, ...] = (IMPROVING, DETERIORATING, UNDETERMINED)

KEEPER = "KEEPER"
MOVER = "MOVER"

TRUE_TOKENS = frozenset({"true", "t", "yes", "y", "1"})
FALSE_TOKENS = frozenset({"false", "f", "no", "n", "0"})

#: METHOD.md §7.2. Enforced on the composed payloads before they are written.
FORBIDDEN_PATTERN = re.compile(
    r"\b(?:caus\w*|predict\w*|lead(?:s|ing)?\s+to)\b", re.IGNORECASE
)

#: Regions of the payload copied verbatim from the human ledger. These carry the
#: reviewer's own words, which METHOD.md §4 requires be published unedited; they
#: are not this study's findings prose and are excluded from the §7.2 word rule.
VERBATIM_SUBTREES = frozenset(
    {
        "adjudication",
        "cause_of_death",
        "definition_changes",
        "first_appearance",
        "last_appearance",
    }
)
VERBATIM_KEYS = frozenset({"benign_labels", "benign_detail", "quote", "rationale"})

#: METHOD.md §7.2 and §12 both require the power statement to be published
#: beside the result rather than left to the reader. The generic form is used
#: where the arms do not exist yet; `_power_note` carries the realised sizes.
POWER_NOTE = (
    "At the size METHOD.md §12 states this study is underpowered for anything "
    "but a large effect. The reported quantity is an association with an "
    "interval, and the interval is wide. No claim of direction between metric "
    "behaviour and later filing events is made or implied."
)


def _power_note(n_keepers: int, n_movers: int) -> str:
    """The power statement, carrying the realised size of both arms."""
    return (
        f"Underpowered for anything but a large effect: {n_keepers} Keeper(s) "
        f"against {n_movers} Mover(s). The reported quantity is an association "
        "with an interval, and the interval is wide. No claim of direction "
        "between metric behaviour and later filing events is made or implied."
    )

LEDGER_ABSENT_REASON = (
    "The adjudication ledger data/adjudication/metrics.csv has not been written "
    "yet. METHOD.md §4 requires every candidate to be ruled on by hand and §5 "
    "makes the terminal state a human ruling, so no state is inferred here and "
    "no figure is published in its place."
)

OUTCOME_NOT_RUN_REASON = (
    "The adverse-event outcome variable has not been extracted from the EDGAR "
    "submissions archive for this run, so no issuer can be scored on it."
)

OUTCOME_SKIPPED_REASON = (
    "The adverse-event extraction was not run because the adjudication ledger is "
    "absent: with no Keeper/Mover split there is nothing to score events against."
)


# ===========================================================================
# Part 1 - statistics. Each one carries its own n; a share without a
# denominator is not publishable (METHOD.md §7.2).
# ===========================================================================


@dataclass(frozen=True)
class Proportion:
    """k of n, with an exact binomial (Clopper-Pearson) interval."""

    k: int
    n: int
    ci_low: float | None
    ci_high: float | None
    confidence: float

    @property
    def share(self) -> float | None:
        return (self.k / self.n) if self.n else None

    def as_dict(self, *, count_key: str = "k") -> dict[str, Any]:
        return {
            count_key: self.k,
            "n": self.n,
            "share": self.share,
            "ci_low": self.ci_low,
            "ci_high": self.ci_high,
            "confidence": self.confidence,
            "interval_method": "Clopper-Pearson exact binomial",
        }


def clopper_pearson(
    k: int, n: int, *, confidence: float = config.CONFIDENCE_LEVEL
) -> Proportion:
    """Exact binomial interval. Not the normal approximation.

    METHOD.md §7.1 asks for an exact interval, and at these counts the normal
    approximation is wrong in the direction that flatters the study: it is too
    narrow, and it produces bounds outside [0, 1] at the extremes where most of
    these cells sit.
    """
    if n < 0 or k < 0:
        raise ValueError(f"clopper_pearson needs non-negative counts, got k={k}, n={n}")
    if k > n:
        raise ValueError(f"clopper_pearson needs k <= n, got k={k}, n={n}")
    if not 0.0 < confidence < 1.0:
        raise ValueError(f"confidence must be in (0, 1), got {confidence}")
    if n == 0:
        return Proportion(k=0, n=0, ci_low=None, ci_high=None, confidence=confidence)

    alpha = 1.0 - confidence
    low = 0.0 if k == 0 else float(_scipy.beta.ppf(alpha / 2.0, k, n - k + 1))
    high = 1.0 if k == n else float(_scipy.beta.ppf(1.0 - alpha / 2.0, k + 1, n - k))
    return Proportion(k=k, n=n, ci_low=low, ci_high=high, confidence=confidence)


def fisher_exact_2x2(a: int, b: int, c: int, d: int) -> float:
    """Two-sided Fisher exact p for [[a, b], [c, d]]. Pure Python, exact.

    Conditioning on both margins, the count in the top-left cell is
    hypergeometric. The two-sided p is the total probability of every table
    with the same margins that is no more probable than the observed one - the
    convention used by R's `fisher.test` and by scipy. Written out here rather
    than delegated so the confirmatory p-value in this study has no third-party
    dependency and can be recomputed by hand from `math.comb`.
    """
    for name, value in (("a", a), ("b", b), ("c", c), ("d", d)):
        if value < 0:
            raise ValueError(f"fisher_exact_2x2 needs non-negative cells, {name}={value}")
    total = a + b + c + d
    if total == 0:
        raise ValueError("fisher_exact_2x2 needs a non-empty table")

    row1 = a + b
    col1 = a + c
    denominator = math.comb(total, col1)

    def probability(k: int) -> float:
        return math.comb(row1, k) * math.comb(total - row1, col1 - k) / denominator

    lo = max(0, row1 + col1 - total)
    hi = min(row1, col1)
    observed = probability(a)
    # The tolerance absorbs float error on tables that are equally probable by
    # symmetry; without it a mirror-image table is dropped and p comes out low.
    tolerance = observed * (1.0 + 1e-7)
    return min(1.0, sum(p for k in range(lo, hi + 1) if (p := probability(k)) <= tolerance))


def _percentile(sorted_values: Sequence[float], q: float) -> float:
    """Linear-interpolated percentile of an already-sorted sequence."""
    if not sorted_values:
        raise ValueError("_percentile needs at least one value")
    if len(sorted_values) == 1:
        return float(sorted_values[0])
    position = q * (len(sorted_values) - 1)
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return float(sorted_values[int(position)])
    weight = position - lower
    return float(sorted_values[lower] * (1 - weight) + sorted_values[upper] * weight)


@dataclass(frozen=True)
class Bootstrap:
    """A resampled difference in proportions, in percentage points."""

    difference_pp: float | None
    ci_low_pp: float | None
    ci_high_pp: float | None
    resamples: int
    seed: int
    n_baseline: int
    n_comparison: int
    reason: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "difference_pp": self.difference_pp,
            "ci_pp": (
                None
                if self.ci_low_pp is None or self.ci_high_pp is None
                else [self.ci_low_pp, self.ci_high_pp]
            ),
            "resamples": self.resamples,
            "seed": self.seed,
            "n_baseline": self.n_baseline,
            "n_comparison": self.n_comparison,
            "method": "percentile bootstrap, resampled within arm with replacement",
            "reason": self.reason,
        }


def bootstrap_difference(
    baseline: Sequence[int],
    comparison: Sequence[int],
    *,
    resamples: int = config.BOOTSTRAP_RESAMPLES,
    seed: int = config.RANDOM_SEED,
    confidence: float = config.CONFIDENCE_LEVEL,
) -> Bootstrap:
    """`comparison` rate minus `baseline` rate, with a percentile CI.

    Both arms are resampled with replacement at their own realised size, which
    is what makes the interval reflect how few issuers each arm actually holds.
    Seeded from `config.RANDOM_SEED` so a published interval is reproducible.
    """
    if resamples < 1:
        raise ValueError(f"resamples must be at least 1, got {resamples}")
    n_baseline, n_comparison = len(baseline), len(comparison)
    if n_baseline == 0 or n_comparison == 0:
        return Bootstrap(
            difference_pp=None,
            ci_low_pp=None,
            ci_high_pp=None,
            resamples=resamples,
            seed=seed,
            n_baseline=n_baseline,
            n_comparison=n_comparison,
            reason="One arm is empty, so a difference in proportions is undefined.",
        )

    observed = (sum(comparison) / n_comparison) - (sum(baseline) / n_baseline)
    rng = random.Random(seed)
    base = list(baseline)
    comp = list(comparison)
    differences = [
        (sum(rng.choices(comp, k=n_comparison)) / n_comparison)
        - (sum(rng.choices(base, k=n_baseline)) / n_baseline)
        for _ in range(resamples)
    ]
    differences.sort()
    alpha = 1.0 - confidence
    return Bootstrap(
        difference_pp=observed * 100.0,
        ci_low_pp=_percentile(differences, alpha / 2.0) * 100.0,
        ci_high_pp=_percentile(differences, 1.0 - alpha / 2.0) * 100.0,
        resamples=resamples,
        seed=seed,
        n_baseline=n_baseline,
        n_comparison=n_comparison,
    )


def mann_whitney(
    baseline: Sequence[float], comparison: Sequence[float]
) -> dict[str, Any]:
    """Two-sided Mann-Whitney U on per-issuer event counts. §7.2, secondary."""
    result: dict[str, Any] = {
        "u": None,
        "p": None,
        "n_baseline": len(baseline),
        "n_comparison": len(comparison),
        "alternative": "two-sided",
        "reason": "",
    }
    if not baseline or not comparison:
        result["reason"] = "One arm is empty, so the rank test cannot be run."
        return result
    try:
        outcome = _scipy.mannwhitneyu(
            list(comparison), list(baseline), alternative="two-sided"
        )
    except ValueError as exc:  # identical constant arms, etc.
        result["reason"] = f"Rank test not defined for these samples: {exc}"
        return result
    result["u"] = float(outcome.statistic)
    result["p"] = float(outcome.pvalue)
    return result


# ===========================================================================
# Part 2 - the adjudication ledger
# ===========================================================================


def _flag(value: str, *, default: bool) -> bool:
    token = (value or "").strip().lower()
    if token in TRUE_TOKENS:
        return True
    if token in FALSE_TOKENS:
        return False
    return default


def _iso_date(value: str) -> date | None:
    token = (value or "").strip()
    if not token:
        return None
    try:
        return date.fromisoformat(token[:10])
    except ValueError:
        return None


def slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", (value or "").strip().lower()).strip("-")
    return slug or "unnamed"


@dataclass(frozen=True)
class LedgerRow:
    """One human ruling. Immutable, and it carries its own provenance."""

    row_number: int
    metric_id: str
    cik: int
    metric_name: str
    state: str
    substantive: bool
    direction: str
    state_change_date: date | None
    last_appearance_date: date | None
    first_appearance_date: date | None
    benign: bool
    benign_label: str
    benign_detail: str
    raw: Mapping[str, str]

    @property
    def is_issuer_row(self) -> bool:
        return self.state == NO_METRICS_DEFINED

    @property
    def is_discontinued(self) -> bool:
        return self.state == "DISCONTINUED"

    @property
    def is_substantive_redefinition(self) -> bool:
        return self.state == "REDEFINED" and self.substantive

    @property
    def moved(self) -> bool:
        """The §7.2 Mover trigger for a single metric."""
        return self.is_discontinued or self.is_substantive_redefinition

    @property
    def move_date(self) -> date | None:
        """When the move can first be placed in time, or None."""
        return self.state_change_date or self.last_appearance_date


@dataclass(frozen=True)
class Ledger:
    """The ledger as read, including the reasons it may be unusable."""

    path: str
    available: bool
    reason: str
    rows: tuple[LedgerRow, ...] = ()
    invalid: tuple[dict[str, str], ...] = ()
    off_cohort: tuple[dict[str, str], ...] = ()

    @property
    def metric_rows(self) -> tuple[LedgerRow, ...]:
        return tuple(r for r in self.rows if not r.is_issuer_row)

    @property
    def no_metrics_ciks(self) -> tuple[int, ...]:
        return tuple(sorted({r.cik for r in self.rows if r.is_issuer_row}))

    def as_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "available": self.available,
            "reason": self.reason,
            "n_rows": len(self.rows),
            "n_metric_rows": len(self.metric_rows),
            "n_issuers_no_metrics_defined": len(self.no_metrics_ciks),
            "n_invalid_rows": len(self.invalid),
            "n_rows_outside_cohort": len(self.off_cohort),
            "invalid_rows": list(self.invalid),
            "rows_outside_cohort": list(self.off_cohort),
        }


def read_ledger(
    path: Path = LEDGER_PATH, *, cohort_ciks: Iterable[int] | None = None
) -> Ledger:
    """Read the human ledger. A missing one is an absence, never a zero."""
    relative = _relative(path)
    if not path.is_file():
        return Ledger(path=relative, available=False, reason=LEDGER_ABSENT_REASON)

    try:
        with path.open("r", newline="", encoding="utf-8") as handle:
            raw_rows = list(csv.DictReader(handle))
    except (OSError, UnicodeDecodeError, csv.Error) as exc:
        return Ledger(
            path=relative,
            available=False,
            reason=(
                f"The adjudication ledger at {relative} could not be read "
                f"({type(exc).__name__}: {exc}). No figure is published in its place."
            ),
        )

    known = {int(c) for c in cohort_ciks} if cohort_ciks is not None else None
    rows: list[LedgerRow] = []
    invalid: list[dict[str, str]] = []
    off_cohort: list[dict[str, str]] = []

    for offset, raw in enumerate(raw_rows, start=2):  # row 1 is the header
        clean = {k: (v or "").strip() for k, v in raw.items() if k}
        cik_token = clean.get("cik", "")
        state = clean.get("state", "").strip().upper()
        name = clean.get("metric_name", "")

        if not cik_token.isdigit():
            invalid.append({"row": str(offset), "problem": "cik is missing or not a number"})
            continue
        if state not in LEDGER_STATES:
            invalid.append(
                {
                    "row": str(offset),
                    "problem": (
                        f"state {state!r} is not one of METHOD.md §5 "
                        f"({', '.join(LEDGER_STATES)})"
                    ),
                }
            )
            continue
        if state != NO_METRICS_DEFINED and not name:
            invalid.append({"row": str(offset), "problem": "metric_name is empty"})
            continue

        cik = int(cik_token)
        if known is not None and cik not in known:
            off_cohort.append({"row": str(offset), "cik": cik_token, "metric_name": name})
            continue

        direction = clean.get("direction_at_last_report", "").strip().upper()
        rows.append(
            LedgerRow(
                row_number=offset,
                metric_id=clean.get("metric_id") or f"{cik}-{slugify(name)}",
                cik=cik,
                metric_name=name,
                state=state,
                substantive=_flag(clean.get("substantive", ""), default=True),
                direction=direction if direction in DIRECTIONS else UNDETERMINED,
                state_change_date=_iso_date(clean.get("state_change_date", "")),
                last_appearance_date=_iso_date(clean.get("last_appearance_date", "")),
                first_appearance_date=_iso_date(clean.get("first_appearance_date", "")),
                benign=_flag(clean.get("benign", ""), default=False),
                benign_label=clean.get("benign_label", ""),
                benign_detail=clean.get("benign_detail", ""),
                raw=MappingProxyType(dict(clean)),
            )
        )

    if not rows:
        detail = (
            f" {len(invalid)} row(s) could not be parsed and {len(off_cohort)} row(s) "
            f"name a CIK outside the frozen cohort."
            if (invalid or off_cohort)
            else ""
        )
        return Ledger(
            path=relative,
            available=False,
            reason=(
                f"The adjudication ledger at {relative} holds no usable ruling.{detail} "
                "No terminal state is inferred and no figure is published."
            ),
            invalid=tuple(invalid),
            off_cohort=tuple(off_cohort),
        )

    return Ledger(
        path=relative,
        available=True,
        reason="",
        rows=tuple(rows),
        invalid=tuple(invalid),
        off_cohort=tuple(off_cohort),
    )


def _relative(path: Path) -> str:
    try:
        return str(path.relative_to(config.ROOT)).replace("\\", "/")
    except ValueError:  # pragma: no cover - path outside the repo
        return str(path)


# ===========================================================================
# Part 3 - the cohort, as the analysis needs it
# ===========================================================================


@dataclass(frozen=True)
class CohortRow:
    cik: int
    name: str
    ticker: str
    arm: str
    sic: str
    sic_description: str
    listing_date: date | None


def read_cohort(path: Path | None = None) -> tuple[CohortRow, ...]:
    """The frozen cohort. Raises rather than proceeding on a missing freeze."""
    target = path or (config.COHORT / "cohort_frozen.csv")
    if not target.is_file():
        raise AnalysisError(
            f"No frozen cohort at {_relative(target)}. METHOD.md §3 freezes the "
            "company list before anything is measured; run "
            "`python -m pipeline.build_cohort` first."
        )
    with target.open("r", newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    out: list[CohortRow] = []
    for raw in rows:
        token = (raw.get("cik") or "").strip()
        if not token.isdigit():
            continue
        out.append(
            CohortRow(
                cik=int(token),
                name=(raw.get("name") or "").strip(),
                ticker=(raw.get("ticker") or "").strip(),
                arm=(raw.get("arm") or "").strip(),
                sic=(raw.get("sic") or "").strip(),
                sic_description=(raw.get("sic_description") or "").strip(),
                listing_date=_iso_date(raw.get("listing_date") or ""),
            )
        )
    return tuple(out)


# ===========================================================================
# Part 4 - §7.2 issuer split. KEEPERS and MOVERS.
# ===========================================================================


@dataclass(frozen=True)
class IssuerBehaviour:
    cik: int
    group: str
    n_metrics: int
    n_discontinued: int
    n_redefined_substantive: int
    move_date: date | None

    @property
    def datable(self) -> bool:
        return self.group == KEEPER or self.move_date is not None


def classify_issuers(
    rows: Sequence[LedgerRow], *, drop_benign: bool = False
) -> tuple[IssuerBehaviour, ...]:
    """Split issuers by their own metric behaviour. METHOD.md §7.2.

    `drop_benign` is the §7.4 sensitivity: a metric whose move carries a
    hand-written benign label stops counting as a move, so an issuer whose only
    move was benign becomes a Keeper.
    """
    by_cik: dict[int, list[LedgerRow]] = {}
    for row in rows:
        if row.is_issuer_row:
            continue
        by_cik.setdefault(row.cik, []).append(row)

    behaviours: list[IssuerBehaviour] = []
    for cik in sorted(by_cik):
        metrics = by_cik[cik]
        moves = [r for r in metrics if r.moved and not (drop_benign and r.benign)]
        dates = [r.move_date for r in moves if r.move_date is not None]
        behaviours.append(
            IssuerBehaviour(
                cik=cik,
                group=MOVER if moves else KEEPER,
                n_metrics=len(metrics),
                n_discontinued=sum(
                    1 for r in moves if r.is_discontinued
                ),
                n_redefined_substantive=sum(
                    1 for r in moves if r.is_substantive_redefinition
                ),
                move_date=min(dates) if dates else None,
            )
        )
    return tuple(behaviours)


# ===========================================================================
# Part 5 - §7.1 the base rate
# ===========================================================================


def base_rate(
    rows: Sequence[LedgerRow], cohort: Sequence[CohortRow]
) -> dict[str, Any]:
    """Share of adjudicated metrics in each terminal state, pooled and per issuer."""
    metrics = [r for r in rows if not r.is_issuer_row]
    total = len(metrics)
    counts = {state: 0 for state in TERMINAL_STATES}
    for row in metrics:
        counts[row.state] += 1

    states = []
    for state in TERMINAL_STATES:
        proportion = clopper_pearson(counts[state], total)
        states.append(
            {
                "state": state,
                "n": counts[state],
                "denominator": total,
                "share": proportion.share,
                "ci_low": proportion.ci_low,
                "ci_high": proportion.ci_high,
            }
        )

    names = {row.cik: row.name for row in cohort}
    by_issuer = []
    for cik in sorted({r.cik for r in metrics}):
        own = [r for r in metrics if r.cik == cik]
        issuer_counts = {state: sum(1 for r in own if r.state == state) for state in TERMINAL_STATES}
        by_issuer.append(
            {
                "cik": cik,
                "issuer": names.get(cik, ""),
                "denominator": len(own),
                "states": [
                    {
                        "state": state,
                        "n": issuer_counts[state],
                        "denominator": len(own),
                        "share": (issuer_counts[state] / len(own)) if own else None,
                        "ci_low": clopper_pearson(issuer_counts[state], len(own)).ci_low,
                        "ci_high": clopper_pearson(issuer_counts[state], len(own)).ci_high,
                    }
                    for state in TERMINAL_STATES
                ],
            }
        )

    return {
        "available": total > 0,
        "reason": "" if total > 0 else LEDGER_ABSENT_REASON,
        "denominator": total,
        "confidence": config.CONFIDENCE_LEVEL,
        "interval_method": "Clopper-Pearson exact binomial",
        "states": states,
        "by_issuer": by_issuer,
    }


# ===========================================================================
# Part 6 - §7.2 the primary hypothesis. The only confirmatory test.
# ===========================================================================


def counting_events(
    events: Sequence[AdverseEvent], *, restore_warrants: bool = False
) -> list[AdverseEvent]:
    """Events that count, honouring Amendment 1.

    `restore_warrants` is the §7.4 sensitivity that puts E1 back. E2, the
    mechanical de-SPAC shell delisting, is never restored: it is dated days
    before the completion 8-K and is not evidence about the issuer at all.
    """
    return [
        event
        for event in events
        if event.counts or (restore_warrants and event.excluded_as == EXCL_WARRANT)
    ]


@dataclass(frozen=True)
class PrimaryTest:
    available: bool
    reason: str
    payload: Mapping[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return dict(self.payload)


def _unavailable_primary(reason: str, **extra: Any) -> PrimaryTest:
    return PrimaryTest(
        available=False,
        reason=reason,
        payload=MappingProxyType(
            {"available": False, "reason": reason, "power_note": POWER_NOTE, **extra}
        ),
    )


def primary_test(
    behaviours: Sequence[IssuerBehaviour],
    *,
    listing_dates: Mapping[int, date],
    events: Mapping[int, Sequence[AdverseEvent]],
    outcome_available: bool,
    outcome_reason: str = "",
    restore_warrants: bool = False,
    resamples: int = config.BOOTSTRAP_RESAMPLES,
    seed: int = config.RANDOM_SEED,
) -> PrimaryTest:
    """H1, with its timing rule, its 2x2, its bootstrap and its rank test.

    Timing, straight from METHOD.md §7.2: an event counts only if it was filed
    after the issuer's first discontinuation or redefinition; for Keepers, after
    the equivalent median offset from listing. A Mover whose move cannot be
    placed in time is excluded and the count is published rather than being
    quietly given the Keeper threshold.
    """
    if not behaviours:
        return _unavailable_primary(LEDGER_ABSENT_REASON)
    if not outcome_available:
        return _unavailable_primary(
            outcome_reason
            or (
                "The adverse-event outcome has not been extracted from the EDGAR "
                "submissions archive, so no arm can be scored."
            )
        )

    movers = [b for b in behaviours if b.group == MOVER]
    keepers = [b for b in behaviours if b.group == KEEPER]

    offsets = [
        (b.move_date - listing_dates[b.cik]).days
        for b in movers
        if b.move_date is not None and b.cik in listing_dates
    ]
    if not offsets:
        return _unavailable_primary(
            "No Mover carries a dated first discontinuation or redefinition, so "
            "the median offset from listing that §7.2 gives Keepers cannot be "
            "computed and neither arm can be placed in time.",
            n_movers=len(movers),
            n_keepers=len(keepers),
        )
    median_offset = round(statistics.median(offsets))

    thresholds: dict[int, date] = {}
    skipped: list[dict[str, Any]] = []
    for behaviour in behaviours:
        listing = listing_dates.get(behaviour.cik)
        if listing is None:
            skipped.append({"cik": behaviour.cik, "reason": "no listing date in the frozen cohort"})
            continue
        if behaviour.group == MOVER:
            if behaviour.move_date is None:
                skipped.append(
                    {
                        "cik": behaviour.cik,
                        "reason": (
                            "Mover with no dated first discontinuation or "
                            "redefinition; it cannot be placed in time"
                        ),
                    }
                )
                continue
            thresholds[behaviour.cik] = behaviour.move_date
        else:
            thresholds[behaviour.cik] = listing + timedelta(days=median_offset)

    arms: dict[str, dict[str, list[int]]] = {
        KEEPER: {"indicators": [], "counts": []},
        MOVER: {"indicators": [], "counts": []},
    }
    per_issuer: list[dict[str, Any]] = []
    for behaviour in behaviours:
        threshold = thresholds.get(behaviour.cik)
        if threshold is None:
            continue
        counted = [
            event
            for event in counting_events(
                list(events.get(behaviour.cik, ())), restore_warrants=restore_warrants
            )
            if event.filing.filing_date > threshold
        ]
        arms[behaviour.group]["counts"].append(len(counted))
        arms[behaviour.group]["indicators"].append(1 if counted else 0)
        per_issuer.append(
            {
                "cik": behaviour.cik,
                "group": behaviour.group,
                "threshold": threshold.isoformat(),
                "n_adverse_events": len(counted),
                "has_adverse_event": bool(counted),
                "reasons": sorted({event.reason for event in counted}),
            }
        )

    keeper_indicators = arms[KEEPER]["indicators"]
    mover_indicators = arms[MOVER]["indicators"]
    if not keeper_indicators or not mover_indicators:
        return _unavailable_primary(
            "One arm holds no issuer that can be placed in time, so a difference "
            "in proportions is undefined. Both arm sizes are published.",
            n_keepers=len(keeper_indicators),
            n_movers=len(mover_indicators),
            n_excluded_untimed=len(skipped),
            excluded_untimed=skipped,
        )

    keeper_proportion = clopper_pearson(sum(keeper_indicators), len(keeper_indicators))
    mover_proportion = clopper_pearson(sum(mover_indicators), len(mover_indicators))
    boot = bootstrap_difference(
        keeper_indicators, mover_indicators, resamples=resamples, seed=seed
    )
    fisher_p = fisher_exact_2x2(
        sum(keeper_indicators),
        len(keeper_indicators) - sum(keeper_indicators),
        sum(mover_indicators),
        len(mover_indicators) - sum(mover_indicators),
    )

    payload = {
        "available": True,
        "reason": "",
        "keepers": {
            "n": len(keeper_indicators),
            "adverse": sum(keeper_indicators),
            "share": keeper_proportion.share,
            "ci_low": keeper_proportion.ci_low,
            "ci_high": keeper_proportion.ci_high,
        },
        "movers": {
            "n": len(mover_indicators),
            "adverse": sum(mover_indicators),
            "share": mover_proportion.share,
            "ci_low": mover_proportion.ci_low,
            "ci_high": mover_proportion.ci_high,
        },
        "difference_pp": boot.difference_pp,
        "bootstrap_ci_pp": (
            None
            if boot.ci_low_pp is None
            else [boot.ci_low_pp, boot.ci_high_pp]
        ),
        "bootstrap": boot.as_dict(),
        "fisher_p": fisher_p,
        "fisher_table": {
            "keepers_adverse": sum(keeper_indicators),
            "keepers_not_adverse": len(keeper_indicators) - sum(keeper_indicators),
            "movers_adverse": sum(mover_indicators),
            "movers_not_adverse": len(mover_indicators) - sum(mover_indicators),
            "alternative": "two-sided",
        },
        "mann_whitney": mann_whitney(arms[KEEPER]["counts"], arms[MOVER]["counts"]),
        "timing": {
            "rule": (
                "An event counts only if it was filed after the issuer's first "
                "discontinuation or redefinition. Keepers use the median offset "
                "from listing observed across Movers."
            ),
            "median_offset_days": median_offset,
            "n_movers_with_a_dated_move": len(offsets),
        },
        "warrant_restatements_restored": restore_warrants,
        "n_excluded_untimed": len(skipped),
        "excluded_untimed": skipped,
        "per_issuer": per_issuer,
        "power_note": _power_note(len(keeper_indicators), len(mover_indicators)),
    }
    return PrimaryTest(available=True, reason="", payload=MappingProxyType(payload))


# ===========================================================================
# Part 7 - §7.3 the counter-hypothesis. Published either way.
# ===========================================================================

COUNTER_VERDICT_RULE = (
    "The two exact binomial intervals are compared. Where they overlap the "
    "discontinuation rates are not separated by the filed record and the "
    "headline is WEAKENED, which is the counter-hypothesis. Where they are "
    "disjoint and the deteriorating rate is the higher one the headline "
    "SURVIVED. METHOD.md §7.3 pre-registers the comparison but no threshold, "
    "so this reading rule is stated here rather than left implicit; it is "
    "descriptive and is not a second confirmatory test."
)


def counter_test(rows: Sequence[LedgerRow]) -> dict[str, Any]:
    """Discontinuation rate by the metric's own last-reported direction.

    Direction that the filed record does not settle is `UNDETERMINED` - an
    explicit, published third category. It is never defaulted into one of the
    other two and never dropped, because doing either would let the counter-test
    be quietly won on a convenient subset.
    """
    metrics = [r for r in rows if not r.is_issuer_row]
    if not metrics:
        return {"available": False, "reason": LEDGER_ABSENT_REASON, "verdict": "NOT_DETERMINABLE"}

    buckets: dict[str, dict[str, Any]] = {}
    for direction in DIRECTIONS:
        own = [r for r in metrics if r.direction == direction]
        discontinued = sum(1 for r in own if r.is_discontinued)
        proportion = clopper_pearson(discontinued, len(own))
        buckets[direction] = {
            "n": len(own),
            "discontinued": discontinued,
            "share": proportion.share,
            "ci_low": proportion.ci_low,
            "ci_high": proportion.ci_high,
        }

    improving = buckets[IMPROVING]
    deteriorating = buckets[DETERIORATING]
    undetermined = buckets[UNDETERMINED]

    if not improving["n"] or not deteriorating["n"]:
        verdict = "NOT_DETERMINABLE"
        difference_pp = None
    else:
        difference_pp = (deteriorating["share"] - improving["share"]) * 100.0
        disjoint = (
            deteriorating["ci_low"] > improving["ci_high"]
            or improving["ci_low"] > deteriorating["ci_high"]
        )
        verdict = "SURVIVED" if (disjoint and difference_pp > 0) else "WEAKENED"

    return {
        "available": True,
        "reason": "",
        "verdict": verdict,
        "verdict_rule": COUNTER_VERDICT_RULE,
        "denominator": len(metrics),
        "improving": improving,
        "deteriorating": deteriorating,
        "undetermined": undetermined,
        "difference_pp": difference_pp,
        "note": (
            f"{undetermined['n']} of {len(metrics)} adjudicated metrics have a "
            "last-reported direction the filed record does not settle. They are "
            "published as a third category rather than assigned to either side."
        ),
    }


# ===========================================================================
# Part 8 - §7.4 the pre-registered sensitivities
# ===========================================================================


def _discontinuation_share(rows: Sequence[LedgerRow], *, drop_benign: bool) -> Proportion:
    metrics = [
        r
        for r in rows
        if not r.is_issuer_row and not (drop_benign and r.benign and r.moved)
    ]
    return clopper_pearson(sum(1 for r in metrics if r.is_discontinued), len(metrics))


def sensitivity(
    rows: Sequence[LedgerRow],
    *,
    listing_dates: Mapping[int, date],
    events: Mapping[int, Sequence[AdverseEvent]],
    outcome_available: bool,
    outcome_reason: str = "",
    resamples: int = config.BOOTSTRAP_RESAMPLES,
    seed: int = config.RANDOM_SEED,
) -> dict[str, Any]:
    """Both §7.4 re-runs, with both deltas.

    1. Hand-labelled benign moves removed. A metric the reviewer labelled benign
       leaves the analysis entirely, so it counts neither in the numerator nor in
       the denominator, and an issuer whose only move was benign becomes a Keeper.
    2. Amendment 1's warrant restatements restored to the outcome variable, so a
       reader can see exactly what that exclusion cost.
    """
    metrics = [r for r in rows if not r.is_issuer_row]
    if not metrics:
        return {"available": False, "reason": LEDGER_ABSENT_REASON}

    as_registered = _discontinuation_share(rows, drop_benign=False)
    benign_removed = _discontinuation_share(rows, drop_benign=True)
    removed = [r for r in metrics if r.benign and r.moved]

    runs = {
        "as_preregistered": primary_test(
            classify_issuers(rows),
            listing_dates=listing_dates,
            events=events,
            outcome_available=outcome_available,
            outcome_reason=outcome_reason,
            resamples=resamples,
            seed=seed,
        ),
        "benign_removed": primary_test(
            classify_issuers(rows, drop_benign=True),
            listing_dates=listing_dates,
            events=events,
            outcome_available=outcome_available,
            outcome_reason=outcome_reason,
            resamples=resamples,
            seed=seed,
        ),
        "warrant_restatements_restored": primary_test(
            classify_issuers(rows),
            listing_dates=listing_dates,
            events=events,
            outcome_available=outcome_available,
            outcome_reason=outcome_reason,
            restore_warrants=True,
            resamples=resamples,
            seed=seed,
        ),
    }

    def difference(key: str) -> float | None:
        value = runs[key].payload.get("difference_pp")
        return value if isinstance(value, (int, float)) else None

    baseline = difference("as_preregistered")
    deltas = {
        key: (
            None
            if baseline is None or difference(key) is None
            else difference(key) - baseline
        )
        for key in ("benign_removed", "warrant_restatements_restored")
    }

    share_delta_pp = (
        None
        if as_registered.share is None or benign_removed.share is None
        else (benign_removed.share - as_registered.share) * 100.0
    )

    return {
        "available": True,
        "reason": "",
        "primary": {
            "label": "As pre-registered",
            "value": as_registered.share,
            "n": as_registered.n,
            "discontinued": as_registered.k,
            "ci_low": as_registered.ci_low,
            "ci_high": as_registered.ci_high,
        },
        "benign_removed": {
            "label": "Benign labels removed",
            "value": benign_removed.share,
            "n": benign_removed.n,
            "discontinued": benign_removed.k,
            "ci_low": benign_removed.ci_low,
            "ci_high": benign_removed.ci_high,
        },
        "delta_pp": share_delta_pp,
        "n_benign_removed": len(removed),
        "benign_labels": sorted({r.benign_label for r in removed if r.benign_label}),
        "runs": {key: run.as_dict() for key, run in runs.items()},
        "deltas_pp": deltas,
        "operationalisation": (
            "A metric carrying a hand-written benign label leaves both the "
            "numerator and the denominator. Amendment 1's E2 mechanical de-SPAC "
            "shell delisting is never restored: it is dated days before the "
            "completion 8-K and is not evidence about the issuer."
        ),
    }


# ===========================================================================
# Part 9 - §7.5 the de-SPAC projection arm
# ===========================================================================

#: In preference order. The first tag that yields annual figures is used.
REVENUE_TAGS: tuple[str, ...] = (
    "RevenueFromContractWithCustomerExcludingAssessedTax",
    "RevenueFromContractWithCustomerIncludingAssessedTax",
    "Revenues",
    "SalesRevenueNet",
)

ANNUAL_PERIOD_MIN_DAYS = 350
ANNUAL_PERIOD_MAX_DAYS = 380

PROJECTIONS_ABSENT_REASON = (
    "No hand-transcribed projections at data/adjudication/projections.csv. "
    "METHOD.md §7.5 transcribes the S-4/F-4 prospective financial information by "
    "hand rather than parsing it, so coverage is 0 until that work is done."
)


@dataclass(frozen=True)
class Projection:
    cik: int
    fiscal_year: str
    projected_revenue: float
    accession: str
    page: str
    caption: str


def read_projections(path: Path = PROJECTIONS_PATH) -> tuple[Projection, ...]:
    """Hand-transcribed projections, or an empty tuple. Never parsed from a filing.

    A row this cannot read is skipped and logged at WARNING with its row number,
    and it lowers the published coverage rate rather than vanishing: §7.5
    publishes coverage precisely so an incomplete transcription is visible.
    """
    if not path.is_file():
        return ()
    try:
        with path.open("r", newline="", encoding="utf-8") as handle:
            raw_rows = list(csv.DictReader(handle))
    except (OSError, UnicodeDecodeError, csv.Error) as exc:
        raise AnalysisError(
            f"The projection ledger at {_relative(path)} could not be read: "
            f"{type(exc).__name__}: {exc}"
        ) from exc

    out: list[Projection] = []
    for offset, raw in enumerate(raw_rows, start=2):
        clean = {k: (v or "").strip() for k, v in raw.items() if k}
        cik = clean.get("cik", "")
        value = clean.get("projected_revenue", "").replace(",", "")
        year = clean.get("fiscal_year", "")
        if not cik.isdigit() or not year:
            logger.warning(
                "%s row %d: cik or fiscal_year is missing, so the row is skipped "
                "and the §7.5 coverage rate falls accordingly",
                _relative(path),
                offset,
            )
            continue
        try:
            projected = float(value)
        except ValueError:
            logger.warning(
                "%s row %d: projected_revenue %r is not a number, so the row is "
                "skipped and the §7.5 coverage rate falls accordingly",
                _relative(path),
                offset,
                value,
            )
            continue
        out.append(
            Projection(
                cik=int(cik),
                fiscal_year=year if year.upper().startswith("FY") else f"FY{year}",
                projected_revenue=projected,
                accession=clean.get("accession", ""),
                page=clean.get("page", ""),
                caption=clean.get("caption", ""),
            )
        )
    return tuple(out)


def realised_annual_revenue(companyfacts: Mapping[str, Any]) -> dict[str, float]:
    """Annual realised revenue by fiscal-year label, from XBRL companyfacts.

    Pure: it is handed an already-fetched payload. Only full-year durations are
    accepted, so a quarter never stands in for a year.
    """
    facts = (companyfacts or {}).get("facts", {}).get("us-gaap", {})
    for tag in REVENUE_TAGS:
        entries = ((facts.get(tag) or {}).get("units") or {}).get("USD") or []
        by_year: dict[str, float] = {}
        for entry in entries:
            start = _iso_date(str(entry.get("start", "")))
            end = _iso_date(str(entry.get("end", "")))
            value = entry.get("val")
            form = str(entry.get("form", "")).upper()
            if start is None or end is None or value is None:
                continue
            if not form.startswith(("10-K", "20-F", "40-F")):
                continue
            span = (end - start).days
            if not ANNUAL_PERIOD_MIN_DAYS <= span <= ANNUAL_PERIOD_MAX_DAYS:
                continue
            try:
                by_year[f"FY{end.year}"] = float(value)
            except (TypeError, ValueError):
                continue
        if by_year:
            return by_year
    return {}


def projection_arm(
    projections: Sequence[Projection],
    realised: Mapping[int, Mapping[str, float]],
    cohort: Sequence[CohortRow],
) -> dict[str, Any]:
    """Realisation ratio against realised revenue, with the coverage rate. §7.5."""
    despac = [row for row in cohort if row.arm.upper() == "DESPAC"]
    n_arm = len(despac)
    covered = sorted({p.cik for p in projections if any(r.cik == p.cik for r in despac)})
    coverage = {
        "n_despac_issuers": n_arm,
        "n_with_transcribed_projections": len(covered),
        "rate": (len(covered) / n_arm) if n_arm else None,
    }

    if not projections:
        return {
            "available": False,
            "reason": PROJECTIONS_ABSENT_REASON,
            "coverage": coverage,
            "realisation": {"n_pairs": 0, "median_ratio": None, "mean_ratio": None},
            "per_projection": [],
        }

    names = {row.cik: row.name for row in cohort}
    pairs: list[dict[str, Any]] = []
    for projection in projections:
        actual = (realised.get(projection.cik) or {}).get(projection.fiscal_year)
        ratio = (
            None
            if actual is None or projection.projected_revenue == 0
            else actual / projection.projected_revenue
        )
        pairs.append(
            {
                "cik": projection.cik,
                "issuer": names.get(projection.cik, ""),
                "fiscal_year": projection.fiscal_year,
                "projected_revenue": projection.projected_revenue,
                "realised_revenue": actual,
                "realisation_ratio": ratio,
                "accession": projection.accession,
                "page": projection.page,
                "caption": projection.caption,
            }
        )

    ratios = [p["realisation_ratio"] for p in pairs if p["realisation_ratio"] is not None]
    return {
        "available": bool(ratios),
        "reason": (
            ""
            if ratios
            else (
                "Projections are transcribed but no realised annual revenue could be "
                "matched to them in XBRL companyfacts, so no ratio is published."
            )
        ),
        "coverage": coverage,
        "realisation": {
            "n_pairs": len(ratios),
            "n_projections": len(pairs),
            "median_ratio": statistics.median(ratios) if ratios else None,
            "mean_ratio": statistics.fmean(ratios) if ratios else None,
        },
        "per_projection": pairs,
    }


# ===========================================================================
# Part 10 - §7.6 the specification counter
# ===========================================================================

SPEC_LOG_FIELDS: tuple[str, ...] = (
    "n",
    "timestamp",
    "specification",
    "preregistered",
    "result",
    "notes",
)


@dataclass(frozen=True)
class SpecRun:
    specification: str
    preregistered: str
    result: str
    notes: str = ""


def append_spec_runs(
    runs: Sequence[SpecRun], *, timestamp: str, path: Path = SPEC_LOG_PATH
) -> int:
    """Append every specification run to the log. Append-only. METHOD.md §7.6.

    Returns the running total, which is the N in "Specifications run: N."
    Nothing is ever rewritten: a specification that produced nothing is exactly
    what this counter exists to keep visible.
    """
    existing = 0
    if path.is_file():
        try:
            with path.open("r", newline="", encoding="utf-8") as handle:
                existing = sum(1 for _ in csv.DictReader(handle))
        except (OSError, UnicodeDecodeError, csv.Error) as exc:
            raise AnalysisError(
                f"The specification log at {_relative(path)} could not be read "
                f"({type(exc).__name__}: {exc}). It is append-only and is never "
                "silently replaced."
            ) from exc

    path.parent.mkdir(parents=True, exist_ok=True)
    write_header = not path.is_file() or existing == 0
    with path.open("a", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(SPEC_LOG_FIELDS))
        if write_header and path.stat().st_size == 0:
            writer.writeheader()
        for offset, run in enumerate(runs, start=1):
            writer.writerow(
                {
                    "n": existing + offset,
                    "timestamp": timestamp,
                    "specification": run.specification,
                    "preregistered": run.preregistered,
                    "result": run.result,
                    "notes": run.notes,
                }
            )
    return existing + len(runs)


def spec_line(total: int) -> str:
    return f"Specifications run: {total}. Pre-registered: #1."


# ===========================================================================
# Part 11 - the forbidden-word gate (METHOD.md §7.2)
# ===========================================================================


def walk_strings(
    payload: Any, *, skip_verbatim: bool, path: str = "$"
) -> list[tuple[str, str]]:
    """Every string in a payload, as `(json path, string)`, keys included.

    With `skip_verbatim`, a key named in `VERBATIM_SUBTREES` or `VERBATIM_KEYS`
    is skipped along with everything under it. The key itself is skipped and not
    only its values, because `cause_of_death` - the schema `app/data.py`
    documents and `app/templates/cohort.html` reads - is one of them.
    """
    found: list[tuple[str, str]] = []
    if isinstance(payload, str):
        found.append((path, payload))
    elif isinstance(payload, Mapping):
        for key, value in payload.items():
            key_text = str(key)
            child = f"{path}.{key_text}"
            if skip_verbatim and (key_text in VERBATIM_SUBTREES or key_text in VERBATIM_KEYS):
                continue
            found.append((f"{child}<key>", key_text))
            found.extend(walk_strings(value, skip_verbatim=skip_verbatim, path=child))
    elif isinstance(payload, (list, tuple)):
        for index, value in enumerate(payload):
            found.extend(
                walk_strings(value, skip_verbatim=skip_verbatim, path=f"{path}[{index}]")
            )
    return found


def forbidden_word_findings(payload: Any, *, skip_verbatim: bool = True) -> list[str]:
    """Paths whose string contains *cause*, *predicts* or *leads to*."""
    return [
        f"{path}: {text[:120]!r}"
        for path, text in walk_strings(payload, skip_verbatim=skip_verbatim)
        if FORBIDDEN_PATTERN.search(text)
    ]


def assert_no_forbidden_words(payload: Any, label: str, *, skip_verbatim: bool = True) -> None:
    """Fail the run rather than publish a word METHOD.md §7.2 rules out."""
    offences = forbidden_word_findings(payload, skip_verbatim=skip_verbatim)
    if offences:
        raise AnalysisError(
            f"{label} contains language METHOD.md §7.2 rules out of the findings "
            f"({len(offences)} occurrence(s)): " + "; ".join(offences[:8])
        )


# ===========================================================================
# Part 12 - assembling the published payloads
# ===========================================================================


@dataclass(frozen=True)
class AnalysisInputs:
    """Everything the analysis needs, already fetched. Nothing here is I/O."""

    as_at: str
    generated: str
    cohort: tuple[CohortRow, ...]
    ledger: Ledger
    events: Mapping[int, tuple[AdverseEvent, ...]]
    outcome_available: bool
    outcome_reason: str
    outcome_errors: tuple[dict[str, str], ...] = ()
    projections: tuple[Projection, ...] = ()
    realised_revenue: Mapping[int, Mapping[str, float]] = MappingProxyType({})
    resamples: int = config.BOOTSTRAP_RESAMPLES
    seed: int = config.RANDOM_SEED


@dataclass(frozen=True)
class Analysis:
    """The three published files, plus what to append to the specification log."""

    finding: Mapping[str, Any]
    scoreboard: Mapping[str, Any]
    metrics: Mapping[str, Any]
    spec_runs: tuple[SpecRun, ...]


def _exclusions_applied(
    events: Mapping[int, Sequence[AdverseEvent]], *, outcome_available: bool
) -> dict[str, int]:
    """Amendment 1's exclusions, counted rather than dropped.

    An empty dictionary when the outcome has not been extracted: publishing a
    zero would read as "none were found", which is a different claim from
    "the extraction has not run".
    """
    if not outcome_available:
        return {}
    tally = {EXCL_WARRANT: 0, EXCL_MECHANICAL_DELISTING: 0}
    for issuer_events in events.values():
        for event in issuer_events:
            if event.excluded_as:
                tally[event.excluded_as] = tally.get(event.excluded_as, 0) + 1
    return tally


def _headline_segments(
    rows: Sequence[LedgerRow], behaviours: Sequence[IssuerBehaviour], as_at: str
) -> list[dict[str, str]]:
    """The front-page sentence. Counts of this study's own committed ledger.

    These are not figures out of a filing, so they carry no accession: they are
    counts of `data/adjudication/metrics.csv`, which is committed and browsable
    row by row. The `basis` field beside them names that file.
    """
    metrics = [r for r in rows if not r.is_issuer_row]
    moved = sum(1 for r in metrics if r.moved)
    return [
        {"text": "Of the "},
        {"term": f"{len(metrics):,}"},
        {"text": " operating metrics that "},
        {"term": f"{len(behaviours):,}"},
        {
            "text": (
                " US issuers defined for themselves in their "
                f"{config.LISTING_WINDOW_START[:4]}-{config.LISTING_WINDOW_END[:4]} "
                "listing documents, "
            )
        },
        {"term": f"{moved:,}"},
        {"text": f" had been discontinued or substantively redefined by {as_at}."},
    ]


def _fiscal_years(cohort: Sequence[CohortRow], as_at: str) -> list[str]:
    listed = [row.listing_date.year for row in cohort if row.listing_date]
    if not listed:
        return []
    end = int(as_at[:4])
    return [f"FY{year}" for year in range(min(listed), end + 1)]


def _issuer_cells(
    rows: Sequence[LedgerRow], listing: date | None, fiscal_years: Sequence[str]
) -> list[dict[str, Any]]:
    """One cell per fiscal year for one issuer, derived from the ledger's dates.

    Nothing is inferred about a state here: every cell reads a state the human
    already recorded, and a year the ledger cannot speak to is NOT_DETERMINABLE
    rather than a guess.
    """
    cells: list[dict[str, Any]] = []
    for label in fiscal_years:
        year = int(label[2:])
        if listing is not None and year < listing.year:
            cells.append({"fy": label, "state": "NONE", "n": 0, "note": "not yet listed"})
            continue
        dated = [r for r in rows if r.first_appearance_date]
        introduced = [r for r in dated if r.first_appearance_date.year == year]
        if not dated and listing is not None and year == listing.year:
            introduced = list(rows)
        moved_here = [
            r for r in rows if r.moved and r.move_date and r.move_date.year == year
        ]
        if moved_here:
            state = (
                "DISCONTINUED"
                if any(r.is_discontinued for r in moved_here)
                else "REDEFINED"
            )
            cells.append(
                {
                    "fy": label,
                    "state": state,
                    "n": len(moved_here),
                    "note": "recorded in the adjudication ledger",
                }
            )
            continue
        if introduced:
            cells.append(
                {
                    "fy": label,
                    "state": "INTRODUCED",
                    "n": len(introduced),
                    "note": "first defined in this year",
                }
            )
            continue
        alive = [r for r in rows if r.state in ("ALIVE", "RENAMED", "ABSORBED")]
        if alive:
            cells.append({"fy": label, "state": "ALIVE", "n": len(alive), "note": ""})
            continue
        gone = [r for r in rows if r.moved and r.move_date and r.move_date.year < year]
        if gone:
            cells.append(
                {
                    "fy": label,
                    "state": (
                        "DISCONTINUED"
                        if any(r.is_discontinued for r in gone)
                        else "REDEFINED"
                    ),
                    "n": len(gone),
                    "note": "state carried forward from the year it was recorded",
                }
            )
            continue
        cells.append(
            {
                "fy": label,
                "state": "NOT_DETERMINABLE",
                "n": 0,
                "note": "the ledger does not place a state in this year",
            }
        )
    return cells


def _citation(
    row: LedgerRow, prefix: str, value: str, cik: int
) -> dict[str, Any] | None:
    accession = row.raw.get(f"{prefix}_accession", "")
    quote = row.raw.get(f"{prefix}_quote", "") or row.raw.get("defining_sentence", "")
    if not accession or not quote:
        return None
    return {
        "value": value,
        "quote": quote,
        "form": row.raw.get(f"{prefix}_form", ""),
        "filed": row.raw.get(f"{prefix}_date", ""),
        "accession": accession,
        "cik": cik,
    }


def build_metrics_payload(
    ledger: Ledger, cohort: Sequence[CohortRow]
) -> dict[str, Any]:
    """`metrics.json` - the ledger behind /cohort, one entry per adjudicated metric."""
    if not ledger.available:
        return {"available": False, "reason": ledger.reason, "metrics": []}

    by_cik = {row.cik: row for row in cohort}
    entries: list[dict[str, Any]] = []
    for row in ledger.metric_rows:
        issuer = by_cik.get(row.cik)
        entry: dict[str, Any] = {
            "id": row.metric_id,
            "cik": row.cik,
            "issuer": issuer.name if issuer else "",
            "ticker": issuer.ticker if issuer else "",
            "arm": issuer.arm if issuer else "",
            "sector": issuer.sic_description if issuer else "",
            "sic": issuer.sic if issuer else "",
            "name": row.metric_name,
            "state": row.state,
            "direction_at_last_report": row.direction,
            "first_appearance": _citation(row, "first_appearance", row.metric_name, row.cik),
            "last_appearance": _citation(row, "last_appearance", row.metric_name, row.cik),
            "definition_changes": [],
            "absence_periods": _int_or_none(row.raw.get("absence_periods", "")),
            "adjudication": {
                "initials": row.raw.get("reviewer", ""),
                "date": row.raw.get("review_date", ""),
                "rationale": row.raw.get("rationale", ""),
                "row": f"{ledger.path}:{row.row_number}",
            },
        }
        if row.benign_label or row.benign:
            # Key name fixed by app/data.py's documented schema and read by
            # app/templates/cohort.html; the label and detail are the reviewer's
            # own words, carried through unedited.
            entry["cause_of_death"] = {
                "label": row.benign_label,
                "benign": row.benign,
                "detail": row.benign_detail,
            }
        entries.append(entry)

    return {"available": True, "reason": "", "metrics": entries}


def _int_or_none(value: str) -> int | None:
    token = (value or "").strip()
    return int(token) if token.isdigit() else None


def build_scoreboard_payload(
    ledger: Ledger,
    cohort: Sequence[CohortRow],
    behaviours: Sequence[IssuerBehaviour],
    as_at: str,
) -> dict[str, Any]:
    """`scoreboard.json` - the one dense graphic. Empty until the ledger exists."""
    if not ledger.available:
        return {
            "as_at": as_at,
            "available": False,
            "reason": ledger.reason,
            "fiscal_years": [],
            "issuers": [],
        }

    groups = {b.cik: b.group for b in behaviours}
    fiscal_years = _fiscal_years(cohort, as_at)
    rows_by_cik: dict[int, list[LedgerRow]] = {}
    for row in ledger.metric_rows:
        rows_by_cik.setdefault(row.cik, []).append(row)

    issuers = [
        {
            "cik": member.cik,
            "name": member.name,
            "ticker": member.ticker,
            "arm": member.arm,
            "sector": member.sic_description,
            "group": groups.get(member.cik, ""),
            "cells": _issuer_cells(
                rows_by_cik.get(member.cik, []), member.listing_date, fiscal_years
            ),
        }
        for member in cohort
        if member.cik in rows_by_cik
    ]
    issuers.sort(key=lambda row: (row["group"] != KEEPER, row["name"]))

    return {
        "as_at": as_at,
        "available": bool(issuers),
        "reason": "" if issuers else ledger.reason,
        "fiscal_years": fiscal_years,
        "issuers": issuers,
    }


def _publishable(block: Mapping[str, Any], fallback_reason: str) -> dict[str, Any]:
    """A specification block as it is published. Returns a new dictionary.

    A payload the template would render as a table of zeros is worse than an
    absent one, so an unavailable specification is reduced to its availability
    and its reason and carries no figure keys at all.
    """
    if block.get("available"):
        return dict(block)
    reduced: dict[str, Any] = {
        "available": False,
        "reason": block.get("reason") or fallback_reason or LEDGER_ABSENT_REASON,
    }
    if "power_note" in block:  # the §7.2 arm sizes and the power statement stay
        reduced["power_note"] = POWER_NOTE
        reduced["n_keepers"] = block.get("n_keepers")
        reduced["n_movers"] = block.get("n_movers")
    return reduced


def run(inputs: AnalysisInputs) -> Analysis:
    """Compute every pre-registered specification and assemble what is published."""
    ledger = inputs.ledger
    cohort = inputs.cohort
    rows = ledger.rows if ledger.available else ()
    behaviours = classify_issuers(rows) if ledger.available else ()
    listing_dates = {
        row.cik: row.listing_date for row in cohort if row.listing_date is not None
    }

    rates = base_rate(rows, cohort)
    counter = counter_test(rows)
    primary = primary_test(
        behaviours,
        listing_dates=listing_dates,
        events=inputs.events,
        outcome_available=inputs.outcome_available,
        outcome_reason=inputs.outcome_reason,
        resamples=inputs.resamples,
        seed=inputs.seed,
    )
    sensitivities = (
        sensitivity(
            rows,
            listing_dates=listing_dates,
            events=inputs.events,
            outcome_available=inputs.outcome_available,
            outcome_reason=inputs.outcome_reason,
            resamples=inputs.resamples,
            seed=inputs.seed,
        )
        if ledger.available
        else {"available": False, "reason": ledger.reason}
    )
    arm = projection_arm(inputs.projections, inputs.realised_revenue, cohort)

    n_keepers = sum(1 for b in behaviours if b.group == KEEPER)
    n_movers = sum(1 for b in behaviours if b.group == MOVER)

    finding: dict[str, Any] = {
        "as_at": inputs.as_at,
        "generated": inputs.generated,
        "available": ledger.available,
        "reason": "" if ledger.available else ledger.reason,
        "cohort": {
            "n_issuers": len(cohort),
            "target_n": config.COHORT_TARGET_N,
            "floor_n": config.COHORT_FLOOR_N,
            "floor_met": len(cohort) >= config.COHORT_FLOOR_N,
            "n_metrics": len(ledger.metric_rows) if ledger.available else None,
            "n_keepers": n_keepers if ledger.available else None,
            "n_movers": n_movers if ledger.available else None,
            "n_no_metrics_defined": (
                len(ledger.no_metrics_ciks) if ledger.available else None
            ),
            "n_issuers_adjudicated": len(behaviours) if ledger.available else None,
        },
        "base_rate": _publishable(rates, ledger.reason),
        "counter_test": _publishable(counter, ledger.reason),
        "sensitivity": _publishable(sensitivities, ledger.reason),
        "primary_test": _publishable(primary.as_dict(), ledger.reason),
        "projection_arm": arm,
        "exclusions_applied": _exclusions_applied(
            inputs.events, outcome_available=inputs.outcome_available
        ),
        "outcome_variable": {
            "available": inputs.outcome_available,
            "reason": inputs.outcome_reason,
            "n_issuers_with_a_filing_index": len(inputs.events),
            "errors": list(inputs.outcome_errors),
            "errors_note": (
                "Every retrieval failure is recorded with the stage it belongs "
                "to. An issuer whose filing index could not be read is not "
                "scored as having no adverse event; it is absent from both arms."
            ),
        },
        "ledger": ledger.as_dict(),
        "limitations": [
            (
                "n is small. At n = 50 the study is underpowered for anything but "
                "a large effect, and no directional claim is made."
            ),
            (
                "The corpus is the SEC-filed record only. Investor decks and "
                "transcripts are outside it."
            ),
            (
                "Companies that delisted early file less and are structurally "
                "under-represented in later periods."
            ),
            (
                "Adjudication is one human's judgment, recorded row by row so it "
                "can be disagreed with."
            ),
            (
                "A discontinuation is not proof of intent, which is why §7.4 is "
                "pre-registered rather than optional."
            ),
            "2019-2021 is one cohort in one market and nothing here generalises.",
        ],
    }
    # The headline is a sentence about adjudicated metrics. With none in hand
    # it is omitted, so the front page shows its pending marker rather than a
    # grammatical sentence built out of zeros.
    if ledger.available and ledger.metric_rows:
        finding["finding"] = {
            "segments": _headline_segments(rows, behaviours, inputs.as_at),
            "basis": ledger.path,
        }

    spec_runs = _spec_runs(finding)
    return Analysis(
        finding=MappingProxyType(finding),
        scoreboard=MappingProxyType(
            build_scoreboard_payload(ledger, cohort, behaviours, inputs.as_at)
        ),
        metrics=MappingProxyType(build_metrics_payload(ledger, cohort)),
        spec_runs=spec_runs,
    )


def _result_of(block: Any, description: str) -> tuple[str, str]:
    if not isinstance(block, dict) or not block.get("available"):
        reason = (block or {}).get("reason", "") if isinstance(block, dict) else ""
        return "NOT RUN - inputs unavailable", reason
    return description, ""


def _spec_runs(finding: Mapping[str, Any]) -> tuple[SpecRun, ...]:
    """One log line per specification, run or not. METHOD.md §7.6."""
    rates = finding.get("base_rate", {})
    counter = finding.get("counter_test", {})
    primary = finding.get("primary_test", {})
    sens = finding.get("sensitivity", {})
    arm = finding.get("projection_arm", {})

    rate_result, rate_note = _result_of(
        rates,
        f"Terminal state of {rates.get('denominator', 0)} adjudicated metrics, "
        "exact binomial 95% intervals",
    )
    counter_result, counter_note = _result_of(
        counter, f"Verdict {counter.get('verdict', 'NOT_DETERMINABLE')}"
    )
    primary_result, primary_note = _result_of(
        primary,
        (
            f"Keepers {primary.get('keepers', {}).get('n')} / Movers "
            f"{primary.get('movers', {}).get('n')}; difference "
            f"{primary.get('difference_pp')} pp; Fisher p {primary.get('fisher_p')}"
        ),
    )
    benign_result, benign_note = _result_of(
        sens, f"Discontinuation share delta {sens.get('delta_pp')} pp"
    )
    warrant_result, warrant_note = _result_of(
        sens,
        (
            "Primary difference delta "
            f"{(sens.get('deltas_pp') or {}).get('warrant_restatements_restored')} pp "
            "with Amendment 1 E1 restored"
        ),
    )
    arm_result, arm_note = _result_of(
        arm,
        (
            f"{(arm.get('realisation') or {}).get('n_pairs')} matched pairs, median "
            f"realisation ratio {(arm.get('realisation') or {}).get('median_ratio')}"
        ),
    )

    return (
        SpecRun("§7.1 Terminal-state base rate, pooled and per issuer", "yes (§7.1)", rate_result, rate_note),
        SpecRun("§7.2 Keepers vs Movers, adverse filing events", "yes (#1, the only confirmatory test)", primary_result, primary_note),
        SpecRun("§7.3 Discontinuation by last-reported direction", "yes (§7.3, counter-hypothesis)", counter_result, counter_note),
        SpecRun("§7.4 Primary re-run, hand-labelled benign moves removed", "yes (§7.4)", benign_result, benign_note),
        SpecRun("§7.4 Primary re-run, Amendment 1 warrant restatements restored", "yes (§7.4)", warrant_result, warrant_note),
        SpecRun("§7.5 De-SPAC projection realisation ratio", "yes (§7.5)", arm_result, arm_note),
    )
