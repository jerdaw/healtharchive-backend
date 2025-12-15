# Production rollout checklist – backend + frontend

This file is a **step‑by‑step checklist** for bringing the **production**
environment online, based on the same patterns used for staging.

Assumptions:

- Production API host: `https://api.healtharchive.ca`
- Production frontend: `https://healtharchive.ca` and `https://www.healtharchive.ca`
- Code from `main` in both repos is what you intend to deploy.

Everything here happens on:

- The production backend host (VM/container/PaaS).
- Vercel (for the frontend).
- GitHub (for CI/branch protection).

Nothing in this file requires changes to your local dev environment.

For background, see:

- `hosting-and-live-server-to-dos.md`
- `environment-matrix.md`
- `production-single-vps.md` (current production runbook)
- `staging-rollout-checklist.md` (optional future)

---

## 1. Backend production environment

### 1.1 Set env vars on the production backend host

On the production host (VM/container/PaaS):

1. Decide where you want production jobs and WARCs to live, e.g.:

   ```bash
   /srv/healtharchive/jobs
   ```

2. Configure env vars for the backend app (via systemd env file, Docker env,
   or PaaS UI). Typical values:

   ```bash
   export HEALTHARCHIVE_ENV=production
   export HEALTHARCHIVE_DATABASE_URL=postgresql+psycopg://user:pass@db-host:5432/healtharchive
   export HEALTHARCHIVE_ARCHIVE_ROOT=/srv/healtharchive/jobs
   export HEALTHARCHIVE_ADMIN_TOKEN="<prod-long-random-secret>"
   export HEALTHARCHIVE_CORS_ORIGINS="https://healtharchive.ca,https://www.healtharchive.ca"
   export HEALTHARCHIVE_LOG_LEVEL=INFO
   ```

   Adjust the DB URL and archive root to match your actual production
   infrastructure. `HEALTHARCHIVE_CORS_ORIGINS` should be as narrow as
   possible in production: usually just the public frontend origins.

### 1.2 Run migrations and seed sources

From a checkout of `healtharchive-backend` at the deployed revision on the
production host:

```bash
cd /path/to/healtharchive-backend

# Activate venv or ensure dependencies are installed
alembic upgrade head
ha-backend seed-sources
```

This:

- Applies all Alembic migrations to the production DB.
- Ensures baseline `Source` rows exist (idempotent).

If you have deployed the link-signal schema (tables `snapshot_outlinks` and `page_signals`),
recompute page signals (includes `pagerank` when present):

```bash
ha-backend recompute-page-signals
```

### 1.3 Start API + worker processes

Configure your process manager to run:

- API:

  ```bash
  uvicorn ha_backend.api:app --host 0.0.0.0 --port 8001
  ```

- Worker:

  ```bash
  ha-backend start-worker --poll-interval 30
  ```

Both processes must see the same `HEALTHARCHIVE_*` env vars from 1.1.

Optional (recommended): enable the blended search ranking by default:

```bash
export HA_SEARCH_RANKING_VERSION=v2
```

Rollback is instant: set `HA_SEARCH_RANKING_VERSION=v1` and restart the API process.

### 1.4 DNS and TLS for the API

In your DNS provider (e.g. Namecheap, Cloudflare, Route 53):

1. Create/verify records for `api.healtharchive.ca`:
   - `A` / `AAAA` pointing at the backend host IP, or
   - `CNAME` pointing at a load balancer / PaaS hostname.

2. Ensure TLS is terminated correctly:
   - Use Let’s Encrypt or a managed certificate for `api.healtharchive.ca`.
   - Configure HTTP→HTTPS redirects.
   - Add an HSTS header at the reverse proxy/load balancer layer, e.g.:

     ```nginx
     add_header Strict-Transport-Security "max-age=31536000; includeSubDomains" always;
     ```

3. Quick checks from your own machine:

   ```bash
   curl -i "https://api.healtharchive.ca/api/health"

   curl -i \
     -H "Origin: https://healtharchive.ca" \
     "https://api.healtharchive.ca/api/health"
   ```

   Verify:

   - HTTP 200 and `"status":"ok"` in the JSON body.
   - `Access-Control-Allow-Origin: https://healtharchive.ca` and `Vary: Origin`.

### 1.5 Seed initial production snapshots

How you seed production is a policy choice; some options:

- Use a few small, controlled crawls driven by the worker:
  - `ha-backend create-job --source hc`
  - `ha-backend create-job --source phac`
  - Let the worker process these jobs and attempt indexing.
- Use a synthetic WARC snapshot (same pattern as staging) for a minimal
  initial smoke test.

At minimum, create one snapshot and note its ID `N_prod` so you can test the
viewer end‑to‑end:

```bash
curl -i "https://api.healtharchive.ca/api/snapshot/<N_prod>"
curl -i "https://api.healtharchive.ca/api/snapshots/raw/<N_prod>"
```

---

## 2. Frontend production configuration (Vercel)

### 2.1 Production env vars (Vercel)

In the Vercel project for `healtharchive-frontend`:

1. Go to **Settings → Environment Variables → Production**.
2. Set:

   ```env
   NEXT_PUBLIC_API_BASE_URL=https://api.healtharchive.ca
   NEXT_PUBLIC_SHOW_API_HEALTH_BANNER=false
   NEXT_PUBLIC_LOG_API_HEALTH_FAILURE=false
   NEXT_PUBLIC_SHOW_API_BASE_HINT=false
   ```

3. Save and trigger a new **Production** deployment (by pushing to `main` or
   clicking **Redeploy** for the latest `main` commit).

### 2.2 Frontend domains

In Vercel + DNS:

- Ensure:
  - `healtharchive.ca` and `www.healtharchive.ca` are pointed at Vercel.
  - Any old records (e.g., GitHub Pages IPs) have been removed.

Once the production deployment completes, visiting `https://healtharchive.ca`
should show the live frontend.

---

## 3. Production verification (browser)

With the production backend and frontend deployed:

### 3.1 Archive pages

1. Visit:

   ```text
   https://healtharchive.ca/archive
   ```

2. Verify:
   - The filters header shows `Filters (live API)` when the backend is up.
   - If the DB has snapshots, results reflect real data (no demo fallback
     notice).

3. Visit:

   ```text
   https://healtharchive.ca/archive/browse-by-source
   ```

   - Cards should show real counts from `/api/sources`.

### 3.2 Snapshot viewer

1. Visit the production snapshot using `N_prod` from §1.5:

   ```text
   https://healtharchive.ca/snapshot/<N_prod>
   ```

2. Confirm:
   - Metadata (title, source, date, language, URL) matches
     `/api/snapshot/<N_prod>`.
   - “Open raw snapshot” opens `https://api.healtharchive.ca/api/snapshots/raw/<N_prod>`.
   - The embedded iframe loads the same URL and renders the HTML.

3. In DevTools → Network:
   - Confirm the iframe request goes to `api.healtharchive.ca`.
   - Confirm security headers match staging expectations (no
     `X-Frame-Options` on the raw snapshot route; other headers present).

---

## 4. Monitoring & CI sign‑off

Once production is healthy, tie this back to:

- **Monitoring & uptime** (see `hosting-and-live-server-to-dos.md` §5.3):
  - Configure uptime checks for:
    - `https://api.healtharchive.ca/api/health`
    - `https://healtharchive.ca/archive`
  - If you have Prometheus or similar, scrape:
    - `https://api.healtharchive.ca/metrics`
    - Build alerts on:
      - `healtharchive_jobs_total{status="failed"}`
      - `healtharchive_snapshots_total`
      - `healtharchive_jobs_cleanup_status_total{cleanup_status="temp_cleaned"}`

- **CI & branch protection** (see `hosting-and-live-server-to-dos.md` §7):
  - Ensure GitHub Actions workflows are enabled and passing.
  - Configure branch protection on `main` to require CI checks before merging.

With this in place, `main` deploys cleanly to production, and you have health
and metrics coverage for both the API and the frontend.
