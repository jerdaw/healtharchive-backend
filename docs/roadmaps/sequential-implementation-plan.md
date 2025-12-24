# HealthArchive sequential implementation plan (current)

This is the **current, step-by-step execution plan** for HealthArchive across:

- backend (`healtharchive-backend`)
- frontend (`healtharchive-frontend`)
- production operations (VPS + Vercel + GitHub settings)

It is intentionally **sequential**: complete each phase before starting the next.

Notes:

- This is not the historical “6-phase upgrade roadmap”. That historical doc lives in
  `healtharchive-6-phase-upgrade-2025.md`.
- This plan is written against **current implementation reality**. Where code already
  exists, phases focus on configuration + verification rather than new development.
- Keep `docs/operations/healtharchive-ops-roadmap.md` short; it should reference this
  plan rather than duplicating it.

## Phase 0 — Baseline inventory (one-time; repeat after major changes)

**Current state:** configuration-driven; code supports most features but production state can drift.

Best-practice approach: treat baseline as **policy (in git)** + **observed snapshots (generated on VPS)** + **drift checks**.

Implementation helpers (repo):

- Desired-state policy (in git): `docs/operations/production-baseline-policy.toml`
- Drift check + snapshot writer (VPS): `scripts/check_baseline_drift.py`
- Snapshot generator (optional): `scripts/baseline_snapshot.py`
- Background timer (optional): `docs/deployment/systemd/healtharchive-baseline-drift-check.*`
- Ops doc: `docs/operations/baseline-drift.md`

1. Record the current values (or “unset”) for:
   - Backend: `HEALTHARCHIVE_ENV`, `HEALTHARCHIVE_DATABASE_URL`, `HEALTHARCHIVE_ARCHIVE_ROOT`,
     `HEALTHARCHIVE_ADMIN_TOKEN`, `HEALTHARCHIVE_CORS_ORIGINS`, `HEALTHARCHIVE_LOG_LEVEL`,
     `HA_SEARCH_RANKING_VERSION`, `HA_PAGES_FASTPATH`,
     `HEALTHARCHIVE_REPLAY_BASE_URL`, `HEALTHARCHIVE_REPLAY_PREVIEW_DIR`,
     `HEALTHARCHIVE_USAGE_METRICS_ENABLED`, `HEALTHARCHIVE_USAGE_METRICS_WINDOW_DAYS`,
     `HEALTHARCHIVE_CHANGE_TRACKING_ENABLED`,
     `HEALTHARCHIVE_EXPORTS_ENABLED`, `HEALTHARCHIVE_EXPORTS_DEFAULT_LIMIT`,
     `HEALTHARCHIVE_EXPORTS_MAX_LIMIT`, `HEALTHARCHIVE_PUBLIC_SITE_URL`.
   - Frontend (Vercel): `NEXT_PUBLIC_API_BASE_URL`,
     `NEXT_PUBLIC_SHOW_API_HEALTH_BANNER`, `NEXT_PUBLIC_LOG_API_HEALTH_FAILURE`,
     `NEXT_PUBLIC_SHOW_API_BASE_HINT`.
2. Capture the current “live surface” URLs you consider canonical:
   - `https://healtharchive.ca`, `https://www.healtharchive.ca`
   - `https://api.healtharchive.ca`
   - `https://replay.healtharchive.ca` (if replay is enabled)
3. Confirm what environments you actively support:
   - “single production backend” vs “staging backend exists”.
4. On the production VPS, generate an observed snapshot and drift report:

   ```bash
   cd /opt/healtharchive-backend
   ./scripts/check_baseline_drift.py --mode live
   ```

   This writes:

   - `/srv/healtharchive/ops/baseline/observed-<timestamp>.json`
   - `/srv/healtharchive/ops/baseline/drift-report-<timestamp>.txt`
   - and updates `observed-latest.json` + `drift-report-latest.txt`.

5. If drift is detected, fix production or update policy **only if the change is intentional**.

**Exit criteria:** `./scripts/check_baseline_drift.py --mode live` reports PASS and artifacts exist under `/srv/healtharchive/ops/baseline/`.

## Phase 1 — Security and access control (must be correct before scaling usage)

**Current state:** implemented in code; must be correctly configured in production.

Implementation helpers (repo):

- Security/admin verification script: `scripts/verify-security-and-admin.sh`

1. Configure `HEALTHARCHIVE_ENV=production` and set a strong `HEALTHARCHIVE_ADMIN_TOKEN`.
   - Code reference: `ha_backend/api/deps.py` fails closed in prod/staging if token is missing.
2. Verify:
   - `/metrics` requires auth and cannot be scraped without the token.
   - `/api/admin/*` routes require auth.
3. Ensure secrets posture:
   - tokens/credentials are in server env files / secret manager only, never committed.
4. Confirm HTTPS posture for the API:
   - HTTP→HTTPS redirect
   - HSTS enabled for `api.healtharchive.ca`
   - (documented checklist) `docs/deployment/hosting-and-live-server-to-dos.md`

HSTS implementation note (Caddy):

- Prefer setting HSTS at the reverse proxy (Caddy), not in the FastAPI app.
- Example (adjust to your policy; `includeSubDomains` is only safe if all subdomains are HTTPS):

  ```caddyfile
  api.healtharchive.ca {
    header Strict-Transport-Security "max-age=31536000; includeSubDomains"
    reverse_proxy 127.0.0.1:8001
  }
  ```

Recommended verification (production):

```bash
cd /opt/healtharchive-backend
set -a; source /etc/healtharchive/backend.env; set +a
./scripts/verify-security-and-admin.sh --api-base https://api.healtharchive.ca --require-hsts
```

**Exit criteria:** admin endpoints are closed to the public, secrets are stored safely, API HTTPS posture is verified.

## Phase 2 — CI enforcement and merge discipline (prevents regressions)

**Current state:** workflow files exist; enforcement is mostly GitHub settings.

Implementation helpers (repo):

- Backend CI workflow: `healtharchive-backend/.github/workflows/backend-ci.yml`
- Frontend CI workflow: `healtharchive-frontend/.github/workflows/frontend-ci.yml`
- GitHub branch protection walkthrough: `docs/operations/monitoring-and-ci-checklist.md`

1. Ensure Actions workflows are enabled for both repos:
   - `healtharchive-backend/.github/workflows/backend-ci.yml`
   - `healtharchive-frontend/.github/workflows/frontend-ci.yml`
2. Solo-fast mode (recommended while you’re the only committer):
   - allow direct pushes to `main`
   - treat “green main” as the deploy gate
   - add local guardrails so you don’t accidentally push broken `main`:
     - from the mono-repo root: `make check`
     - optional but recommended:
       - `healtharchive-backend/scripts/install-pre-push-hook.sh`
       - `healtharchive-frontend/scripts/install-pre-push-hook.sh`
3. Deploy gate (production):
   - recommended (one command): `./scripts/vps-deploy.sh --apply --baseline-mode live`
     - includes baseline drift + public-surface verify by default
   - if you use a local alias like `dodeploy`, ensure you still run:
     - `./scripts/check_baseline_drift.py --mode live`
     - `./scripts/verify_public_surface.py`
4. TODO (tighten later when there are multiple committers):
   - require PR
   - require status checks
   - include administrators + require code owner review

**Exit criteria:** deploys only happen from a green `main`, and post-deploy verification is routine.

## Phase 3 — External monitoring (site-up signal + low-noise alerts)

**Current state:** guidance + helper scripts exist; requires operator setup.

Implementation helpers (repo):

- Monitor setup walkthrough: `docs/operations/monitoring-and-ci-checklist.md`
- Pre-flight checker: `scripts/smoke-external-monitors.sh`

1. Configure external uptime monitors:
   - `https://api.healtharchive.ca/api/health`
   - `https://www.healtharchive.ca/archive` (integration check)
   - `https://replay.healtharchive.ca/` (only if replay is relied upon)
2. Use the local helper to validate from a laptop/VPS:
   - `healtharchive-backend/scripts/smoke-external-monitors.sh`
3. Decide alert routing (page vs email) and document the decision.

**Exit criteria:** monitors exist, are green, and alert routing is confirmed.

## Phase 4 — Environment wiring, CORS posture, and “preview” policy

**Current state:** backend CORS is strict-by-default; frontend can fall back to demo mode.

Implementation helpers (repo):

- Canonical wiring doc: `docs/deployment/environments-and-configuration.md`
- Production drift + wiring validation (includes CORS header checks): `scripts/check_baseline_drift.py --mode live`

1. Set canonical API base on Vercel:
   - `NEXT_PUBLIC_API_BASE_URL=https://api.healtharchive.ca`
2. Decide branch-preview posture:
    - Option A (recommended): strict CORS allowlist; branch preview URLs fall back to demo.
   - Option B: allow additional preview origins (higher risk; more surface).
3. Implement the chosen posture:
   - set `HEALTHARCHIVE_CORS_ORIGINS` explicitly in production env.
4. Verify:
   - production site uses live API
   - Vercel project domain (`healtharchive.vercel.app`) behavior matches the chosen posture
   - branch previews behave as expected (demo fallback or live API)

**Exit criteria:** there are no “surprise demo mode” deployments; behavior is explicit and repeatable.

## Phase 5 — End-to-end baseline usability (frontend ↔ backend)

**Current state:** frontend already calls the backend directly and has offline fallbacks.

Implementation helpers (repo):

- Public-surface verifier (frontend + public API + replay + usage): `scripts/verify_public_surface.py`

1. Verify core public routes against the live backend:
   - `/archive` search + pagination
   - `/archive/browse-by-source`
   - `/snapshot/[id]` metadata loads from `/api/snapshot/{id}`
   - `/report` submits to the backend via the frontend forwarder
2. Confirm expected behavior when the backend is down:
   - clear fallback notices
   - demo dataset is used

**Exit criteria:** `scripts/verify_public_surface.py` passes and degradations are clear and safe.

## Phase 6 — Replay service (full-fidelity browsing) + snapshot viewer fidelity

**Current state:** backend can emit `browseUrl`; frontend prefers replay when present.

1. Deploy/maintain replay service (pywb) per:
   - `docs/deployment/replay-service-pywb.md`
2. Enable replay URL generation on the backend:
   - set `HEALTHARCHIVE_REPLAY_BASE_URL`
   - confirm API responses include `browseUrl` where expected
3. Index at least one job into replay:
   - `ha-backend replay-index-job --id <JOB_ID>`
   - validate a known-good replay URL loads
4. Confirm frontend behavior:
   - `/snapshot/[id]` embeds replay (`browseUrl`) when available, otherwise raw HTML fallback.
5. Confirm cleanup posture:
   - do not delete WARCs needed for replay; rely on the cleanup safety checks when replay is enabled.

**Exit criteria:** replay-backed snapshots work end-to-end for at least one real job and are safe from accidental cleanup.

## Phase 7 — Usage metrics and public reporting pages (`/status`, `/impact`)

**Current state:** backend has usage metrics code + endpoint; frontend already renders enabled/disabled states.

1. Ensure the DB schema/migrations include usage tables and the feature is enabled:
   - set `HEALTHARCHIVE_USAGE_METRICS_ENABLED=1` (or decide to keep it off)
2. Verify event recording works (best-effort):
   - search requests
   - snapshot detail views
   - raw snapshot views
   - report submissions
3. Verify `/api/usage` returns `enabled=true` and realistic windowed totals.
4. Verify frontend pages render real numbers:
   - `/status`
   - `/impact`

**Exit criteria:** public reporting surfaces reflect real aggregate counts (or are explicitly disabled by policy).

## Phase 8 — Research exports, dataset releases, and the Researchers page TODO

**Current state:** export endpoints and the `/exports` + `/researchers` pages exist; verify script covers exports and pages.

1. Confirm exports are enabled and stable:
   - `HEALTHARCHIVE_EXPORTS_ENABLED=1`
   - `/api/exports`, `/api/exports/snapshots`, `/api/exports/changes`
   - Recommended verifier (production):
     - `./scripts/verify_public_surface.py` (checks exports manifest + export HEADs)
2. Confirm the public data dictionary + downloadables are correct:
   - `healtharchive-frontend/public/exports/healtharchive-data-dictionary.md`
   - `healtharchive-frontend/public/exports/healtharchive-data-dictionary.fr.md` (alpha)
3. Decide and document the dataset release process:
   - cadence (quarterly is the current intent)
   - where releases live (GitHub Releases)
   - checksum policy (`SHA256SUMS`)
   - validation step (in ops cadence)
   - Implementation helpers (repo):
     - `docs/operations/dataset-release-runbook.md`
     - `docs/operations/export-integrity-contract.md`
4. Implement the Researchers page workflow (remove TODO / avoid “planned” contradictions):
   - state the **current** dataset release cadence (or explicitly mark it “not yet stable”)
   - document the bulk export request workflow (what info to send, constraints, expected response time)
   - keep English canonical and ship French in the same change
   - reference: `healtharchive-frontend/docs/development/bilingual-dev-guide.md`

**Exit criteria:** `/researchers` accurately describes how researchers get data today (including datasets + bulk requests), and no “planned” copy contradicts reality.

## Phase 9 — Coverage expansion (CIHR legacy import and new sources)

**Current state:** CIHR import is documented; helper script exists to normalize/register/index on the VPS.

1. Complete CIHR legacy import:
   - normalize permissions
   - register job dir for CIHR
   - index job
   - reference: `docs/operations/legacy-crawl-imports.md`
   - Optional helper (VPS): `scripts/import-legacy-crawl.sh`
2. Verify:
   - CIHR appears in `/api/sources`
   - CIHR is searchable in `/api/search` and visible in the frontend
   - Single command (production):
     - `./scripts/verify_public_surface.py --require-source cihr`
3. Only after CIHR is stable, consider additional source expansion (annual campaign updates).

**Exit criteria:** CIHR is a first-class source in search, browse, and snapshot detail.

## Phase 10 — Search quality loop + ranking rollout decisions

**Current state:** evaluation docs + capture/diff scripts exist; ranking v2 rollout is documented (and currently recommended).

1. Establish a repeatable evaluation workflow:
   - golden queries + expectations live in:
     - `docs/operations/search-golden-queries.md`
     - `docs/operations/search-quality.md`
   - capture/diff scripts under `healtharchive-backend/scripts/`:
     - `scripts/search-eval-run.sh` (one-command v1+v2 capture + diff)
2. Run evaluations periodically and record outcomes (public-safe).
3. Only after you have signal:
   - decide whether to keep `HA_SEARCH_RANKING_VERSION=v1` or switch to `v2`
   - follow `docs/deployment/search-rollout.md` if switching

**Exit criteria:** search changes are evaluated against a stable query set and rollout/rollback is routine.

## Phase 11 — Automation and sustainability (timers, reconcile loops, retention)

**Current state:** systemd templates and ops docs exist; enabling automation is an operator action on the VPS.

Implementation helpers (repo):

- Systemd templates + enablement steps: `docs/deployment/systemd/README.md`
- Install/update systemd templates (VPS): `scripts/vps-install-systemd-units.sh`
- Bootstrap `/srv/healtharchive/ops/*` dirs (VPS): `scripts/vps-bootstrap-ops-dirs.sh`
- Automation verifier (VPS): `scripts/verify_ops_automation.sh`
- Ops cadence docs/templates:
  - restore tests: `docs/operations/restore-test-procedure.md`, `docs/operations/restore-test-log-template.md`
  - dataset releases: `docs/operations/dataset-release-runbook.md`
  - adoption signals: `docs/operations/adoption-signals-log-template.md`
  - monitoring/timer pings: `docs/operations/monitoring-and-ci-checklist.md`
- Retention constraints (replay safety): `docs/operations/growth-constraints.md`

### 11.1 Bootstrap ops directories (one-time; VPS)

If not already created, bootstrap the ops artifact directories so timers and scripts can write artifacts:

```bash
cd /opt/healtharchive-backend
sudo ./scripts/vps-bootstrap-ops-dirs.sh
```

### 11.2 Install/update systemd templates (VPS)

```bash
cd /opt/healtharchive-backend
sudo ./scripts/vps-install-systemd-units.sh --apply --restart-worker
```

### 11.3 Enable baseline drift timer (recommended; VPS)

Baseline drift checks are low-risk and provide early warning on misconfig (env, perms, unit enablement).

```bash
sudo install -m 0644 -o root -g root /dev/null /etc/healtharchive/baseline-drift-enabled
sudo systemctl enable --now healtharchive-baseline-drift-check.timer
```

### 11.4 Decide optional automation timers (VPS)

Keep it boring: enable only what you’re ready to operate. Validate dry-run services before enabling timers:

- **Annual scheduling (Jan 01 UTC)**
  - Validate: `sudo systemctl start healtharchive-schedule-annual-dry-run.service`
  - Enable: create `/etc/healtharchive/automation-enabled`, then `enable --now healtharchive-schedule-annual.timer`
- **Replay reconcile (daily; optional)**
  - Validate: `sudo systemctl start healtharchive-replay-reconcile-dry-run.service`
  - Enable: create `/etc/healtharchive/replay-automation-enabled`, then `enable --now healtharchive-replay-reconcile.timer`
- **Change tracking (daily; optional)**
  - Validate: `sudo systemctl start healtharchive-change-tracking-dry-run.service`
  - Enable: create `/etc/healtharchive/change-tracking-enabled`, then `enable --now healtharchive-change-tracking.timer`
- **Annual search verification capture (daily timer; captures once per year; optional)**
  - Enable: `sudo systemctl enable --now healtharchive-annual-search-verify.timer`

Full enablement and rollback commands live in: `docs/deployment/systemd/README.md`

### 11.5 Optional: timer-ran monitoring (Healthchecks-style; VPS)

If you want “silent failure” alerts (timer disabled, unit failing, disk-full refusal, etc.), follow:

- `docs/operations/monitoring-and-ci-checklist.md` (Step 4)

This uses a root-owned `/etc/healtharchive/healthchecks.env` plus
`scripts/systemd-healthchecks-wrapper.sh` so ping URLs are not committed.

### 11.6 Operationalize the quarterly ops cadence (VPS + GitHub Releases)

- **Quarterly restore test:** follow `docs/operations/restore-test-procedure.md` and write a public-safe log entry under `/srv/healtharchive/ops/restore-tests/`.
- **Quarterly dataset checksum verification:** verify release assets with `sha256sum -c SHA256SUMS` (see `docs/operations/dataset-release-runbook.md`).
- **Quarterly adoption signals entry:** write a public-safe entry under `/srv/healtharchive/ops/adoption/` using `docs/operations/adoption-signals-log-template.md`.

### 11.7 Retention / cleanup posture (VPS)

- If replay is enabled, treat WARC retention as critical state: do not delete WARCs needed for replay.
- Use only “safe cleanup” modes until you have a cold storage replay plan:
  - reference: `docs/operations/growth-constraints.md`

### 11.8 Verify Phase 11 (VPS)

```bash
cd /opt/healtharchive-backend
./scripts/verify_ops_automation.sh
./scripts/check_baseline_drift.py --mode live
```

**Exit criteria:** baseline drift timer is enabled, ops artifact dirs exist, worker priority override is installed, and any optional timers are either enabled or explicitly deferred (with monitoring decisions recorded).

## Phase 12 — External validation and outreach (only after the service is credible)

**Current state:** public-facing “credibility” pages exist; the remaining work is mostly IRL, but templates and checklists exist in-repo.

Implementation helpers (repo):

- Partner kit (internal ops guide): `docs/operations/partner-kit.md`
- Outreach email templates: `docs/operations/outreach-templates.md`
- Verifier packet outline: `docs/operations/verification-packet.md`
- Mentions log template: `docs/operations/mentions-log-template.md`
- Citation handout pointer: `docs/operations/citation-handout.md`
- Public pages to keep healthy (frontend):
  - `/changes`, `/digest`
  - `/brief`, `/cite`
  - `/methods`, `/governance`
- Public surface verifier (production): `scripts/verify_public_surface.py` (includes `/api/changes/rss` + partner pages)

### 12.1 Confirm public credibility surfaces are live (production)

```bash
cd /opt/healtharchive-backend
./scripts/verify_public_surface.py
```

### 12.2 Start (and maintain) a public-safe mentions log

- Use `docs/operations/mentions-log-template.md`.
- Store it somewhere you will actually update:
  - recommended (VPS): `/srv/healtharchive/ops/adoption/` (public-safe only), or
  - a separate private notes location (never commit private contact details to git).

### 12.3 Secure 1 distribution partner (IRL)

- Use `docs/operations/outreach-templates.md` and `docs/operations/partner-kit.md`.
- Get explicit permission before naming them publicly.

### 12.4 Secure 1 verifier (IRL)

- Use `docs/operations/verification-packet.md`.
- Get explicit permission before naming them publicly.

### 12.5 Reflect highlights in public impact reporting (when appropriate)

- Add partner highlights and citations only when permissions are clear.
- Keep the story evidence-backed and non-committal (“archive”, “snapshots”, “change tracking”; not “official guidance”).

**Exit criteria:** at least one partner + one verifier are secured (with permission to name), the mentions log discipline exists, and public-facing claims are backed by links/evidence.
