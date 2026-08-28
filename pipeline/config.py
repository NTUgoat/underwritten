"""Configuration constants for the Underwritten pipeline.

Every value that governs the study is defined here rather than inline, so the
pre-registered parameters in METHOD.md have exactly one representation in code.
"""

from __future__ import annotations

import os
from pathlib import Path

# --- Paths -----------------------------------------------------------------

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
RAW = DATA / "raw"              # gitignored: reproducible from the manifest
COHORT = DATA / "cohort"        # committed: the frozen company list
ADJUDICATION = DATA / "adjudication"  # committed: the human ruling ledger
MANIFEST = DATA / "manifest"    # committed: SHA-256 of every document read
DERIVED = DATA / "derived"      # committed: computed tables and the spec log

for _d in (RAW, COHORT, ADJUDICATION, MANIFEST, DERIVED):
    _d.mkdir(parents=True, exist_ok=True)

# --- SEC access ------------------------------------------------------------
# The SEC fair-access policy requires a declared User-Agent carrying a real
# contact address, and caps automated access at 10 requests/second. Both are
# enforced in edgar.py rather than left to the caller's discretion.

SEC_CONTACT = os.environ.get("SEC_CONTACT", "Jex Lin jlin048@e.ntu.edu.sg")
USER_AGENT = SEC_CONTACT

SEC_WWW = "https://www.sec.gov"
SEC_DATA = "https://data.sec.gov"
SEC_FTS = "https://efts.sec.gov/LATEST/search-index"

MAX_REQUESTS_PER_SECOND = 8.0   # below the 10/s ceiling, deliberately
REQUEST_TIMEOUT_SECONDS = 30.0
MAX_RETRIES = 4
BACKOFF_BASE_SECONDS = 1.5

# --- Study parameters (see METHOD.md §3) -----------------------------------

LISTING_WINDOW_START = "2019-01-01"
LISTING_WINDOW_END = "2021-12-31"

COHORT_TARGET_N = 50
COHORT_FLOOR_N = 25

# As-at date for every terminal state in the study. Fixed, not "today", so the
# analysis is reproducible and does not drift between runs.
AS_AT_DATE = "2026-08-28"

# METHOD.md §6: absence across this many consecutive reporting periods, across
# the entire filed corpus, before a metric may be called DISCONTINUED.
DISCONTINUATION_PERIODS = 4

# --- Form types ------------------------------------------------------------

LISTING_FORMS = ("424B4", "424B3", "424B1", "S-1", "F-1", "S-4", "F-4")
ANNUAL_FORMS = ("10-K", "20-F", "40-F", "10-K/A", "20-F/A")
QUARTERLY_FORMS = ("10-Q", "10-Q/A")
CURRENT_FORMS = ("8-K", "6-K", "8-K/A", "6-K/A")

# The whole corpus that the §6 absence test must search. Narrowing this list is
# the single easiest way to produce a false DISCONTINUED, so it is defined once.
CORPUS_FORMS = ANNUAL_FORMS + QUARTERLY_FORMS + CURRENT_FORMS

# --- Outcome variables (METHOD.md §7.2) ------------------------------------
# Extracted from the `items` array of the EDGAR submissions archive. These are
# objective, dated, public, and not chosen by the author.

ADVERSE_8K_ITEMS = {
    "4.02": "Non-reliance on previously issued financial statements",
    "4.01": "Change in registrant's certifying accountant",
}
ADVERSE_FORMS = {
    "NT 10-K": "Late annual report",
    "NT 10-Q": "Late quarterly report",
    "25": "Delisting notification",
    "25-NSE": "Delisting notification (exchange-initiated)",
}

# --- Analysis --------------------------------------------------------------

BOOTSTRAP_RESAMPLES = 10_000
RANDOM_SEED = 20260828  # fixed so every published interval is reproducible
CONFIDENCE_LEVEL = 0.95
