# Staging rollout checklist – backend + frontend

This file turns the higher‑level hosting notes into a **step‑by‑step checklist**
for bringing a staging environment online. It assumes:

- Staging API host: `https://api-staging.healtharchive.ca`
- Staging frontend/preview: `https://healtharchive.vercel.app` +
  branch‑specific preview URLs.
- Code from `main` in both repos has been deployed to the staging host /
  Vercel.

It does **not** require or describe changes on your local machine, beyond
pushing commits; all steps here are meant to be performed on the staging host
and in Vercel / GitHub.

> Note: the current production deployment intentionally runs **without** a
> separate staging backend. If you are following the single‑VPS production
> runbook, you can skip this checklist unless/until you decide to add staging.

For background, see:

- `hosting-and-live-server-to-dos.md`
- `environments-and-configuration.md`
- `healtharchive-frontend/docs/deployment/verification.md`

---

## 1. Backend staging environment

### 1.1 Set env vars on the staging backend host

On the staging host (VM/container/PaaS):

1. Decide where you want jobs and WARCs to live, e.g.:

   ```bash
   /srv/healtharchive/jobs-staging
   ```

2. Configure env vars for the backend app (via systemd env file, Docker env,
   or PaaS UI). Typical values:

   ```bash
   export HEALTHARCHIVE_ENV=staging
   export HEALTHARCHIVE_DATABASE_URL=postgresql+psycopg://user:pass@db-host:5432/healtharchive_staging
   export HEALTHARCHIVE_ARCHIVE_ROOT=/srv/healtharchive/jobs-staging
   export HEALTHARCHIVE_ADMIN_TOKEN="<staging-long-random-secret>"
   export HEALTHARCHIVE_CORS_ORIGINS="https://healtharchive.vercel.app"
   export HEALTHARCHIVE_LOG_LEVEL=INFO
   ```

   Adjust the DB URL, archive root, and CORS origins to match your actual
   staging infrastructure. If you want specific branch preview URLs to call
   the API directly, add them to `HEALTHARCHIVE_CORS_ORIGINS` as a
   comma‑separated list.

### 1.2 Run migrations and seed sources

From a checkout of `healtharchive-backend` at the deployed revision on the
staging host:

```bash
cd /path/to/healtharchive-backend

# Activate venv or ensure dependencies are installed
alembic upgrade head
ha-backend seed-sources
```

This:

- Applies all Alembic migrations to the staging DB.
- Ensures baseline `Source` rows for `hc` and `phac` exist (idempotent).

### 1.3 Start API + worker processes

Configure your process manager (systemd, Docker Compose, PaaS) to run:

- API:

  ```bash
  uvicorn ha_backend.api:app --host 0.0.0.0 --port 8001
  ```

- Worker:

  ```bash
  ha-backend start-worker --poll-interval 30
  ```

Both processes must see the same `HEALTHARCHIVE_*` env vars from 1.1.

### 1.4 Create at least one staging snapshot

For basic end‑to‑end testing, you can start with a tiny synthetic WARC + one
`Snapshot` row, using the recipe from the local live‑testing guide. On the
staging host:

1. Ensure `HEALTHARCHIVE_DATABASE_URL` and `HEALTHARCHIVE_ARCHIVE_ROOT` are set
   (as in 1.1).

2. Run the synthetic WARC script from
   `healtharchive-backend/docs/development/live-testing.md` §6.1 (“Happy‑path
   viewer using a synthetic WARC”). It will:

   - Create a small WARC file under `HEALTHARCHIVE_ARCHIVE_ROOT`.
   - Insert a `Snapshot` row in the staging DB.
   - Print:

     ```text
     SNAPSHOT_ID <N>
     ```

3. Record this `N` as your canonical staging snapshot ID for smoke tests.

4. Quick check (from your own machine):

   ```bash
   curl -i "https://api-staging.healtharchive.ca/api/snapshot/<N>"
   curl -i "https://api-staging.healtharchive.ca/api/snapshots/raw/<N>"
   ```

   - Both should return HTTP 200 with sensible data/HTML.

### 1.5 API health and CORS checks

From your own terminal (not on the staging host):

```bash
curl -i "https://api-staging.healtharchive.ca/api/health"

curl -i \
  -H "Origin: https://healtharchive.vercel.app" \
  "https://api-staging.healtharchive.ca/api/health"
```

Verify:

- HTTP 200 and `"status":"ok"` in the JSON body.
- `Access-Control-Allow-Origin: https://healtharchive.vercel.app`
- `Vary: Origin`

---

## 2. Frontend staging configuration (Vercel Preview)

### 2.1 Preview env vars

In the Vercel project for `healtharchive-frontend`:

1. Go to **Settings → Environment Variables → Preview**.
2. Set:

   ```env
   NEXT_PUBLIC_API_BASE_URL=https://api-staging.healtharchive.ca
   NEXT_PUBLIC_SHOW_API_HEALTH_BANNER=true
   NEXT_PUBLIC_LOG_API_HEALTH_FAILURE=true
   NEXT_PUBLIC_SHOW_API_BASE_HINT=true
   ```

3. Save and trigger a new **Preview** deployment (e.g. by pushing a commit to
   the staging branch or redeploying the latest preview).

### 2.2 Grab the preview URL

After the build completes:

1. Open the Vercel project’s **Deployments** tab.
2. Click the latest **Preview** deployment.
3. Copy its URL, e.g.:

   ```text
   https://healtharchive-git-staging-<hash>.vercel.app
   ```

You will use this URL for the verification steps below.

---

## 3. Staging verification (browser)

The following steps mirror `healtharchive-frontend/docs/deployment/verification.md`
but framed as a checklist.

### 3.1 Frontend security headers & CSP

1. Open the preview `/archive` route in a browser:

   ```text
   https://healtharchive-git-staging-<hash>.vercel.app/archive
   ```

2. In DevTools → Network:
   - Select the main document request (`/archive`).
   - Under *Response headers*, confirm:
     - `Referrer-Policy: strict-origin-when-cross-origin`
     - `X-Content-Type-Options: nosniff`
     - `X-Frame-Options: SAMEORIGIN`
     - `Permissions-Policy: geolocation=(), microphone=(), camera=()`
     - `Content-Security-Policy-Report-Only: ...` with the expected
       `connect-src` and `frame-src` entries for
       `https://api.healtharchive.ca` and `https://api-staging.healtharchive.ca`.

### 3.2 Backend headers & CORS (via frontend)

1. Still on `/archive`, filter the Network tab for requests to
   `https://api-staging.healtharchive.ca`.
2. Inspect `GET /api/health` and `GET /api/search?...`:
   - Confirm HTTP 200 + JSON body.
   - Confirm headers:
     - `X-Content-Type-Options`, `Referrer-Policy`, `X-Frame-Options`,
       `Permissions-Policy`.
     - `Access-Control-Allow-Origin` equal to the preview URL.
     - `Vary: Origin`.

### 3.3 Snapshot viewer iframe

1. Navigate to the staging snapshot using the ID from §1.4:

   ```text
   https://healtharchive-git-staging-<hash>.vercel.app/snapshot/<N>
   ```

2. In DevTools → Elements:
   - Locate the `<iframe>` in the snapshot viewer.
   - Confirm:
     - `src` is
       `https://api-staging.healtharchive.ca/api/snapshots/raw/<N>` (i.e.
       `NEXT_PUBLIC_API_BASE_URL` + the `rawSnapshotUrl` path).
     - `sandbox="allow-same-origin allow-scripts"` is present.

3. In Network:
   - Click the iframe request (`GET /api/snapshots/raw/<N>`).
   - Confirm:
     - The request goes to `api-staging.healtharchive.ca`.
     - Security headers match `/api/health`, except that
       `X-Frame-Options` is intentionally **omitted** on this route so the
       snapshot can be embedded.

### 3.4 Console diagnostics

1. On `/archive`, open the browser console.
2. Confirm you see a log similar to:

   ```text
   [healtharchive] API base URL (from NEXT_PUBLIC_API_BASE_URL or default): https://api-staging.healtharchive.ca
   ```

3. If the backend is unreachable or misconfigured, confirm:
   - The health banner appears (when enabled).
   - `NEXT_PUBLIC_LOG_API_HEALTH_FAILURE` causes an appropriate warning.

---

## 4. Staging sign-off

Once the steps above pass, you can:

- Mark the staging‑related items in `hosting-and-live-server-to-dos.md` as
  complete for the staging environment.
- Use the same patterns (with different env vars and hosts) when bringing
  production online.
