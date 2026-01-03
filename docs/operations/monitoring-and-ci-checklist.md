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

## 0. Implementation steps (CI + external monitoring)

This section is a **practical, sequential** setup plan for enforcing CI and
configuring external monitoring in the real world (GitHub + UptimeRobot, etc.).

Important: most of this is **not** “code you deploy” — it is configuration in:

- GitHub repository settings (branch protection)
- Your monitoring provider (UptimeRobot, Healthchecks, etc.)

### Step 0 — Baseline audit + decisions (operator)

Objective: avoid duplicate monitors, avoid alert noise, and avoid “unknown
settings drift”.

1. Inventory current external monitors (UptimeRobot, etc.):
   - Monitor name
   - URL
   - Interval + timeout
   - Alert contacts/routes
   - Any keyword/body assertions
2. Decide alert routing:
   - Which alerts should page you vs. just email (recommended: only “site down”
     pages; everything else emails).
3. Decide the `main` branch policy:
   - **Solo-fast (recommended for this project right now): direct pushes to `main`**.
     - CI still runs on every push to `main`.
     - Deploys are gated by “green main” + VPS verification steps (below).
   - Future (when there are multiple committers): PR-only merges into `main` with required
     status checks + code owners (track in `../roadmaps/future-roadmap.md`).

Verification:

- You can point to a quick note (even in a personal doc) listing current
  monitors + what each covers.

### Step 1 — Verify CI runs on `main` pushes (operator)

Objective: ensure CI runs on pushes to `main` so you can treat “green main” as the deploy gate.

1. Confirm GitHub Actions workflows are enabled:
   - Repo → Actions → ensure workflows are enabled (not disabled by org/fork policy).
2. Push a trivial commit to `main` (e.g. a doc tweak).
3. Confirm the workflow runs and passes on that commit.

Verification:

- GitHub Actions shows the backend CI workflow completing successfully on `main`.

### Step 1b — End-to-end smoke checks (CI)

Objective: catch regressions where the apps “build” but user‑critical paths fail at runtime.

What the smoke does:

- Starts the backend locally (uvicorn) with a tiny seeded SQLite + WARC dataset.
- Builds and starts the frontend locally (`next start`) pointing at that backend.
- Runs `healtharchive-backend/scripts/verify_public_surface.py` against:
  - Frontend: `/archive`, `/fr/archive`, `/snapshot/{id}`, `/fr/snapshot/{id}`, and other key pages
  - API: `/api/health`, `/api/sources`, `/api/search`, `/api/snapshot/{id}`, `/api/usage`, `/api/exports`, `/api/changes`
- Replay (pywb) is intentionally skipped in CI (`--skip-replay`).
- The verifier includes minimal “not just 200” assertions:
  - `/archive` pages must include a stable `<title>` marker.
  - `/snapshot/{id}` pages must include the snapshot title returned by the API.

Where it runs:

- Backend repo CI: `.github/workflows/backend-ci.yml` job `e2e-smoke`
  - Tests backend changes against latest frontend `main`.
- Frontend repo CI: `.github/workflows/frontend-ci.yml` job `e2e-smoke`
  - Tests frontend changes against latest backend `main`.
- If cross-repo checkout fails (private repo), set a repo secret:
  - `HEALTHARCHIVE_CI_READ_TOKEN` (PAT with read access)

Local reproduction (from the mono‑repo workspace where the repos are siblings):

```bash
cd healtharchive-frontend && npm ci
cd ../healtharchive-backend
make venv
./scripts/ci-e2e-smoke.sh --frontend-dir ../healtharchive-frontend
```

On failure, the script prints the tail of the backend/frontend logs that it writes under:

- `healtharchive-backend/.tmp/ci-e2e-smoke/`

### Step 2 — Solo-fast deploy gate (operator; recommended)

Objective: prevent broken deploys by only deploying when `main` is green.

Workflow (recommended):

0. Local guardrails (recommended while branch protections are relaxed):
   - Run checks before you push:
     - From the mono-repo root: `make check`
     - Or per-repo: `healtharchive-backend: make check`, `healtharchive-frontend: npm run check`
   - Optional but recommended: install pre-push hooks so you can't forget:
     - Backend: `healtharchive-backend/scripts/install-pre-push-hook.sh`
     - Frontend: `healtharchive-frontend/scripts/install-pre-push-hook.sh`
1. Push to `main`.
2. Wait for GitHub Actions to go green on that commit.
3. Deploy on the VPS:
   - Recommended (one command): `./scripts/vps-deploy.sh --apply --baseline-mode live`
     - Includes baseline drift + public-surface verify by default.
   - If you use a local alias like `dodeploy`, ensure you still run:
     - `./scripts/check_baseline_drift.py --mode live`
     - `./scripts/verify_public_surface.py`

Verification:

- The VPS deploy completes and both verification scripts pass.

Future (tighten later):

- When there are multiple committers or when you want stricter enforcement, switch to PR-only merges
  and require the backend/frontend checks in branch protection (track in `../roadmaps/future-roadmap.md`).

### Step 3 — External uptime monitoring (operator; UptimeRobot settings)

Objective: catch real, user-visible failures with minimal noise.

Recommended minimal monitor set (HTTP(s) checks):

1. **API health**
   - URL: `https://api.healtharchive.ca/api/health`
   - Expected: HTTP 200
   - Interval: 1–5 minutes
   - Note: backend supports `HEAD /api/health` for providers that default to `HEAD`.
2. **Frontend integration**
   - URL: `https://www.healtharchive.ca/archive`
   - Expected: HTTP 200
   - Interval: 5 minutes
   - Optional: keyword assertion (stable string that should always appear).
3. **Replay base URL** (optional but recommended if you rely on replay)
   - URL: `https://replay.healtharchive.ca/`
   - Expected: HTTP 200
   - Interval: 5–10 minutes

Optional, higher-signal replay monitoring (recommended later):

- After annual jobs exist and are replay-indexed, add 1–3 “known-good replay URL”
  monitors (one per source or one total) pointing at a stable capture inside a
  `job-<id>` collection. Update them annually.

Verification:

- Optional pre-flight from the VPS (or any machine with internet + `curl`):
  - `./scripts/smoke-external-monitors.sh`
- All monitors show “Up”.
- Alerting routes work (optional test: intentionally break a monitor briefly).

---

### Step 4 — Timer ran monitoring (Healthchecks-style; optional but recommended)

Objective: get alerted if systemd timers stop running (even when the site is up).

This is intentionally optional: you already have high-value uptime checks in
Step 3, but "timer ran" alerts are useful for catching silent failures (timer
disabled, unit failing, disk low refusal, etc.).

Recommended checks to monitor:

- Baseline drift check (weekly):
  - `healtharchive-baseline-drift-check.timer`
  - Ping variable: `HEALTHARCHIVE_HC_PING_BASELINE_DRIFT`
- Public surface verification (daily):
  - `healtharchive-public-surface-verify.timer`
  - Ping variable: `HEALTHARCHIVE_HC_PING_PUBLIC_VERIFY`
- Replay reconcile (daily):
  - `healtharchive-replay-reconcile.timer`
  - Ping variable: `HEALTHARCHIVE_HC_PING_REPLAY_RECONCILE`
- Change tracking (daily):
  - `healtharchive-change-tracking.timer`
  - Ping variable: `HEALTHARCHIVE_HC_PING_CHANGE_TRACKING`
- Annual scheduler (yearly):
  - `healtharchive-schedule-annual.timer`
  - Ping variable: `HEALTHARCHIVE_HC_PING_SCHEDULE_ANNUAL`
- Annual search verify (daily, idempotent once per year):
  - `healtharchive-annual-search-verify.timer`
  - Ping variable: `HEALTHARCHIVE_HC_PING_ANNUAL_SEARCH_VERIFY`
- Coverage guardrails (daily):
  - `healtharchive-coverage-guardrails.timer`
  - Ping variable: `HEALTHARCHIVE_HC_PING_COVERAGE_GUARDRAILS`
- Replay smoke tests (daily):
  - `healtharchive-replay-smoke.timer`
  - Ping variable: `HEALTHARCHIVE_HC_PING_REPLAY_SMOKE`
- Cleanup automation (weekly):
  - `healtharchive-cleanup-automation.timer`
  - Ping variable: `HEALTHARCHIVE_HC_PING_CLEANUP_AUTOMATION`

Note: avoid pinging high-frequency timers (e.g., crawl metrics, crawl auto-recover) to reduce noise.

Implementation approach (VPS):

1. Create a check in your Healthchecks provider for each timer you care about.
2. Store ping URLs only on the VPS in a root-owned env file:
   - `/etc/healtharchive/healthchecks.env` (mode 0600, root:root)
   - Note: this file may be shared across multiple automations; it is OK to keep both:
     - legacy `HC_*` variables (DB backup + disk check)
     - newer `HEALTHARCHIVE_HC_PING_*` variables (systemd unit templates)
3. Ensure the installed systemd units source that env file:
   - `EnvironmentFile=-/etc/healtharchive/healthchecks.env`
4. Ensure the unit uses the wrapper so ping URLs never appear in unit files:
   - `/opt/healtharchive-backend/scripts/systemd-healthchecks-wrapper.sh`

Safety posture:

- Pinging is best-effort; ping failures do not fail jobs.
- Removing `/etc/healtharchive/healthchecks.env` disables pings immediately.

Verification (VPS):

- Add a temporary ping URL for one service, then run:
  - `sudo systemctl start healtharchive-replay-reconcile-dry-run.service`
  - Confirm the check receives a ping.

---

### Step 5 — Automated post-campaign search verification capture (optional)

Objective: once the annual campaign becomes search-ready, automatically capture
golden-query `/api/search` JSON into a year-tagged directory for later diffing
and audits.

What gets captured (recommended minimal set):

- `annual-status.json` and a human-readable `annual-status.txt`
- `meta.txt` (capture metadata)
- `<query>.pages.json` + `<query>.snapshots.json` for your golden query list

Implementation approach (VPS, systemd):

- This repo provides an optional daily timer that is idempotent:
  - If the campaign is not ready, it exits 0 (no alert noise).
  - If artifacts already exist for the current year/run-id, it exits 0.

Install and enable:

1. Copy templates onto the VPS (see `../deployment/systemd/README.md`):
   - `healtharchive-annual-search-verify.service`
   - `healtharchive-annual-search-verify.timer`
2. Reload systemd:
   - `sudo systemctl daemon-reload`
3. Enable the timer:
   - `sudo systemctl enable --now healtharchive-annual-search-verify.timer`

Artifacts:

- Default location: `/srv/healtharchive/ops/search-eval/<year>/final/`
- To force re-run for a year: delete that directory and re-run the service.

Verification (VPS):

- Force-run once:
  - `sudo systemctl start healtharchive-annual-search-verify.service`
- Confirm it either:
  - exits 0 quickly (not ready), or
  - creates artifacts under `/srv/healtharchive/ops/search-eval/<year>/final/`.

---

### Step 6 — Optional GitHub-driven deploys (CD) (infrastructure project)

Objective: reduce deploy mistakes without expanding the production attack
surface.

Recommended posture for this project (single VPS, no staging backend):

- Keep deployments **manual** on the VPS.
- Use the deploy helper script:
  - `scripts/vps-deploy.sh` (dry-run default; `--apply` to deploy)

Rationale:

- Avoids storing production access secrets in GitHub.
- Avoids granting passwordless sudo/SSH access to GitHub Actions.
- Keeps the operational path “boring” and easy to reason about.

Verification (VPS):

- Dry-run: `cd /opt/healtharchive-backend && ./scripts/vps-deploy.sh`
- Apply: `cd /opt/healtharchive-backend && ./scripts/vps-deploy.sh --apply`

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

### 1.3 Replay uptime check (optional but recommended if replay is in use)

To ensure replay is reachable:

- `GET https://replay.healtharchive.ca/`

If you want a higher-signal check (recommended once you have stable annual jobs):

- Monitor a known-good replay URL inside a specific `job-<id>` collection:
  - `https://replay.healtharchive.ca/job-<id>/<original_url>`
  - Choose an original URL that is stable and low-cost to serve.

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
  healtharchive_search_mode_total{mode="relevance_fallback"} 25
  healtharchive_search_mode_total{mode="relevance_fuzzy"} 5
  healtharchive_search_mode_total{mode="boolean"} 2
  healtharchive_search_mode_total{mode="url"} 3
  healtharchive_search_mode_total{mode="newest"} 8
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
  - Run `make check`.
- Frontend:
  - Install deps via `npm ci`.
  - Run `npm run check`.
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
   - Enable **Include administrators** (recommended best practice).
   - Enable **Require review from Code Owners** (recommended; requires `.github/CODEOWNERS`).

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
