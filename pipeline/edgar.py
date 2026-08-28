"""Rate-limited, caching, hash-recording EDGAR client.

Three responsibilities, deliberately kept in one place:

1. Respect the SEC fair-access policy. A declared User-Agent and a hard rate
   limit, enforced here so no caller can bypass them.
2. Cache every retrieved document on disk, so the corpus is pulled once and the
   analysis can be re-run offline without touching sec.gov again.
3. Record a SHA-256 for every document at the moment it is read. The provenance
   claim in METHOD.md §11 is only as good as this manifest, so hashing is not
   optional and not deferred - it happens on the same code path as the fetch.

Nothing here interprets a filing. Parsing lives in corpus.py and metrics.py.
"""

from __future__ import annotations

import hashlib
import itertools
import json
import os
import threading
import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import requests

from . import config


_TMP_COUNTER = itertools.count()

# Retries for os.replace on Windows, where a destination held open by another
# reader raises PermissionError transiently.
_REPLACE_ATTEMPTS = 5


class EdgarError(RuntimeError):
    """Raised when EDGAR cannot satisfy a request after retries."""


class OfflineCacheMiss(EdgarError):
    """An offline client was asked for a document that is not cached."""


@dataclass(frozen=True)
class Fetched:
    """An immutable record of one retrieved document."""

    url: str
    path: Path
    sha256: str
    n_bytes: int
    from_cache: bool

    def as_manifest_row(self) -> dict[str, Any]:
        return {
            "url": self.url,
            "local_path": str(self.path.relative_to(config.ROOT)).replace("\\", "/"),
            "sha256": self.sha256,
            "bytes": self.n_bytes,
        }


class _RateLimiter:
    """Token-free minimum-interval limiter. Thread-safe and process-local."""

    def __init__(self, per_second: float) -> None:
        if per_second <= 0:
            raise ValueError("per_second must be positive")
        self._min_interval = 1.0 / per_second
        self._lock = threading.Lock()
        self._last = 0.0

    def wait(self) -> None:
        with self._lock:
            now = time.monotonic()
            gap = now - self._last
            if gap < self._min_interval:
                time.sleep(self._min_interval - gap)
            self._last = time.monotonic()


_LIMITER = _RateLimiter(config.MAX_REQUESTS_PER_SECOND)


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _cache_path(url: str) -> Path:
    """Deterministic cache location. Sharded so no directory grows unbounded."""
    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()
    return config.RAW / digest[:2] / digest[2:4] / f"{digest}.bin"


def _atomic_write(path: Path, payload: bytes) -> None:
    """Write via a unique temp file then rename, so a reader never sees a partial file.

    This is a correctness requirement, not tidiness. The cache key is a hash of
    the URL, not of the content, so a half-written file is indistinguishable
    from a complete one on the next run - it would simply be read back as short
    text. In this study short text means a phrase appears absent, and absence is
    what METHOD.md §6 turns into a DISCONTINUED verdict. A truncated cache entry
    would therefore manufacture a finding.

    Two runs of this pipeline overlapped once (Errno 22 on Windows, two
    processes writing the same path), which is what surfaced this. `os.replace`
    is atomic on both POSIX and Windows, and the PID/counter suffix keeps two
    writers from colliding on the temp file itself.
    """
    tmp = path.with_name(f"{path.name}.{os.getpid()}.{next(_TMP_COUNTER)}.tmp")
    try:
        tmp.write_bytes(payload)

        # os.replace is atomic, but on Windows it raises PermissionError
        # (WinError 5) when the destination is briefly held open by another
        # reader - a concurrent build, an indexer, or antivirus. That is
        # transient, and treating it as fatal is worse than useless here: the
        # caller records the document as unreadable, so a filing that was
        # successfully downloaded becomes a hole in the corpus, and a hole in
        # the corpus is what METHOD.md §6 would read as an absent phrase.
        last: OSError | None = None
        for attempt in range(_REPLACE_ATTEMPTS):
            try:
                os.replace(tmp, path)
                return
            except PermissionError as exc:
                last = exc
                time.sleep(0.2 * (attempt + 1))

        # Still blocked. If the destination now exists and is the same size,
        # another writer completed the identical fetch while we waited - the
        # bytes are already on disk, so this is a success, not a loss.
        if path.exists() and path.stat().st_size == len(payload):
            tmp.unlink(missing_ok=True)
            return
        raise last if last else OSError(f"could not place {path}")
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


class EdgarClient:
    """A caching EDGAR reader that records the hash of everything it reads."""

    def __init__(self, contact: str | None = None, *, offline: bool = False) -> None:
        """`offline=True` forbids network access entirely.

        Analysis stages run against the cached corpus and must never fetch. Left
        to discipline this goes wrong: a stage that quietly fetches a missing
        document competes with a running corpus build for the SEC's fair-access
        budget, and both processes then share one 10 requests/second ceiling
        while each believes it owns it. Making the guarantee structural means a
        cache miss raises instead of silently going to the network.
        """
        contact = contact or config.SEC_CONTACT
        if "@" not in contact:
            raise ValueError(
                "SEC requires a contact address in the User-Agent. "
                "Set SEC_CONTACT='Your Name your@email' in the environment."
            )
        self.offline = offline
        self._session = requests.Session()
        self._session.headers.update(
            {
                "User-Agent": contact,
                "Accept-Encoding": "gzip, deflate",
            }
        )
        self._manifest: dict[str, Fetched] = {}

    # -- core fetch --------------------------------------------------------

    def fetch(self, url: str, *, force: bool = False) -> Fetched:
        """Retrieve a URL, using the on-disk cache unless force=True."""
        path = _cache_path(url)

        if path.exists() and not force:
            payload = path.read_bytes()
            record = Fetched(
                url=url,
                path=path,
                sha256=_sha256(payload),
                n_bytes=len(payload),
                from_cache=True,
            )
            self._manifest[url] = record
            return record

        if self.offline:
            raise OfflineCacheMiss(
                f"Not in the local cache and this client is offline: {url}"
            )

        payload = self._get_with_retries(url)
        path.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write(path, payload)

        record = Fetched(
            url=url,
            path=path,
            sha256=_sha256(payload),
            n_bytes=len(payload),
            from_cache=False,
        )
        self._manifest[url] = record
        return record

    def _get_with_retries(self, url: str) -> bytes:
        last_error: Exception | None = None

        for attempt in range(config.MAX_RETRIES):
            _LIMITER.wait()
            try:
                response = self._session.get(
                    url, timeout=config.REQUEST_TIMEOUT_SECONDS
                )
            except requests.RequestException as exc:
                last_error = exc
            else:
                if response.status_code == 200:
                    return response.content

                # 429 and 5xx are worth retrying; 4xx generally is not.
                if response.status_code == 429 or response.status_code >= 500:
                    last_error = EdgarError(
                        f"HTTP {response.status_code} for {url}"
                    )
                else:
                    raise EdgarError(
                        f"HTTP {response.status_code} for {url}: "
                        f"{response.text[:200]}"
                    )

            if attempt < config.MAX_RETRIES - 1:
                time.sleep(config.BACKOFF_BASE_SECONDS * (2**attempt))

        raise EdgarError(f"Failed after {config.MAX_RETRIES} attempts: {url}") from last_error

    # -- typed helpers -----------------------------------------------------

    def fetch_json(self, url: str, *, force: bool = False) -> Any:
        record = self.fetch(url, force=force)
        try:
            return json.loads(record.path.read_bytes())
        except json.JSONDecodeError as exc:
            raise EdgarError(f"Response at {url} is not valid JSON: {exc}") from exc

    def fetch_text(self, url: str, *, force: bool = False) -> str:
        record = self.fetch(url, force=force)
        return record.path.read_bytes().decode("utf-8", errors="replace")

    def submissions(self, cik: int | str) -> dict[str, Any]:
        """The full submissions record for a CIK, including the `items` array."""
        cik10 = normalise_cik(cik)
        return self.fetch_json(f"{config.SEC_DATA}/submissions/CIK{cik10}.json")

    def company_facts(self, cik: int | str) -> dict[str, Any]:
        """XBRL companyfacts. Used only for realised revenue (METHOD.md §7.5)."""
        cik10 = normalise_cik(cik)
        return self.fetch_json(
            f"{config.SEC_DATA}/api/xbrl/companyfacts/CIK{cik10}.json"
        )

    def full_text_search(
        self,
        query: str,
        *,
        forms: str | None = None,
        start: str | None = None,
        end: str | None = None,
        offset: int = 0,
    ) -> dict[str, Any]:
        """EDGAR full-text search. Covers 2001 onward.

        `query` is sent verbatim - wrap it in double quotes for a phrase search.
        """
        params = [f"q={requests.utils.quote(query)}", f"from={offset}"]
        if forms:
            params.append(f"forms={requests.utils.quote(forms)}")
        if start and end:
            params.append(f"dateRange=custom&startdt={start}&enddt={end}")
        url = f"{config.SEC_FTS}?{'&'.join(params)}"
        return self.fetch_json(url)

    # -- manifest ----------------------------------------------------------

    def manifest_rows(self) -> list[dict[str, Any]]:
        """Every document this client has read, sorted for a stable diff."""
        return sorted(
            (record.as_manifest_row() for record in self._manifest.values()),
            key=lambda row: row["url"],
        )

    def write_manifest(self, name: str = "sources.json") -> Path:
        path = config.MANIFEST / name
        rows = self.manifest_rows()
        path.write_text(
            json.dumps(
                {
                    "as_at": config.AS_AT_DATE,
                    "n_documents": len(rows),
                    "documents": rows,
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        return path


# -- pure helpers -----------------------------------------------------------


def normalise_cik(cik: int | str) -> str:
    """CIK as the zero-padded 10-character string every SEC endpoint expects."""
    digits = str(cik).strip().upper().removeprefix("CIK").lstrip("0")
    if not digits.isdigit():
        raise ValueError(f"Not a CIK: {cik!r}")
    return digits.zfill(10)


def accession_no_dashes(accession: str) -> str:
    return accession.replace("-", "")


def filing_index_url(cik: int | str, accession: str) -> str:
    """The filing's index page - the human-readable landing page on sec.gov."""
    cik_plain = str(int(normalise_cik(cik)))
    return (
        f"{config.SEC_WWW}/Archives/edgar/data/{cik_plain}/"
        f"{accession_no_dashes(accession)}/{accession}-index.htm"
    )


def document_url(cik: int | str, accession: str, filename: str) -> str:
    """The URL of one document inside a filing."""
    cik_plain = str(int(normalise_cik(cik)))
    return (
        f"{config.SEC_WWW}/Archives/edgar/data/{cik_plain}/"
        f"{accession_no_dashes(accession)}/{filename}"
    )
