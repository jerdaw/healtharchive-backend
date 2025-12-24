# Ops observability + private usage dashboards (Prometheus/Grafana) — implementation plan

This is a **sequential implementation plan** for adding an operator-only
observability stack (Prometheus + Grafana) and a **private stats surface** for
both **ops health** and **expanded usage aggregates**, with an explicit bias
toward:

- **Low maintenance / low toil**
- **Strong privacy posture** (no per-user tracking)
- **No new public attack surface**
- Reuse of existing backend surfaces (`/metrics`, `/api/admin/**`, `usage_metrics`)

This plan does **not** implement anything by itself.

---

## 0) Executive summary (decisions locked up-front)

### 0.1 What we are building

1) **Private “stats page” = Grafana**

- Grafana dashboards become the operator-only stats surface.
- Prometheus scrapes backend + host + DB metrics.
- Grafana reads from Prometheus (time-series) and Postgres (tables / long-window aggregates).

2) **Expanded usage (private)**

- Continue the project’s existing approach: **daily aggregate counters only**, stored in Postgres (`usage_metrics`).
- Expand the event set over time (still aggregate-only), but **do not** expose every event publicly.
- Grafana reads the full internal dataset directly from Postgres.

3) **Admin/ops UI**

- **Default**: treat Grafana as the “ops UI” (tables + links), and continue to use existing admin endpoints for JSON drilldown.
- **Only if justified by real operator pain**: consider a bespoke admin console later, starting read-only.

### 0.2 Private access model (chosen)

**Use Tailscale for private access; publish Grafana via `tailscale serve`.**

Rationale:

- Keeps the public internet surface unchanged (no new DNS, no new public ports).
- Fits the project’s current production model (Tailscale-only SSH).
- Gives a clean, low-friction operator UX compared to SSH port-forwarding.
- Makes “rarely used” admin access less annoying.

Implementation principle:

- Grafana listens on loopback; Tailscale proxies it to a tailnet-only HTTPS URL.
- Prometheus does not need to be operator-facing; keep its UI loopback-only.

### 0.3 Non-goals / guardrails

- No third-party browser analytics scripts.
- No collecting IPs, user IDs, query strings, or referrers into “analytics tables”.
- No public web routes that expose admin/ops data.
- No weakening admin token behavior; `/metrics` and `/api/admin/**` remain token-gated in prod.

---

## 1) Scope definition (what “private stats” means)

### 1.1 Ops health questions we must answer quickly

- Is the API up and serving real responses?
- Is the DB healthy (connections, storage, latency signals)?
- Are crawls/indexing progressing or stuck?
- Are failures spiking (job failures, crawl page failures, search errors)?
- Are we at risk of outage due to disk pressure (especially `/srv/healtharchive`) or memory pressure?
- Are automated timers/services running on schedule?

### 1.2 Usage questions we want (aggregate-only)

Expanded usage should answer questions like:

- How many searches per day/week/month?
- How many snapshot detail views? raw snapshot views?
- How many compare / changes / exports requests (if we add those events)?
- How many issue reports submitted?
- What’s the seasonal trend and growth rate?

Explicitly *not* in scope:

- “Top search terms” (would require storing query text; do not do this).
- Per-source/per-page breakdown that requires high-cardinality dimensions.

---

## 2) Constraints and current state (inventory)

### 2.1 Existing backend surfaces we will reuse

- **Admin endpoints (token-gated)**: `/api/admin/**`
  - Jobs list/detail/status counts, job snapshots, issue reports list/detail, search debug.
- **Metrics endpoint (token-gated)**: `GET /metrics`
  - Job status counts, storage totals, snapshot/page totals, crawl page totals.
  - Per-process search metrics: request count, errors, latency histogram buckets.
- **Usage metrics (aggregate-only; stored in DB)**:
  - Table: `usage_metrics (metric_date, event, count)`.
  - Public API: `GET /api/usage` currently returns daily + totals for a rolling window.

### 2.2 Data sensitivity notes

- `issue_reports` can include free text and optional email.
  - Treat as sensitive.
  - Grafana should not display raw free text or emails by default.

### 2.3 Production model constraints

- Single VPS; public ports are 80/443 only.
- SSH is private-only via Tailscale.
- Ops artifacts live under `/srv/healtharchive/ops/` with group-writable permissions.

---

## 3) Implementation phases (sequential)

Each phase includes:

- **Deliverables** (what must exist after)
- **Steps** (procedural checklist)
- **Acceptance criteria** (how we know it worked)
- **Rollback** (how to back out safely)

### Phase 1 — Formalize the “private observability” contract (documentation-first)

**Goal:** lock the boundaries so we don’t accidentally add public surface or privacy risk.

Status: implemented in this repo (see `docs/operations/observability-and-private-stats.md`).
Linked from `docs/operations/README.md`.

Deliverables:

- A short internal doc under `docs/operations/` defining:
  - what is collected (aggregate-only),
  - what is never collected,
  - what is public vs private.

Steps:

1. Draft `docs/operations/observability-and-private-stats.md`:
   - “Private stats = tailnet-only Grafana.”
   - “Public stats = `/status` + `/impact` pages.”
   - “Usage metrics are aggregate-only daily counters; no identifiers.”
2. Add explicit “do not expose admin/metrics to public UI” reminder.
3. Link the doc from `docs/operations/README.md`.

Acceptance criteria:

- A new operator can read one doc and understand what is and is not collected/exposed.

Rollback:

- Documentation-only; revert the file if needed.

---

### Phase 2 — Provision the ops stack host footprint (directories, users, secrets)

**Goal:** create a stable, least-privilege filesystem and secrets layout that matches existing project conventions.

Status: implemented in this repo (bootstrap script + docs).

Deliverables:

- Standard directories created under `/srv/healtharchive/ops/observability/` for:
  - dashboard exports / provisioning (no secrets)
  - public-safe operator notes
- Root-owned secret file locations under `/etc/healtharchive/observability/`.

Design choice (low-maintenance default):

- Keep Prometheus/Grafana data in distro defaults (`/var/lib/prometheus`, `/var/lib/grafana`)
  unless you have a strong reason to relocate.

Steps:

1. Add the internal contract doc under `docs/operations/` (Phase 1).
2. Add the bootstrap script:
   - `scripts/vps-bootstrap-observability-scaffold.sh`
3. Add the playbook:
   - `docs/operations/playbooks/observability-bootstrap.md`
4. Standardize secret file locations (created root-only by the script):
   - `/etc/healtharchive/observability/prometheus_backend_admin_token`
   - `/etc/healtharchive/observability/grafana_admin_password`
   - `/etc/healtharchive/observability/postgres_grafana_password`

Acceptance criteria:

- Secrets are not present in `/srv/healtharchive/ops/`.
- Secrets are not logged by scripts.
- The bootstrap script is idempotent and safe to re-run.

Rollback:

- Remove the new directories and secrets files.

---

### Phase 3 — Install and configure exporters (host + Postgres)

**Goal:** provide a minimal set of signals that catch real outages: disk, CPU, memory, DB health.

Status: implemented in this repo (installer script + playbook); requires running on the VPS.

Deliverables:

- Node exporter running as a service (private bind).
- Postgres exporter running as a service (private bind), using a least-privilege role.
  - Role: `postgres_exporter` with `pg_monitor`.
  - Credentials: `/etc/healtharchive/observability/postgres_exporter.env` (root-owned).

Steps:

1. Add the VPS installer script:
   - `scripts/vps-install-observability-exporters.sh`
2. Add the playbook:
   - `docs/operations/playbooks/observability-exporters.md`
3. Run on the VPS:
   - dry-run: `./scripts/vps-install-observability-exporters.sh`
   - apply: `sudo ./scripts/vps-install-observability-exporters.sh --apply`
4. Validate endpoints locally:
   - `curl http://127.0.0.1:9100/metrics | head`
   - `curl http://127.0.0.1:9187/metrics | head`

Acceptance criteria:

- Exporters are reachable locally and are not reachable from the public internet.

Rollback:

- Stop/disable services; uninstall packages.

---

### Phase 4 — Prometheus (scrape config, retention, service hardening)

**Goal:** collect time-series metrics in a controlled way that cannot fill disk or overload the backend.

Deliverables:

- Prometheus running under systemd.
- A `prometheus.yml` that scrapes:
  - backend `/metrics` via loopback
  - node exporter
  - postgres exporter
- Explicit retention and disk guards.

Steps:

1. Install Prometheus.
2. Configure retention:
   - Start conservative: `15d` or `30d` time retention.
   - Also set a size-based retention cap if available.
3. Configure scrape target for backend metrics:
   - Target: `http://127.0.0.1:8001/metrics`
   - Add the admin token header (from file) to avoid embedding secrets in `prometheus.yml`.
   - Use a conservative scrape interval (e.g., 60s–300s) because `/metrics` performs DB queries.
4. Configure scrape targets for exporters.
5. Add recording rules for expensive/standard derived series (later dashboards will depend on these).
6. Validate in Prometheus UI (loopback-only): targets are `UP`.

Acceptance criteria:

- Prometheus is stable for 24h with no meaningful CPU spikes and no rapid TSDB growth.
- Backend remains responsive under scrape load.

Rollback:

- Stop Prometheus; remove its data dir.

---

### Phase 5 — Grafana (private “stats page”) with tailnet-only access

**Goal:** create a single operator-only entrypoint for ops health and private usage.

Deliverables:

- Grafana running.
- Grafana accessible only via tailnet (Tailscale Serve).
- Data sources configured:
  - Prometheus
  - Postgres (read-only role)

Steps:

1. Install Grafana (OSS).
2. Configure Grafana security:
   - Disable anonymous access.
   - Disable self-signup.
   - Create a single operator admin account (or minimal operators).
3. Bind Grafana to loopback only.
4. Publish Grafana via Tailscale:
   - `tailscale serve` to expose `http://127.0.0.1:<grafana_port>` as tailnet-only HTTPS.
5. Configure data sources:
   - Prometheus at `http://127.0.0.1:<prom_port>`.
   - Postgres at `127.0.0.1:5432`, using `grafana_readonly` DB role.
6. Decide dashboard lifecycle:
   - Recommended: provision dashboards from files (keeps drift low).

Acceptance criteria:

- Grafana is reachable from an operator machine on the tailnet.
- Grafana is unreachable from the public internet.

Rollback:

- Remove `tailscale serve` config; stop/disable Grafana.

---

### Phase 6 — Dashboards: ops health (must-have)

**Goal:** ship dashboards that directly reduce operator time during “something feels wrong”.

Deliverables (minimum set):

1) **Ops overview** dashboard:

- Backend up (Prometheus `up` for the backend scrape job)
- Job status counts
- Snapshot totals
- Disk usage for `/srv/healtharchive`
- DB up + key DB signals
- Search errors and request volume

2) **Pipeline health** dashboard:

- Crawl pages crawled/failed totals
- Storage totals (warc/output/tmp)
- “No progress” heuristics (e.g., snapshots flatline)

3) **Search performance** dashboard:

- `healtharchive_search_duration_seconds_*` quantiles (using `histogram_quantile`)
- Search error rate
- Mode breakdown (relevance vs fallback vs boolean vs url)

Steps:

1. List the exact Prometheus series you will use (documented in each panel description).
2. Create dashboards with a small number of high-signal panels.
3. Add runbook links on dashboards (to existing playbooks such as deploy/verify).

Acceptance criteria:

- Operator can answer the Phase 1 questions (“is it up?”, “is disk dying?”, “are jobs stuck?”) in <2 minutes.

Rollback:

- Dashboards are config; revert dashboard JSON/provisioning.

---

### Phase 7 — Dashboards: expanded private usage (aggregate-only)

**Goal:** provide a long-window, operator-only view of usage without collecting identifiers.

Deliverables:

- A Grafana “Usage” dashboard backed by Postgres queries against `usage_metrics`.
- A “Monthly/Quarterly impact” dashboard that helps generate public-safe reporting.

Steps:

1. Confirm current `usage_metrics` event set:
   - `search_request`, `snapshot_detail`, `snapshot_raw`, `report_submitted`.
2. Decide which additional events are worth adding (still aggregate-only), e.g.:
   - `changes_list` (GET /api/changes)
   - `compare_view` (GET /api/changes/compare)
   - `timeline_view` (GET /api/snapshots/{id}/timeline)
   - `exports_manifest` (GET /api/exports)
   - `exports_download_snapshots`, `exports_download_changes` (GET /api/exports/*)
   - `sources_list` (GET /api/sources)
   - `stats_view` (GET /api/stats)

   Keep the list small; each event adds ongoing interpretation overhead.

3. Make a **public vs private exposure** decision for new events:
   - Public `/api/usage` should remain aligned with public reporting pages.
   - Private dashboards can see more than public.

   Recommended approach:
   - Store *all* events in `usage_metrics`.
   - Expose only a curated subset via public `GET /api/usage`.
   - Optionally create an admin-only endpoint for full usage metrics later.

4. Build the initial usage dashboard using the existing four events:
   - Daily series
   - 7d/30d rolling averages
   - Month-to-date vs previous month comparisons
   - Report submissions trend (proxy for community engagement)

5. Add a dashboard for quarterly summaries to match ops cadence:
   - 90d / 180d trend panels
   - Exports/download activity

Acceptance criteria:

- Operator can generate a public-safe “impact summary” without guessing.
- No identifiers or raw text are introduced.

Rollback:

- No schema changes required for dashboards; remove dashboards.

---

### Phase 8 — Alerting strategy (minimal, high-signal)

**Goal:** get notified about real outages without creating pager fatigue.

Deliverables:

- A small alert set routed to one operator channel.
- Each alert includes a runbook link.

Steps:

1. Decide routing (recommended minimal options):
   - Keep existing external uptime checks for public URLs.
   - Use Grafana managed alerts (simpler than full Alertmanager at this project scale).
2. Start with only these alerts:
   - Backend scrape down for >5 minutes.
   - Disk usage >80% (warning) and >90% (critical) on `/srv/healtharchive`.
   - Search error rate non-zero for sustained window.
   - Job failures rising (or `failed` status count increases unexpectedly).
3. Add runbook links that point to existing playbooks and the production runbook.
4. Test each alert intentionally once.

Acceptance criteria:

- Alerts fire when expected and do not produce daily noise.

Rollback:

- Disable alerts; keep dashboards.

---

### Phase 9 — Admin/ops UI (decision gate; build only if needed)

**Goal:** avoid building a bespoke UI unless it clearly reduces toil.

Decision gate (required):

- Use Grafana + existing JSON endpoints for at least 1–2 weeks.
- Track operator friction:
  - “What do we still need SSH for?”
  - “What do dashboards not answer?”
  - “What actions are error-prone as CLI-only?”

If (and only if) justified, implement **read-only** admin UI MVP.

Read-only MVP scope (no mutating actions):

- Jobs list + filters (maps to `GET /api/admin/jobs`)
- Job detail (maps to `GET /api/admin/jobs/{id}`)
- Issue report list/detail (maps to `GET /api/admin/reports`, `/api/admin/reports/{id}`)
- Search debug tool (maps to `GET /api/admin/search-debug`)

Security requirements:

- Must be tailnet-only.
- Must not embed the admin token into a publicly served bundle.
- Prefer server-side token usage (UI backend calls admin API; browser never sees token).

Acceptance criteria:

- Operator can triage jobs and reports without SSH.

Rollback:

- Remove the admin UI service; no data migrations required.

---

### Phase 10 — Drift-proofing and docs updates (make it maintainable)

**Goal:** ensure the new stack stays reproducible and doesn’t silently degrade.

Deliverables:

- Production runbook updated with:
  - how to access Grafana (tailnet URL)
  - how to restart services
  - where dashboards/config live
- Baseline drift policy extended to include observability invariants.
- One ops playbook: “Observability maintenance”.

Steps:

1. Update `docs/deployment/production-single-vps.md`:
   - Add a section describing Prometheus/Grafana components.
   - Document tailnet-only access path.
2. Update `docs/operations/production-baseline-policy.toml`:
   - Assert that Prometheus/Grafana services exist and are enabled.
   - Assert that no new public ports are open.
   - Assert secrets/config files exist with correct ownership/mode.
3. Add `docs/operations/playbooks/observability-maintenance.md`:
   - upgrade cadence (quarterly)
   - how to export dashboards
   - how to rotate creds
   - how to prune retention
4. Add a simple verification checklist (similar to deploy+verify style):
   - Grafana reachable via tailnet
   - Prometheus targets up
   - Key dashboards load

Acceptance criteria:

- A new operator can set up and use the stack with only canonical docs.

Rollback:

- Remove baseline assertions and docs; disable services.

---

## 4) Sequencing and estimated time

Conservative estimate (single operator, careful execution):

- Phase 1: 0.5–1 day
- Phase 2: 0.5 day
- Phase 3: 1–2 days
- Phase 4: 1–2 days
- Phase 5: 1–2 days
- Phase 6: 1–3 days
- Phase 7: 1–3 days
- Phase 8: 1–2 days
- Phase 9: defer unless needed
- Phase 10: 0.5–1 day

Total to a useful private stats surface (through Phase 7): ~5–12 days elapsed.

---

## 5) Operational checklists (copy/paste)

### 5.1 “Did we accidentally make anything public?” checklist

- [ ] No new DNS records for ops tools.
- [ ] No new Caddy vhosts for ops tools.
- [ ] Hetzner firewall unchanged (still only 80/443 + Tailscale UDP).
- [ ] UFW unchanged except tailnet-only allowances (if any).
- [ ] Grafana/Prometheus/exporters bind to loopback.
- [ ] Access is via Tailscale Serve only.

### 5.2 “Did we accidentally collect identifiers?” checklist

- [ ] No IP/user-agent logging copied into analytics tables.
- [ ] No query strings stored.
- [ ] No user IDs (there are none).
- [ ] Usage metrics remain daily aggregates.

---

## 6) Notes for future iterations (do not do on day 1)

- If `/metrics` scraping becomes heavy:
  - Add server-side caching for `/metrics` output for a short TTL.
  - Or move some DB-derived metrics into a scheduled “snapshot table” updated periodically.
- If you need “per-source usage” later:
  - Prefer a *small, fixed* dimension space (e.g., “filtered vs unfiltered search”), not per-URL breakdown.
  - If true per-source usage is required, implement a dedicated daily aggregation job that computes counts by joining stable DB keys (source_id) rather than recording high-cardinality labels on every request.

---

## Appendix A — Component / port / access matrix (target state)

This section exists to prevent accidental public exposure.

| Component | Purpose | Bind | Port | Who can access | Notes |
| --- | --- | --- | --- | --- | --- |
| HealthArchive API | Public API + admin endpoints | `127.0.0.1` | `8001` | Public via Caddy for `/api/**`; operators for `/metrics` + `/api/admin/**` | Prometheus scrapes `/metrics` via loopback with token |
| Caddy | Public TLS reverse proxy | public | `443` | Public | Should not proxy Grafana/Prometheus |
| Prometheus | Metrics scraping + storage | `127.0.0.1` | `9090` | Operators (optional) | UI not required; keep loopback by default |
| Grafana | Private stats “page” | `127.0.0.1` | `3000` | Operators (tailnet only) | Expose via `tailscale serve` |
| node exporter | Host CPU/mem/disk signals | `127.0.0.1` | `9100` | Prometheus only | Never public |
| postgres exporter | Postgres signals | `127.0.0.1` | `9187` (typical) | Prometheus only | Never public |

If you choose to bind any of these to a tailnet interface (instead of loopback),
limit it explicitly to `tailscale0` and still do **not** add any Caddy vhosts.

---

## Appendix B — Prometheus configuration skeleton (safe, no secrets)

Goal: scrape the backend via loopback and provide the admin token via a
**credentials file** (so it never appears in `prometheus.yml`).

Suggested files:

- `/etc/prometheus/prometheus.yml`
- `/etc/healtharchive/observability/prometheus_backend_admin_token` (created root-only by default; later phases may relax for Prometheus)

Example `prometheus.yml` (adjust ports/paths to your distro packaging):

```yaml
global:
  scrape_interval: 60s
  scrape_timeout: 10s

scrape_configs:
  - job_name: healtharchive_backend
    metrics_path: /metrics
    scheme: http
    static_configs:
      - targets: ["127.0.0.1:8001"]
    authorization:
      type: Bearer
      credentials_file: /etc/healtharchive/observability/prometheus_backend_admin_token

  - job_name: node
    static_configs:
      - targets: ["127.0.0.1:9100"]

  - job_name: postgres
    static_configs:
      - targets: ["127.0.0.1:9187"]
```

Hardening checklist (Prometheus):

- Run Prometheus as a dedicated user (distro packages usually do this).
- Set retention explicitly (time and/or size) so it cannot fill `/`.
- Prefer distro defaults for TSDB (`/var/lib/prometheus`) to reduce maintenance.

Backend load control:

- If `/metrics` queries prove heavy, increase scrape interval first.
- Only then consider caching or pre-aggregation in the backend.

---

## Appendix C — Grafana data sources and least-privilege Postgres roles

Grafana should have:

1) **Prometheus** data source (time-series)
2) **Postgres** data source (tables + long-window aggregates)

### C.1 Postgres role: Grafana read-only

Create a dedicated DB role for Grafana; do **not** reuse the app DB user.

Example SQL (adjust DB name/schema if needed):

```sql
-- One-time: create a login role.
CREATE ROLE grafana_readonly LOGIN PASSWORD '<STRONG_PASSWORD>';

-- Allow connecting.
GRANT CONNECT ON DATABASE healtharchive TO grafana_readonly;

-- Allow schema usage.
GRANT USAGE ON SCHEMA public TO grafana_readonly;

-- Allow reading usage aggregates.
GRANT SELECT ON TABLE usage_metrics TO grafana_readonly;

-- Allow reading jobs/snapshots if you want table panels.
GRANT SELECT ON TABLE archive_jobs TO grafana_readonly;
GRANT SELECT ON TABLE sources TO grafana_readonly;
GRANT SELECT ON TABLE snapshots TO grafana_readonly;
```

### C.2 Sensitive data: issue reports

`issue_reports` may contain emails + free text. For low-risk dashboards:

- Create a redacted view that excludes `reporter_email`, `description`, and
  `internal_notes`.
- Grant Grafana access only to that view.

Example:

```sql
CREATE VIEW grafana_issue_reports_summary AS
SELECT
  id,
  category,
  status,
  created_at,
  updated_at,
  snapshot_id,
  original_url,
  page_url
FROM issue_reports;

GRANT SELECT ON grafana_issue_reports_summary TO grafana_readonly;
```

If you later build a bespoke admin UI for reports, handle sensitive fields there
with explicit operator intent and audit trails.

---

## Appendix D — Expanded usage metrics: design and implementation details

### D.1 Current state

- Storage: `usage_metrics(metric_date, event, count)`.
- Events today:
  - `search_request`
  - `snapshot_detail`
  - `snapshot_raw`
  - `report_submitted`

### D.2 Expansion strategy (low-maintenance)

Principles:

- Add **a few** new events that correspond to real user workflows.
- Avoid any dimension that explodes cardinality.
- Keep event names stable and documented.

Recommended “next events” (candidate set):

- `changes_list` (human workflow: “what changed?”)
- `compare_view` (human workflow: “what changed between captures?”)
- `exports_manifest` (human workflow: “what data can I download?”)
- `exports_download_snapshots`, `exports_download_changes` (human workflow: “download data”)

Recommended “do not add” (privacy/toil risk):

- Query text capture (top terms).
- Per-URL/per-path tracking.
- Per-source breakdown via labels recorded per request.

### D.3 Public vs private usage exposure (important)

Because the project already uses `GET /api/usage` to power public reporting, we
need a stable policy:

- **DB can store more than the public API exposes.**
- Public API should return only metrics you’re comfortable publishing.

Implementation plan (backend; later work):

1. Split events into two lists:
   - `EVENTS_INTERNAL` (everything you store)
   - `EVENTS_PUBLIC` (subset returned by `GET /api/usage`)
2. Keep the existing response shape stable for the frontend `/impact` page.
   - If you add new public fields, coordinate frontend changes.
3. For truly private usage panels, Grafana should read from Postgres directly,
   not from `GET /api/usage`.

Testing plan (backend; later work):

- Add tests that verify:
  - private-only events do not appear in `/api/usage` output.
  - public events continue to increment and render as expected.

### D.4 Excluding “automation noise”

Do not count:

- `/api/health` (it is hit by uptime checks)

Prefer counting:

- Search requests, snapshot views, compare views, report submissions—these are
  closer to human workflow.

---

## Appendix E — Verification and rollback checklists (operator run)

### E.1 Verify “tailnet only” access

- From a machine **not** on your tailnet:
  - Grafana URL should be unreachable.
- From a tailnet-connected operator machine:
  - Grafana URL loads and prompts for login.

### E.2 Verify backend scrape is token-protected

- `curl -i https://api.healtharchive.ca/metrics` should be `403` without token.
- Prometheus target for backend `/metrics` should show `UP`.

### E.3 Verify dashboards are answering the right questions

- Ops overview: backend up, job counts, disk, DB.
- Usage: daily totals (long-window) match expectations.

### E.4 Rollback recipe (if anything feels risky)

- Remove `tailscale serve` mapping first (instant private UI removal).
- Stop Grafana.
- Stop Prometheus.
- Leave backend unchanged.
