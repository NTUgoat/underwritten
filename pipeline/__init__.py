"""Underwritten data pipeline.

TLS note: this machine sits behind a TLS-inspecting proxy whose CA is present in
the OS trust store but not in certifi's bundle, so `requests` fails to verify
sec.gov while `curl` succeeds. `truststore` routes Python's SSL through the
operating system's trust store, which fixes it without ever disabling
verification. Injection is best-effort: on hosts where it is unavailable or
unnecessary (Railway's Linux images), certifi already works and the import is a
no-op.

Verification is never disabled. If both paths fail, the fetch fails loudly.
"""

from __future__ import annotations

try:  # pragma: no cover - environment dependent
    import truststore

    truststore.inject_into_ssl()
except Exception:  # noqa: BLE001 - any failure here is non-fatal by design
    pass
