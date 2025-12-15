# Monitoring, uptime, and CI checklist

This file pulls together the **ongoing operations** aspects of the project:

- Uptime and health monitoring.
- Metrics and alerting.
- CI enforcement and branch protection.

It is meant to complement:

- `../deployment/hosting-and-live-server-to-dos.md`
- `../deployment/staging-rollout-checklist.md`
- `../deployment/production-rollout-checklist.md`

---

## 1. Uptime and health checks

### 1.1 Backend health endpoint

Primary health endpoint:

- `GET https://api.healtharchive.ca/api/health`
- `HEAD https://api.healtharchive.ca/api/health` (supported; some uptime tools use `HEAD`)

Some uptime providers issue `HEAD` requests by default. The backend supports
`HEAD /api/health` so monitors may use either method.

Expected behavior:

- HTTP 200
- JSON body like:

  ```json
  {
    "status": "ok",
    "checks": {
      "db": "ok",
      "jobs": {
        "queued": 0,
        "indexed": 5,
        "failed": 0
      },
      "snapshots": {
        "total": 123
      }
    }
  }
  ```

Suggested uptime monitor:

- Configure an external service (UptimeRobot, healthchecks.io, your cloud
  provider) to poll:
  - `https://api.healtharchive.ca/api/health`
- If you later add a separate staging API, also poll:
  - `https://api-staging.healtharchive.ca/api/health`
- Alert on:
  - 5xx responses.
  - Timeouts.
  - Repeated failures over a short window.

### 1.2 Frontend + integration check

To verify the frontend **and** backend integration:

- `GET https://healtharchive.ca/archive`

Expected behavior:

- HTTP 200.
- Page renders with:
  - Filters header showing `Filters (live API)` when backend is up.
  - Real search results when snapshots exist.

Suggested uptime monitor:

- Configure a separate check that:
  - Downloads `https://healtharchive.ca/archive`.
  - Optionally asserts presence of a known string in the body (e.g.
    “HealthArchive.ca” or “Browse & search demo snapshots”).

---

## 2. Metrics and alerting

### 2.1 Metrics endpoint

Metrics are exposed at:

- `GET https://api.healtharchive.ca/metrics`
- If you later add a separate staging API:
  - `GET https://api-staging.healtharchive.ca/metrics`

This endpoint is protected by `HEALTHARCHIVE_ADMIN_TOKEN`. In Prometheus or a
similar system, you will typically:

- Store the token in a secure place (e.g. Prometheus config / secret).
- Pass it via `Authorization: Bearer <token>` or `X-Admin-Token` header.

### 2.2 Key metrics

The metrics endpoint exposes, among others:

- Job counts:

  ```text
  healtharchive_jobs_total{status="queued"} 1
  healtharchive_jobs_total{status="indexed"} 5
  healtharchive_jobs_total{status="failed"} 0
  ```

- Cleanup status:

  ```text
  healtharchive_jobs_cleanup_status_total{cleanup_status="none"} 10
  healtharchive_jobs_cleanup_status_total{cleanup_status="temp_cleaned"} 3
  ```

- Snapshot counts:

  ```text
  healtharchive_snapshots_total 123
  healtharchive_snapshots_total{source="hc"} 80
  healtharchive_snapshots_total{source="phac"} 43
  ```

- Page‑level crawl metrics:

  ```text
  healtharchive_jobs_pages_crawled_total 45678
  healtharchive_jobs_pages_crawled_total{source="hc"} 30000
  healtharchive_jobs_pages_failed_total 120
  healtharchive_jobs_pages_failed_total{source="hc"} 30
  ```

- Search metrics (per-process; reset on restart):

  ```text
  healtharchive_search_requests_total 123
  healtharchive_search_errors_total 0
  healtharchive_search_duration_seconds_bucket{le="0.3"} 100
  healtharchive_search_mode_total{mode="relevance_fts"} 80
  ```

### 2.3 Example alert ideas (Prometheus‑style)

These are **examples**, not full rules, but can guide what you set up:

- High job failure rate:

  - Alert if `healtharchive_jobs_total{status="failed"}` jumps unexpectedly
    over a sliding window.

- No new snapshots over time:

  - Alert if `increase(healtharchive_snapshots_total[24h]) == 0` while jobs
    are being created, indicating indexing or crawl issues.

- Cleanup not happening:

  - Alert if `healtharchive_jobs_cleanup_status_total{cleanup_status="none"}`
    grows without bound while `temp_cleaned` remains flat.

Tune these based on actual volumes and acceptable thresholds.

---

## 3. CI and branch protection

### 3.1 GitHub Actions workflows

Workflows live at:

- Backend: `.github/workflows/backend-ci.yml`
- Frontend: `.github/workflows/frontend-ci.yml`

Each should:

- Backend:
  - Install deps via `pip install -e ".[dev]"`.
  - Run `pytest -q`.
  - Optionally run security tooling (e.g. `bandit`).
- Frontend:
  - Install deps via `npm ci`.
  - Run `npm run lint`.
  - Run `npm test`.
  - Optionally run `npm audit --audit-level=high`.

Checklist:

- [ ] Ensure workflows are enabled in GitHub:
  - Open the repo on GitHub.
  - Go to **Actions**.
  - If you see “Workflows are disabled for this fork”, click **Enable**.
- [ ] Verify that pushing to `main` or opening a PR triggers the workflows.

### 3.2 Branch protection on `main`

In each repo:

1. Go to **Settings → Branches → Branch protection rules**.
2. Add or edit a rule for the `main` branch:
   - Enable **Require a pull request before merging**.
   - Enable **Require status checks to pass before merging**.
   - Select:
     - The backend CI workflow for the backend repo.
     - The frontend CI workflow for the frontend repo.
   - Optionally:
     - Enable **Include administrators**.

This ensures:

- No changes reach `main` without passing tests and linting.
- Every `main` deploy (to staging/production) is backed by green CI.

---

## 4. Periodic operations review

On a regular cadence (e.g. monthly or quarterly), review:

- Uptime logs:
  - Are there recurring outages at specific times?
- Metrics:
  - Are job failures spiking?
  - Are snapshots growing at the expected rate?
  - Is cleanup keeping up with new jobs?
- CI:
  - Are workflows still running on all relevant branches?
  - Do new checks or tooling need to be added?

Recording a short “ops state” note alongside these reviews will make future
debugging and capacity planning much easier.
