# Ops Observability + Private Usage Dashboards (Implemented 2025-12-24)

**Status:** Implemented | **Scope:** Prometheus/Grafana observability stack with Tailscale-only access for ops health and aggregate usage metrics.

## Outcomes

- **Prometheus:** Scrapes backend `/metrics`, node exporter, and postgres exporter on loopback
- **Grafana:** Private stats surface accessible only via Tailscale (SSH port-forward or Tailscale Serve)
- **Dashboards:** Ops overview, pipeline health, search performance, private usage, impact summary
- **Alerting:** Minimal high-signal alerts via Prometheus rules + Alertmanager webhook routing
- **Admin Proxy:** Loopback-only proxy for `/api/admin/**` without manual token copying
- **Usage Metrics:** Expanded event set (`changes_list`, `compare_view`, `timeline_view`, exports) stored as daily aggregates

## Canonical Docs Updated

- Contract: [operations/observability-and-private-stats.md](../../operations/observability-and-private-stats.md)
- Production runbook: [deployment/production-single-vps.md](../../deployment/production-single-vps.md)
- Playbook: [playbooks/observability/observability-guide.md](../../operations/playbooks/observability/observability-guide.md) (consolidated)

## Key Design Decisions

- **Tailscale-only access:** No new public DNS or ports; keeps public attack surface unchanged
- **Aggregate-only usage:** Daily counters in `usage_metrics` table; no IPs, query strings, or user IDs
- **Grafana as ops UI:** Dashboards serve read-only console needs; bespoke admin UI deferred
- **Least-privilege Postgres role:** `grafana_readonly` with SELECT on specific tables + redacted views for sensitive data

## Scripts Added

- `scripts/vps-bootstrap-observability-scaffold.sh`
- `scripts/vps-install-observability-exporters.sh`
- `scripts/vps-install-observability-prometheus.sh`
- `scripts/vps-install-observability-grafana.sh`
- `scripts/vps-install-observability-dashboards.sh`
- `scripts/vps-install-observability-alerting.sh`

## Historical Context

10-phase sequential implementation (800+ lines) with detailed acceptance criteria, rollback plans, and configuration skeletons. Appendices covered Prometheus config, Grafana data sources, Postgres roles, and usage metrics expansion. Preserved in git history.
