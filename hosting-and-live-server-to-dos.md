# Hosting & Live Server TODOs (Backend + Frontend)

This document tracks the remaining **infrastructure / hosting steps** needed to
run HealthArchive.ca with a fully wired frontend + backend in staging and
production environments.

Nothing in here requires code changes – it is all environment configuration,
DNS, and manual verification.

---

## 0. Quick index of “remote‑only” tasks

Use this as a map of everything that must be done **outside** your local dev
environment (i.e., on live servers, in Vercel, or in the GitHub UI). Each item
links to the detailed checklist later in this file.

- **On backend servers (staging + production)** – see §2 and §4:
  - [ ] Provision a Postgres DB and set `HEALTHARCHIVE_DATABASE_URL`.
  - [ ] Choose and provision storage for `HEALTHARCHIVE_ARCHIVE_ROOT`.
  - [ ] Configure `HEALTHARCHIVE_ADMIN_TOKEN` and `HEALTHARCHIVE_CORS_ORIGINS`.
  - [ ] Reload/restart the backend service with the new env vars.
  - [ ] Verify `/api/health`, `/api/sources`, `/api/search`, and CORS headers
        over HTTPS.
  - [ ] Ensure HTTPS is enforced (HTTP→HTTPS redirect) and HSTS is enabled for
        `api.healtharchive.ca` (and `api-staging.healtharchive.ca` if used).
  - [ ] Configure DNS for `api.healtharchive.ca` (and optionally
        `api-staging.healtharchive.ca`) pointing at the backend.

- **In Vercel for the frontend** – see §3 and §5:
  - [ ] Ensure the `healtharchive-frontend` GitHub repo is connected to a
        Vercel project.
  - [ ] Set `NEXT_PUBLIC_API_BASE_URL` for **Production** and **Preview**
        environments.
  - [ ] Configure diagnostics flags
        (`NEXT_PUBLIC_SHOW_API_HEALTH_BANNER`,
        `NEXT_PUBLIC_LOG_API_HEALTH_FAILURE`,
        `NEXT_PUBLIC_SHOW_API_BASE_HINT`) per environment.
  - [ ] Trigger deployments and run the browser‑side smoke checks on
        `/archive`, `/archive/browse-by-source`, and `/snapshot/[id]`.

- **In GitHub for both repos** – see §7:
  - [ ] Commit and push CI workflows:
        `.github/workflows/backend-ci.yml` and
        `.github/workflows/frontend-ci.yml`.
  - [ ] Enable Actions in the GitHub UI if prompted.
  - [ ] Configure branch protection on `main` to require the CI checks before
        merging.

You can tick off these high‑level items as you go, using the later sections for
the exact commands and UI steps.

---

## 1. Decide canonical URLs (one‑time decision)

Before configuring env vars, confirm the URLs you want to use:

- **Frontend – production**
  - `https://healtharchive.ca`
  - `https://www.healtharchive.ca`

- **Frontend – staging / preview**
  - `https://healtharchive.vercel.app` (Vercel default)
  - plus any branch‑preview URLs Vercel creates

- **Backend – production API**
  - e.g. `https://api.healtharchive.ca`

- **Backend – staging API** (optional)
  - e.g. `https://api-staging.healtharchive.ca`
  - If you don’t want a separate staging API, previews can re‑use
    `https://api.healtharchive.ca`.

Once you’re happy with those hostnames, the remaining steps in this document
assume that naming. Substitute your actual choices as needed.

---

## 2. Backend configuration (CORS + env)

The backend already supports CORS and uses environment variables for its DB
and archive root. Production/staging configuration is about **setting the
right env vars in the host environment** and restarting the service.

### 2.1. Environment variables to set

On each backend deployment (systemd unit, Docker container, or PaaS app),
configure the following environment variables **on the remote host** (not just
in your local shell). Typical flow:

1. SSH into the server or open your cloud provider’s “environment variables”
   UI for the backend app.
2. Add/update the variables below.
3. Restart the backend service (see §2.2).

- `HEALTHARCHIVE_DATABASE_URL`
  - Points at your production/staging DB (Postgres recommended).
  - Example:

    ```bash
    export HEALTHARCHIVE_DATABASE_URL=postgresql+psycopg://user:pass@db-host:5432/healtharchive
    ```

- `HEALTHARCHIVE_ENV`
  - High‑level environment hint used by admin auth.
  - Recommended values:
    - `development` (or unset) for local dev.
    - `staging` for staging hosts.
    - `production` for production hosts.
  - When `HEALTHARCHIVE_ENV` is `staging` or `production` and
    `HEALTHARCHIVE_ADMIN_TOKEN` is **unset**, admin and metrics endpoints fail
    closed with HTTP 500 instead of being left open.

- `HEALTHARCHIVE_ARCHIVE_ROOT`
  - Root directory where crawl jobs and WARCs will be written.
  - Must be on a filesystem with enough space and backups appropriate for
    your risk tolerance.

    ```bash
    export HEALTHARCHIVE_ARCHIVE_ROOT=/srv/healtharchive/jobs
    ```

- `HEALTHARCHIVE_ADMIN_TOKEN`
  - Token required for `/api/admin/*` and `/metrics` when set.
  - Should be a strong random string, stored only in secure places (not
    committed to git).

    ```bash
    export HEALTHARCHIVE_ADMIN_TOKEN="some-long-random-string"
    ```

- `HEALTHARCHIVE_CORS_ORIGINS`
  - **Critical for frontend integration.**
  - Comma‑separated list of frontend origins allowed to call the public API.
  - When set, overrides the built‑in defaults.

  **Production example** (frontend at `healtharchive.ca`):

  ```bash
  export HEALTHARCHIVE_CORS_ORIGINS="https://healtharchive.ca,https://www.healtharchive.ca"
  ```

  **Staging example** (frontend at `healtharchive.vercel.app`):

  ```bash
  export HEALTHARCHIVE_CORS_ORIGINS="https://healtharchive.vercel.app"
  ```

  **Optional local dev access to prod/staging API**:

  ```bash
  export HEALTHARCHIVE_CORS_ORIGINS="https://healtharchive.ca,https://www.healtharchive.ca,http://localhost:3000"
  # or with staging:
  export HEALTHARCHIVE_CORS_ORIGINS="https://healtharchive.vercel.app,http://localhost:3000"
  ```

### 2.2. Apply config and restart services

How you do this depends on your hosting stack:

- **systemd unit**:
  - Add env vars to the unit file (`Environment=` lines) or a drop‑in
    `EnvironmentFile=/etc/default/healtharchive-backend`.
  - Reload + restart:
    ```bash
    sudo systemctl daemon-reload
    sudo systemctl restart healtharchive-backend.service
    ```

- **Docker / Docker Compose**:
  - Add env vars under `environment:` in your compose file or `docker run`
    command.
  - Recreate containers:
    ```bash
    docker compose up -d --force-recreate backend
    ```

- **PaaS (Render, Fly.io, Heroku, etc.)**:
  - Use the provider’s UI/CLI to set env vars.
  - Trigger a deployment or restart.

In staging and production you will typically run **two** backend processes:

- An API process (FastAPI + uvicorn) that serves `/api/**` and `/metrics`.
- A worker process (`ha-backend start-worker --poll-interval 30`) that
  continuously processes queued jobs.

Both processes must see the same `HEALTHARCHIVE_DATABASE_URL`,
`HEALTHARCHIVE_ARCHIVE_ROOT`, and related env vars from §2.1 so they share
jobs and archive output consistently.

### 2.3. Backend smoke checks (staging/prod)

From a machine that can reach the backend host:

1. **API health**

   ```bash
   curl -i "https://api.healtharchive.ca/api/health"
   ```

   Check:
   - HTTP 200.
   - JSON body like:
     ```json
     {"status":"ok","checks":{"db":"ok","jobs":{...},"snapshots":{"total":...}}}
     ```

2. **CORS headers**

   - Call the API with a fake `Origin` header matching your frontend:
     ```bash
     curl -i \
       -H "Origin: https://healtharchive.ca" \
       "https://api.healtharchive.ca/api/health"
     ```
   - Response should include:
     ```text
     Access-Control-Allow-Origin: https://healtharchive.ca
     Vary: Origin
     ```

3. **Basic public routes**

   - Verify:
     ```bash
     curl -i "https://api.healtharchive.ca/api/sources"
     curl -i "https://api.healtharchive.ca/api/search?page=1&pageSize=10"
     ```
  - Expect HTTP 200, JSON bodies, and CORS headers.

4. **Security headers**

   - Confirm that security-related headers are present on responses:

     ```bash
     curl -i "https://api.healtharchive.ca/api/health" | sed -n '1,20p'
     ```

   - Look for:
     - `X-Content-Type-Options: nosniff`
     - `Referrer-Policy: strict-origin-when-cross-origin`
     - `X-Frame-Options: SAMEORIGIN`
     - `Permissions-Policy: geolocation=(), microphone=(), camera=()`

---

### 2.4. Archive storage & retention

The `HEALTHARCHIVE_ARCHIVE_ROOT` directory is where crawl jobs and WARCs live.
In staging and production you should treat it as **persistent, non‑ephemeral
storage** and have a basic retention plan.

Checklist for each non‑dev environment:

- [ ] Place `HEALTHARCHIVE_ARCHIVE_ROOT` on a filesystem that:
  - Is not ephemeral (survives VM/container restarts).
  - Has enough capacity for expected WARCs and logs.
  - Has a backup or snapshot policy appropriate for your risk tolerance.
- [ ] Decide whether this path is:
  - Backed up regularly (if you want WARCs as part of a disaster‑recovery
    story), or
  - Treated as “best‑effort cache” (if you rely on ZIMs/exports or other
    secondary storage).
- [ ] Decide when it is safe to delete temporary crawl artifacts:
  - Only once jobs are `indexed` or `index_failed` *and* you have verified any
    desired ZIMs/exports.
  - Use the `ha-backend cleanup-job --id JOB_ID --mode temp` command for this
    cleanup; it removes `.tmp*` directories and `.archive_state.json` but
    leaves the main job directory and any final ZIMs.
- [ ] For larger deployments, consider:
  - Keeping a simple inventory of jobs (via `/api/admin/jobs` and metrics) so
    you know roughly how many indexed jobs you have and how big `jobs/` is.
  - Periodically reviewing `cleanup_status` via `/metrics`
    (`healtharchive_jobs_cleanup_status_total{cleanup_status="temp_cleaned"}`)
    to ensure temp artifacts are being pruned over time.

For local development it is sufficient to keep `HEALTHARCHIVE_ARCHIVE_ROOT`
inside the repo (e.g. `./.dev-archive-root`) and delete it manually when you
want a clean slate.

---

## 3. Frontend configuration (Vercel env vars)

The Next.js app reads `NEXT_PUBLIC_API_BASE_URL` at build time and uses it for
all backend requests. It must be set separately for each environment in Vercel.

### 3.1. Production env vars (Vercel)

In the Vercel dashboard for the `healtharchive-frontend` project:

1. Log in to https://vercel.com with the GitHub account that owns
   `healtharchive-frontend`.
2. From the Vercel dashboard, click the **healtharchive-frontend** project.
3. Go to **Settings → Environment Variables**.
4. Under **Production**, add:

   ```env
   NEXT_PUBLIC_API_BASE_URL=https://api.healtharchive.ca
   ```

5. (Optional, but recommended) keep diagnostics **off** in production:

   ```env
   NEXT_PUBLIC_SHOW_API_HEALTH_BANNER=false
   NEXT_PUBLIC_LOG_API_HEALTH_FAILURE=false
   NEXT_PUBLIC_SHOW_API_BASE_HINT=false
   ```

6. Trigger a new deployment of the `main` branch:
   - Either click **Deploy** for the latest `main` commit in Vercel, or push a
     new commit to `main` so Vercel automatically builds and deploys.

### 3.2. Preview / staging env vars

Still in Vercel:

1. In the same **Settings → Environment Variables** screen, switch to the
   **Preview** tab.
2. Under **Preview** environment variables, add:

   ```env
   NEXT_PUBLIC_API_BASE_URL=https://api-staging.healtharchive.ca
   # or reuse https://api.healtharchive.ca if you don't have a separate staging API
   ```

3. Enable diagnostics to make issues more obvious:

   ```env
   NEXT_PUBLIC_SHOW_API_HEALTH_BANNER=true
   NEXT_PUBLIC_LOG_API_HEALTH_FAILURE=true
   NEXT_PUBLIC_SHOW_API_BASE_HINT=true
   ```

4. Deploy a preview build (e.g., push to a feature branch or `staging`
   branch) and confirm that Vercel creates a new preview URL under
   `https://healtharchive.vercel.app` for that commit.

### 3.3. Local development env (already mostly done)

In `healtharchive-frontend/.env.local` (not committed):

```env
NEXT_PUBLIC_API_BASE_URL=http://127.0.0.1:8001
NEXT_PUBLIC_SHOW_API_HEALTH_BANNER=true
NEXT_PUBLIC_LOG_API_HEALTH_FAILURE=true
NEXT_PUBLIC_SHOW_API_BASE_HINT=true
```

This is the template for your local dev; Vercel envs for Preview/Production
should mirror the same shape but with different API URLs and diagnostics
typically disabled in production.

---

## 4. DNS TODOs

Ensure DNS records are in place for the backend hosts.

### 4.1. Production API DNS

- In your DNS provider’s UI (e.g., Namecheap, Cloudflare, Route 53), locate the
  zone for `healtharchive.ca`.
- Create a record for `api.healtharchive.ca`:
  - If the backend is on a VM with a fixed IP:
    - Add an `A` record (and `AAAA` for IPv6 if applicable) pointing to the
      backend server IP.
  - If the backend is behind a load balancer or PaaS:
    - Add a `CNAME` pointing at the provider hostname (e.g.,
      `your-app.region.cloudprovider.com`).

### 4.2. Staging API DNS (optional)

- If you want a separate staging backend, create `api-staging.healtharchive.ca`
  in the same DNS zone:
  - Use an `A`/`AAAA` record (for a separate staging VM) or a `CNAME` (for a
    staging app/load balancer) pointing at the staging backend host.

After DNS is configured:

- Verify with:
  ```bash
  dig +short api.healtharchive.ca
  dig +short api-staging.healtharchive.ca
  ```
- Then run the API health curl commands in §2.3 against the HTTPS URLs.

### 4.3. TLS / HTTPS and HSTS

- Terminate TLS (HTTPS) for `api.healtharchive.ca` (and
  `api-staging.healtharchive.ca` if applicable) at your reverse proxy or load
  balancer:
  - Use Let's Encrypt or a managed certificate.
  - Configure HTTP→HTTPS redirects for all HTTP traffic.
- Add an `Strict-Transport-Security` header on HTTPS responses to enforce
  long-lived HTTPS in browsers. For example, in Nginx:

  ```nginx
  add_header Strict-Transport-Security "max-age=31536000; includeSubDomains" always;
  ```

- After enabling HSTS, verify with:

  ```bash
  curl -i "https://api.healtharchive.ca/api/health" | grep -i strict-transport-security
  ```

---

## 5. End‑to‑end smoke checklist (staging/prod)

Once backend env vars, Vercel env vars, and DNS are in place:

### 5.1. From the frontend domain

On **production** (`https://healtharchive.ca`) and/or **staging**:

1. Visit `/archive`:
   - With backend up:
     - Filters header should show `Filters (live API)`.
     - If the DB has snapshots, you’ll see real data (no demo fallback notice).
   - With backend intentionally stopped (only in staging):
     - A small “Backend unreachable” banner appears (if enabled).
     - Filters header changes to `Filters (demo dataset fallback)`.
     - Demo records appear instead of live data.

2. Try filtering:
   - Choose a source and topic, e.g. `source=hc`, `topic=covid-19`.
   - URL updates with `?source=hc&topic=covid-19`.
   - Results list changes accordingly (when live snapshots exist).

3. Navigate to `/archive/browse-by-source`:
   - With backend up:
     - Cards should show real record counts and topics from `/api/sources`.
   - With backend down (staging):
     - “Backend unavailable” callout appears and demo summaries are shown.

4. Open a snapshot detail page `/snapshot/[id]`:
   - For a real backend snapshot ID:
     - Metadata (title, source, date, language, URL) is from `/api/snapshot/{id}`.
     - “Open raw snapshot” ultimately points at `https://api…/api/snapshots/raw/{id}`
       on the API host (the frontend prefixes the `rawSnapshotUrl` path from
       the API with `NEXT_PUBLIC_API_BASE_URL`).
   - For a demo snapshot ID:
     - Metadata comes from the bundled demo dataset, and the iframe points
       into `/demo-archive/**`.

### 5.2. Console diagnostics (staging)

On staging, with diagnostics enabled:

- Open `/archive` and check the browser console:
  - You should see something like:
    ```text
    [healtharchive] API base URL (from NEXT_PUBLIC_API_BASE_URL or default): https://api-staging.healtharchive.ca
    ```
  - If the base URL is wrong or the API is unreachable, the health banner and
    warning logs will make it obvious.

If all of the above checks pass in staging, you can safely mirror the env and
DNS configuration to production (with diagnostics turned off) and deploy.  

This document should be revisited and checked off as each environment (local,
staging, production) is brought fully online.

For a more detailed staging rollout, see:

- `docs/staging-rollout-checklist.md`

For a more detailed production rollout, see:

- `docs/production-rollout-checklist.md`

For a more detailed staging verification of CSP, headers, CORS, and the
snapshot viewer iframe behavior, see:

- `healtharchive-frontend/docs/staging-verification.md`

---

## 5.3. Monitoring & uptime checks (optional but recommended)

- Configure an external uptime monitor (e.g., UptimeRobot, healthchecks.io, or
  your cloud provider) to poll:
  - `https://api.healtharchive.ca/api/health` (backend health).
  - `https://healtharchive.ca/archive` (frontend & backend integration).
- Configure alerts (email/Slack/etc.) for repeated failures or slow responses.
- If you deploy Prometheus or a similar system, scrape
  `https://api.healtharchive.ca/metrics` and build dashboards/alerts for:
  - `healtharchive_jobs_total{status="failed"}` – job failures.
  - `healtharchive_snapshots_total` – sudden jumps in snapshot count.

---

## 6. Admin / operator access TODOs

- [ ] Configure `HEALTHARCHIVE_ADMIN_TOKEN` in every **non‑dev** environment:
  - Set a long, random value via your hosting platform’s secret manager.
  - Do **not** commit the token to the repo or to any checked‑in `.env` file.
- [ ] Verify that `/api/admin/*` and `/metrics` require the token:
  - Without headers:
    ```bash
    curl -i "https://api.healtharchive.ca/api/admin/jobs"
    curl -i "https://api.healtharchive.ca/metrics"
    ```
    Expect `403 Forbidden` when the token is configured.
  - With token:
    ```bash
    curl -i \
      -H "Authorization: Bearer $HEALTHARCHIVE_ADMIN_TOKEN" \
      "https://api.healtharchive.ca/api/admin/jobs"
    curl -i \
      -H "Authorization: Bearer $HEALTHARCHIVE_ADMIN_TOKEN" \
      "https://api.healtharchive.ca/metrics"
    ```
    Expect `200 OK`.
- [ ] Decide how operators will call admin APIs:
  - Short‑term: direct `curl`/CLI usage with the token exported in the shell.
  - Longer‑term (optional): a separate admin console (e.g.,
    `https://admin.healtharchive.ca`) that runs in a trusted environment and
    never exposes `HEALTHARCHIVE_ADMIN_TOKEN` to browser JavaScript.
- [ ] If you later add an admin console:
  - Protect it behind SSO, VPN, or other strong authentication.
  - Avoid linking it from the public site navigation.
  - Exclude admin URLs from search indexing (robots.txt and/or `<meta>` tags).

---

## 7. GitHub Actions & branch protection TODOs

Continuous integration is wired via workflow files in each repo, but it only
becomes effective once you commit/push them and (optionally) protect branches.

### 6.1. Enable and verify GitHub Actions

For each repo (`healtharchive-backend` and `healtharchive-frontend`):

1. Ensure the workflow files are present (already true in this repo) and
   enabled in the GitHub UI:

   - Navigate to the repository on https://github.com.
   - Click the **Actions** tab.
   - If GitHub shows a banner like “Workflows are disabled for this fork,”
     click **Enable workflows**.

2. Push a test commit or re‑run the latest workflow to verify that a run is
   triggered for branch `main` and for pull requests:

   - Backend CI should:
     - Install deps via `pip install -e ".[dev]"`.
     - Run `pytest -q`.
   - Frontend CI should:
     - Install deps via `npm ci`.
     - Run `npm run lint` and `npm test`.

### 6.2. Configure branch protection (optional but recommended)

To prevent merging changes that break tests or linting:

1. For each GitHub repo, open the repository page and go to
   **Settings → Branches**.
2. Under **Branch protection rules**, click **Add rule** (or edit an existing
   rule) and set:

   - **Branch name pattern**: `main`
   - Enable **Require a pull request before merging** (tune review settings as
     you prefer).
   - Enable **Require status checks to pass before merging** and select the CI
     workflows:
     - In the backend repo, select the check corresponding to
       `.github/workflows/backend-ci.yml` (e.g., `Backend CI`).
     - In the frontend repo, select the check corresponding to
       `.github/workflows/frontend-ci.yml` (e.g., `Frontend CI`).
   - Optionally enable **Include administrators** so even admin users must
     wait for green CI.

3. Click **Create** or **Save changes** to persist the rule.

After this, any PR targeting `main` will need green CI checks before it can be
merged, ensuring that:

- Backend changes don’t break the pytest suite.
- Frontend changes don’t break linting or Vitest tests.
