# syntax=docker/dockerfile:1

# Underwritten - deployed image.
#
# What is in here: the FastAPI app, the pipeline package it imports, and the
# committed artifacts under data/ (cohort, adjudication, manifest, derived).
# What is deliberately NOT in here: data/raw/, the cached EDGAR corpus. The site
# serves precomputed artifacts; it does not retrieve filings at request time.
# See .dockerignore, and the assertion below that fails the build if the corpus
# leaks into the context.
#
# Two stages so that no build tool, no uv binary and no wheel cache reach the
# runtime image.

# --- Stage 1: dependencies ------------------------------------------------

FROM python:3.13-slim AS builder

COPY --from=ghcr.io/astral-sh/uv:0.11.8 /uv /bin/uv

ENV UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_COMPILE_BYTECODE=1

WORKDIR /build

# Only pyproject.toml, so this layer is rebuilt when dependencies change and
# not when the research code changes. `-r pyproject.toml` installs the declared
# dependencies without building or installing the project itself; the source is
# copied into the runtime image and imported from the working directory.
COPY pyproject.toml ./
RUN uv venv /opt/venv \
 && uv pip install --python /opt/venv/bin/python --no-cache -r pyproject.toml

# --- Stage 2: runtime -----------------------------------------------------

FROM python:3.13-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PATH="/opt/venv/bin:${PATH}" \
    VIRTUAL_ENV=/opt/venv \
    PORT=8000

COPY --from=builder /opt/venv /opt/venv

WORKDIR /app
COPY . .

# The image must not contain the EDGAR corpus. If .dockerignore is edited and
# this stops being true, the build fails here rather than silently shipping
# several gigabytes of cached filings.
RUN test ! -d /app/data/raw \
 || { echo "REFUSING TO BUILD: data/raw/ is in the build context. See .dockerignore."; exit 1; }

# Non-root. pipeline/config.py creates its data directories on import, so the
# working tree must be writable by the runtime user.
RUN useradd --create-home --uid 10001 --shell /usr/sbin/nologin app \
 && chown -R app:app /app
USER app

EXPOSE 8000

# Railway performs its own healthcheck against deploy.healthcheckPath in
# railway.json; this one is for `docker run` and any other orchestrator.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c 'import os,sys,urllib.request; sys.exit(0 if urllib.request.urlopen("http://127.0.0.1:" + os.environ.get("PORT", "8000") + "/healthz", timeout=4).status == 200 else 1)'

# Railway injects $PORT and it is not stable between deploys, so it is read at
# start time and never hardcoded. The fallback is only for local `docker run`.
# WEB_CONCURRENCY defaults to 1: the site serves precomputed artifacts and each
# worker carries a full pandas/numpy import, so replicas are cheaper than
# workers on a small instance.
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --workers ${WEB_CONCURRENCY:-1} --proxy-headers --forwarded-allow-ips=*"]
