# Observability + private stats (internal contract)

This document defines the **public vs private** boundaries for HealthArchive
observability and “private stats”, with a bias toward:

- low maintenance / low toil
- privacy-preserving measurement (aggregate-only; no identifiers)
- no new public attack surface

This is an **internal** ops document. Keep it public-safe (no secrets).

---

## 1) Definitions: public vs private surfaces

### Public surfaces (intentionally public)

- Frontend pages like `/status` and `/impact` (public reporting).
- Public API routes under `/api/**` (search, sources, snapshots, public usage window).

### Private surfaces (operator-only)

- Observability stack UIs (Grafana; optionally Prometheus UI).
- Admin endpoints:
  - `/api/admin/**`
  - `/metrics` (Prometheus-style metrics)

**Rule:** public web UI must never call or depend on `/api/admin/**` or `/metrics`.

---

## 2) Private access model (default)

Default approach: **tailnet-only** access, using Tailscale.

- Preferred: access Grafana via an SSH port-forward over Tailscale (simple, private, no Tailscale HTTPS certs required).
- Optional: use `tailscale serve` (tailnet-only HTTPS) if you want a shareable URL and you are OK with the tailnet hostname appearing in public certificate logs.
- Keep Prometheus UI loopback-only unless operators explicitly need it.

Non-goals:

- No new public DNS records for ops tools.
- No Caddy vhosts for ops tools.
- No new public firewall openings.

If an operator needs access without Tailscale, treat that as a deliberate security
change and document it as a separate decision.

---

## 2.1 Host footprint (dirs + secrets)

These paths are conventions for **operators** and **automation**; they do not
imply anything is public.

### Ops directories (public-safe by policy)

- `/srv/healtharchive/ops/observability/`
  - `dashboards/` — exported dashboard JSON, provisioning files (no secrets)
  - `notes/` — public-safe operational notes

Low-maintenance default:

- Keep Prometheus/Grafana data in distro defaults (typically `/var/lib/prometheus` and
  `/var/lib/grafana`) unless you have a strong reason to relocate.

### Secrets (root-owned; never under `/srv/healtharchive/ops/`)

- `/etc/healtharchive/observability/prometheus_backend_admin_token`
- `/etc/healtharchive/observability/grafana_admin_password`
- `/etc/healtharchive/observability/postgres_grafana_password`
- `/etc/healtharchive/observability/postgres_exporter.env`
- `/etc/healtharchive/observability/postgres_exporter_password`

Bootstrap helper (VPS only):

- `scripts/vps-bootstrap-observability-scaffold.sh`
- `scripts/vps-install-observability-exporters.sh`
- `scripts/vps-install-observability-prometheus.sh`
- `scripts/vps-install-observability-grafana.sh`
- `scripts/vps-enable-tailscale-serve-grafana.sh`

---

## 3) Data collection contract (privacy-preserving)

### 3.1 What we collect (allowed)

- **Usage metrics**: daily aggregate counters only.
  - Storage: `usage_metrics(metric_date, event, count)`
  - No IPs, no user IDs, no per-request identifiers.
- **Ops/service metrics**: operational counts and totals needed to keep the service healthy.
  - Examples: job status counts, snapshot totals, storage totals, search error rate.

See also: `data-handling-retention.md` (retention + PHI risk notes).

### 3.2 What we do not collect (explicitly disallowed)

- Query strings / “top search terms”.
- IP addresses, user agents, referrers stored into analytics tables.
- Per-user or per-session identifiers.
- High-cardinality dimensions (per-URL/page/path tracking).
- Third-party browser analytics scripts (unless a separate, explicit privacy/security decision is made).

### 3.3 Public vs private usage reporting

- Public reporting can show only a curated subset of aggregate usage metrics.
- Private dashboards (Grafana) may show a broader set of aggregate events **from the DB**,
  as long as they remain aggregate-only and public-safe.

If adding new usage events:

- Keep the event set small and stable.
- Update docs and tests to ensure private-only events do not accidentally appear in public reporting.

---

## 4) Observability architecture (intended shape)

### 4.1 Prometheus scraping

- Prometheus scrapes backend `GET /metrics` via loopback and includes the admin token.
- Exporters (node/postgres) are loopback- or tailnet-only and scraped by Prometheus.
- Retention must be capped (time and/or size) so Prometheus cannot fill disk.

### 4.2 Grafana dashboards (“private stats page”)

Grafana is the operator-facing surface.

Data sources:

- Prometheus (time-series).
- Postgres (tables and long-window aggregates), using a **dedicated read-only** DB role.

Sensitive tables:

- Treat `issue_reports` as sensitive (may contain free text and emails).
- Prefer redacted views for Grafana (counts + metadata only), and keep full text for explicit operator workflows.

---

## 5) Operational invariants (must remain true)

- In production/staging, admin token must be configured and admin/metrics endpoints must not be public.
- Secrets must not be written under `/srv/healtharchive/ops/` (ops artifacts are public-safe by policy).
- Anything that changes public vs private boundaries must be documented as a deliberate decision.

---

## 6) Related docs (canonical references)

- Monitoring + CI checklist: `monitoring-and-ci-checklist.md`
- Data handling & retention: `data-handling-retention.md`
- Production runbook: `../deployment/production-single-vps.md`
- Ops playbooks index: `playbooks/README.md`
