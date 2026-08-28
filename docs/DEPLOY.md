# Deploying the site

The published site is a FastAPI application served by uvicorn, built as a Docker image and
deployed to [Railway](https://railway.com). This document is the whole of the deployment
procedure; there is nothing kept out of it.

---

## What is deployed, and what is not

The site **serves precomputed artifacts only**. It reads what the pipeline has already
committed:

| In the image | Why |
|---|---|
| `app/`, `pipeline/` | The application and the modules it imports. |
| `data/cohort/` | The frozen company list and the exclusions. |
| `data/adjudication/` | The human ruling ledger. |
| `data/derived/` | Computed tables and the specification log. |
| `data/manifest/` | SHA-256 of every document read, for `/provenance`. |
| `METHOD.md`, `CHANGELOG.md` | Rendered by the site. |

| Not in the image | Why |
|---|---|
| **`data/raw/`** | The cached EDGAR corpus: large, gitignored, and fully reproducible from `data/manifest/`. The site does not retrieve filings at request time, so it does not need it. |
| `.venv/`, caches, `tests/`, `docs/`, `.git/` | Not needed to serve a page. |

Both halves are enforced by [`.dockerignore`](../.dockerignore), and the `Dockerfile`
**fails the build** if `data/raw/` appears in the build context anyway:

```dockerfile
RUN test ! -d /app/data/raw \
 || { echo "REFUSING TO BUILD: data/raw/ is in the build context. See .dockerignore."; exit 1; }
```

This is deliberate rather than tidy-minded. A container that can reach sec.gov at request
time is a container that can silently answer a page from something other than the
committed, hashed artifacts, and the provenance claim in `METHOD.md` §11 depends on that
never happening.

---

## Requirement on the application: `GET /healthz`

Both Railway's healthcheck and the image's own `HEALTHCHECK` poll **`/healthz`**. The
application must therefore expose it. This is a statement of the requirement, not a change
to the app — `app/main.py` is owned elsewhere.

The endpoint must:

- respond to `GET /healthz` with HTTP **200** once the process is ready to serve;
- answer **without** touching the network, and ideally without touching disk beyond what is
  already loaded — it is polled every 30 seconds for the life of the container;
- return quickly. A useful body is a small JSON object such as
  `{"status": "ok", "as_at": "2026-08-28"}`, but the status code is what is checked.

It should **not** be authenticated, and it should **not** report healthy before the
precomputed artifacts it needs have loaded — a container that answers 200 while unable to
serve a page will pass the deploy and fail the reader.

If the path ever changes, three places change with it: `healthcheck` in
`.railway/railway.ts`, the `HEALTHCHECK` line in `Dockerfile`, and this section.

> **As at 28 August 2026 the route does not exist.** Running the built image and requesting
> `/healthz` returns `404 {"detail":"Not Found"}`; the application currently exposes `/`,
> `/note`, `/cohort`, `/positions`, `/resolved`, `/method`, `/provenance` and `/changelog`.
> **Railway will therefore fail the healthcheck and refuse to promote the deployment until
> `/healthz` is added to `app/main.py`.** Delete this note once it is.

---

## Port binding

Railway assigns a port per deployment and injects it as **`$PORT`**. It is not stable
between deploys, so it is read at start time and never hardcoded. The container binds
`0.0.0.0:$PORT`; binding `127.0.0.1` would make the service unreachable from Railway's
proxy no matter what else is correct.

The start command lives in **one** place — the `Dockerfile` `CMD`:

```dockerfile
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --workers ${WEB_CONCURRENCY:-1} --proxy-headers --forwarded-allow-ips=*"]
```

`.railway/railway.ts` deliberately does **not** set a start command. Two definitions of how
the process starts is one definition too many, and the one in the Dockerfile is the one
that also applies locally. The `8000` fallback exists only for `docker run` on a laptop.

`exec` matters: uvicorn becomes PID 1 and receives Railway's `SIGTERM` directly, so
redeploys drain rather than get killed.

---

## Building and running locally

```bash
docker build -t underwritten .
docker run --rm -p 8000:8000 -e PORT=8000 underwritten
# then: curl -fsS http://127.0.0.1:8000/healthz
```

To check the port is genuinely dynamic — the failure this guards against does not show up
when `PORT` happens to be 8000:

```bash
docker run --rm -p 9123:9123 -e PORT=9123 underwritten
curl -fsS http://127.0.0.1:9123/healthz
```

The image is a two-stage build: stage one installs dependencies into `/opt/venv` with
`uv`, stage two copies that venv into a clean `python:3.13-slim` and adds the source. No
build tool, no `uv` binary and no wheel cache reaches the runtime image. Only
`pyproject.toml` is copied into the dependency layer, so editing research code does not
reinstall pandas.

The container runs as a non-root user (`uid 10001`). `pipeline/config.py` creates its data
directories on import, so `/app` is chowned to that user; if you change `WORKDIR`, keep
that pairing.

---

## Deploying to Railway

Install the [Railway CLI](https://docs.railway.com/cli), then, from the repository root:

```bash
railway login          # opens a browser; creates the account if you do not have one
railway init           # creates the project and links this directory to it
railway up             # uploads the build context, builds the Dockerfile, deploys
railway domain         # assigns a public *.up.railway.app domain and prints the URL
```

`railway up` streams build and deploy logs. `railway domain` is what actually makes the
service reachable — until it is run, the deployment is healthy and private.

Afterwards:

```bash
railway logs           # runtime logs
railway status         # project, environment, service
railway variables      # what the service can see
```

### Variables to set

```bash
railway variables --set "SEC_CONTACT=Your Name you@example.com"
```

| Variable | Set by | Notes |
|---|---|---|
| `PORT` | **Railway** | Injected. Never set it yourself. |
| `SEC_CONTACT` | You | Not needed to serve precomputed artifacts, but `pipeline/config.py` falls back to a hardcoded default if it is unset. Set it explicitly so the deployed configuration is the same object as the local one. |
| `WEB_CONCURRENCY` | You, optionally | uvicorn workers; defaults to 1. Each worker carries a full pandas/numpy import, so on a small instance replicas are cheaper than workers. Raise it only after watching memory. |

Do not put `SEC_CONTACT` in the Railway config file. It is a real address and
configuration files are public; the same value is a repository secret for GitHub Actions,
for the same reason.

---

## Configuration lives in `.railway/railway.ts`, not `railway.json`

`railway.json` was written first and then **deleted**. Railway's own documentation states
that Config as Code is deprecated, that **"new services cannot opt into Config as Code"**,
and that existing files stop being read on **2026-12-01** (hard cutoff).

This is a new service. A `railway.json` in this repository would therefore have been read
by nobody, while looking exactly like working configuration — the healthcheck would simply
never have been set, and nothing would have said so. Dead config that looks live is worse
than no config, so it is gone.

The committed replacement is `.railway/railway.ts`:

```ts
import { defineRailway, project, service } from "railway/iac";

export default defineRailway(() => {
  const web = service("underwritten", {
    healthcheck: "/healthz",
    healthcheckTimeout: 120,
  });

  return project("underwritten", { resources: [web] });
});
```

Apply it with:

```bash
npm install railway     # the IaC DSL is a real npm package
railway link
railway config plan     # preview; safe, read-only
railway config apply    # applies after confirmation
```

- **`healthcheck: /healthz`** — Railway polls this before routing traffic to a new
  deployment. A deployment that never answers 200 is not promoted, so a broken build
  cannot replace a working site. The route must not touch the filesystem or render a
  template, so that it succeeds even when `data/derived/` is empty.
- **`healthcheckTimeout: 120`** — seconds to wait for the first healthy response.
  Generous on purpose: the app loads precomputed artifacts at import.

**Note the gap, since it is a real regression.** The IaC service DSL exposes `healthcheck`
and `healthcheckTimeout` but **no restart-policy field**. The restart policy that
`railway.json` used to carry (`ON_FAILURE`, max 10 retries) cannot be expressed in code
and must be set by hand if wanted: *Settings → Deploy → Restart Policy* = `On Failure`,
max retries `10`. That is less auditable than before, and it is recorded here rather than
glossed over.

**Build and start are not set here on purpose.** The `Dockerfile` is the single definition
of how the process starts, so the container running on Railway is the one that runs
locally.

If IaC is not wanted at all, the fallback is entirely manual and works fine for one
service: deploy with `railway up`, then set *Settings → Deploy → Healthcheck Path* =
`/healthz` in the dashboard. Nothing else in this document depends on the choice.

---

## Troubleshooting

**Healthcheck never passes.** Almost always the port. Confirm the process logs
`Uvicorn running on http://0.0.0.0:<port>` and that `<port>` matches `$PORT` in
`railway variables`. Then confirm `/healthz` exists and is unauthenticated — a 404 and a
401 both fail the check identically.

**Build fails with `REFUSING TO BUILD: data/raw/`.** `.dockerignore` has been edited or
lost. Restore the `data/raw/` line. Do not remove the assertion.

**Build cannot pull `ghcr.io/astral-sh/uv:0.11.8`.** That pinned tag supplies the `uv`
binary to the builder stage only. Bump it if the tag is withdrawn; do not switch to
`:latest`, which would make the dependency layer non-reproducible.

**Build fails at `uv pip install` with `invalid peer certificate: UnknownIssuer`.** This is
the same TLS-inspecting proxy described in the README, hitting the *build* rather than the
application: `uv` inside the container does not trust the proxy's CA, so it cannot reach
pypi.org. It does not occur on Railway, whose builders are not behind such a proxy, and the
workaround does not belong in this Dockerfile. To build locally behind one, pass the CA
bundle in as a BuildKit secret from a scratch copy of the Dockerfile rather than committing
the workaround:

```dockerfile
RUN --mount=type=secret,id=ca,target=/tmp/ca.pem \
    SSL_CERT_FILE=/tmp/ca.pem uv venv /opt/venv \
 && SSL_CERT_FILE=/tmp/ca.pem uv pip install --python /opt/venv/bin/python --no-cache -r pyproject.toml
```

```bash
docker build -f Dockerfile.local --secret id=ca,src=/path/to/corp-ca.pem -t underwritten .
```

**TLS failures reaching sec.gov during local development.** Expected behind a
TLS-inspecting proxy, and handled: `truststore` routes Python's SSL through the OS trust
store. See the TLS note in the [README](../README.md#reproducing-it). It does not arise on
Railway's Linux images, where certifi already works. Certificate verification is never
disabled in either environment.

**A deploy is healthy but the URL 404s at the apex.** `railway domain` has not been run, or
was run against a different service. `railway status` shows which service is linked.
