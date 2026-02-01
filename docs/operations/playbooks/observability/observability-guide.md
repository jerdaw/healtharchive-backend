# Observability Setup and Maintenance Guide

**Scope:** Complete setup of the private observability stack (Prometheus, Grafana, Alertmanager) on the production VPS.

Canonical boundary doc (read first): [observability-and-private-stats.md](../../observability-and-private-stats.md)

---

## Overview

This guide covers the full observability stack installation in order:

1. [Bootstrap](#1-bootstrap-prerequisites) - Filesystem + secrets layout
2. [Install Exporters](#2-install-exporters) - node_exporter + postgres_exporter
3. [Configure Prometheus](#3-configure-prometheus) - Metrics collection
4. [Configure Grafana](#4-configure-grafana) - Dashboards + tailnet access
5. [Provision Dashboards](#5-provision-dashboards) - Automated dashboard deployment
6. [Configure Alerting](#6-configure-alerting) - Alertmanager + rules
7. [Ongoing Maintenance](#7-ongoing-maintenance) - Upgrades, rotation, troubleshooting

**Architecture:** All services bind to loopback only. Access via Tailscale SSH port-forward.

---

## 1. Bootstrap (Prerequisites)

**Goal:** Prepare filesystem + secrets layout without installing services.

### Preconditions

- On the production VPS with `sudo` access
- `/srv/healtharchive/` exists
- Ops group exists (usually `healtharchive`)

### Procedure

```bash
cd /opt/healtharchive-backend
sudo ./scripts/vps-bootstrap-observability-scaffold.sh
```

Populate secret files (do NOT store under `/srv/healtharchive/ops/`):

```bash
sudoedit /etc/healtharchive/observability/prometheus_backend_admin_token
sudoedit /etc/healtharchive/observability/grafana_admin_password
sudoedit /etc/healtharchive/observability/postgres_grafana_password
```

### Verify

```bash
stat -c '%U:%G %a %n' /srv/healtharchive/ops/observability /srv/healtharchive/ops/observability/*
stat -c '%U:%G %a %n' /etc/healtharchive/observability/*
```

### Rollback

```bash
sudo rm -rf /srv/healtharchive/ops/observability
sudo rm -rf /etc/healtharchive/observability
```

---

## 2. Install Exporters

**Goal:** Install node_exporter (host metrics) and postgres_exporter (DB health), loopback-only.

### Preconditions

- Bootstrap complete (directories exist)
- Postgres running locally

### Procedure

```bash
cd /opt/healtharchive-backend
./scripts/vps-install-observability-exporters.sh          # Dry-run
sudo ./scripts/vps-install-observability-exporters.sh --apply
```

This installs packages, creates the `postgres_exporter` DB role with `pg_monitor`, and forces loopback binding (`127.0.0.1:9100`, `127.0.0.1:9187`).

### Verify

```bash
curl -s http://127.0.0.1:9100/metrics | head
curl -s http://127.0.0.1:9187/metrics | head
ss -lntp | grep -E ':9100|:9187'  # Expect 127.0.0.1 only
systemctl --no-pager status prometheus-node-exporter prometheus-postgres-exporter
```

### Rollback

```bash
sudo systemctl disable --now prometheus-node-exporter prometheus-postgres-exporter || true
sudo rm -rf /etc/systemd/system/prometheus-node-exporter.service.d \
            /etc/systemd/system/prometheus-postgres-exporter.service.d
sudo systemctl daemon-reload
sudo apt-get remove -y prometheus-node-exporter prometheus-postgres-exporter
sudo rm -f /etc/healtharchive/observability/postgres_exporter.env \
           /etc/healtharchive/observability/postgres_exporter_password
```

---

## 3. Configure Prometheus

**Goal:** Install Prometheus and configure scraping of HealthArchive metrics.

### Preconditions

- Exporters installed and loopback-only
- `/etc/healtharchive/observability/prometheus_backend_admin_token` set to backend admin token
- Backend API reachable: `curl -s http://127.0.0.1:8001/api/health`

### Procedure

```bash
cd /opt/healtharchive-backend
./scripts/vps-install-observability-prometheus.sh          # Dry-run
sudo ./scripts/vps-install-observability-prometheus.sh --apply
```

This installs Prometheus, writes config to `/etc/prometheus/prometheus.yml`, forces loopback binding (`127.0.0.1:9090`), and caps retention.

### Verify

```bash
curl -s http://127.0.0.1:9090/-/ready
ss -lntp | grep -E ':9090\b'  # Expect 127.0.0.1 only
curl -s http://127.0.0.1:9090/api/v1/targets | head
curl -s "http://127.0.0.1:9090/api/v1/query?query=up%7Bjob%3D%22healtharchive_backend%22%7D" | head
```

### Rollback

```bash
sudo systemctl disable --now prometheus.service
sudo rm -rf /etc/systemd/system/prometheus.service.d
sudo systemctl daemon-reload
sudo apt-get remove -y prometheus  # Optional
```

---

## 4. Configure Grafana

**Goal:** Install Grafana as operator-only dashboard, reachable via tailnet.

### Preconditions

- Prometheus installed on `127.0.0.1:9090`
- Tailscale connected to tailnet
- Secrets set: `grafana_admin_password`, `postgres_grafana_password`

### Procedure

```bash
cd /opt/healtharchive-backend
./scripts/vps-install-observability-grafana.sh          # Dry-run
sudo ./scripts/vps-install-observability-grafana.sh --apply
```

This binds Grafana to `127.0.0.1:3000`, disables anonymous access, resets admin password, and creates the `grafana_readonly` Postgres role.

### Access Options

**Preferred - SSH port-forward (more private):**

```bash
ssh -L 3000:127.0.0.1:3000 haadmin@<vps-tailscale-ip>
# Then open http://127.0.0.1:3000
```

**Optional - Tailscale Serve (requires HTTPS certs enabled):**

```bash
./scripts/vps-enable-tailscale-serve-grafana.sh          # Dry-run
sudo ./scripts/vps-enable-tailscale-serve-grafana.sh --apply
sudo tailscale serve status  # Get HTTPS URL
```

### Configure Data Sources (Grafana UI)

1. **Prometheus:** URL `http://127.0.0.1:9090`
2. **Postgres:** Host `127.0.0.1:5432`, DB `healtharchive`, User `grafana_readonly`, TLS disabled

### Verify

```bash
ss -lntp | grep -E ':3000\b'  # Expect 127.0.0.1 only
# Test data sources in Grafana UI
```

### Rollback

```bash
sudo tailscale serve reset  # If using Serve
sudo systemctl disable --now grafana-server.service
sudo rm -rf /etc/systemd/system/grafana-server.service.d
sudo systemctl daemon-reload
```

---

## 5. Provision Dashboards

**Goal:** Install ops and usage dashboards reproducibly.

### Preconditions

- Prometheus and Grafana running
- Data sources configured in Grafana UI:
  - Prometheus: named `prometheus`
  - Postgres: named `grafana-postgresql-datasource`

### Procedure

```bash
cd /opt/healtharchive-backend
git pull
./scripts/vps-install-observability-dashboards.sh          # Dry-run
sudo ./scripts/vps-install-observability-dashboards.sh --apply
```

### Verify

In Grafana, find the `HealthArchive` folder with these dashboards:

- HealthArchive - Ops Overview
- HealthArchive - Ops Console (Read-only)
- HealthArchive - Pipeline Health
- HealthArchive - Search Performance
- HealthArchive - Usage (Private, Aggregate)
- HealthArchive - Impact Summary (Private, Aggregate)

### Troubleshooting

- **Permission errors:** Add Grafana to ops group: `sudo usermod -aG healtharchive grafana && sudo systemctl restart grafana-server`
- **"Data source not found":** Rename data sources to match expected names or edit dashboard JSON

### Rollback

```bash
sudo rm -f /etc/grafana/provisioning/dashboards/healtharchive.yaml
sudo rm -rf /srv/healtharchive/ops/observability/dashboards/healtharchive
sudo systemctl restart grafana-server
```

---

## 6. Configure Alerting

**Goal:** Get notified about real outages without pager fatigue.

### Preconditions

- Prometheus running
- Node exporter installed (for disk metrics)
- If using WARC tiering: `sudo systemctl enable --now healtharchive-tiering-metrics.timer`

### Choose Operator Channel

Create a webhook URL (Discord, Slack, or any HTTPS endpoint accepting Alertmanager JSON):

```bash
sudoedit /etc/healtharchive/observability/alertmanager_webhook_url
```

**For Pushover:**

```bash
sudoedit /etc/healtharchive/observability/pushover_app_token
sudoedit /etc/healtharchive/observability/pushover_user_key
sudo ./scripts/vps-install-observability-pushover-relay.sh --apply
# Set webhook URL to: http://127.0.0.1:9911/alertmanager
```

### Procedure

```bash
cd /opt/healtharchive-backend
git pull
./scripts/vps-install-observability-alerting.sh          # Dry-run
sudo ./scripts/vps-install-observability-alerting.sh --apply
# Optional: --mountpoint / (if storage not on /)
```

### Alert Rules (High-Signal Set)

- Backend scrape down (>5m)
- Disk usage >80% warning, >90% critical
- Sustained `/api/search` errors (traffic-gated)
- Job failures increased
- Storage Box mount down (if tiering enabled)
- WARC tiering bind-mount failed
- Tiering metrics stale (>2 hours)
- Tiering hot path unreadable
- Annual campaign sentinel failed (Jan 01 UTC)

### Verify

```bash
curl -s http://127.0.0.1:9093/-/ready
curl -s http://127.0.0.1:9090/api/v1/rules | head
ss -lntp | grep -E ':9093\b|:9090\b'
```

### Test Delivery

```bash
amtool alert add HealthArchiveTestAlert severity=warning service=healtharchive
```

### Rollback

```bash
sudo systemctl disable --now prometheus-alertmanager.service || \
  sudo systemctl disable --now alertmanager.service
sudo rm -f /etc/prometheus/rules/healtharchive-alerts.yml
sudo systemctl restart prometheus.service
```

---

## 7. Ongoing Maintenance

### Quick Verify (Recommended)

On VPS:

```bash
cd /opt/healtharchive-backend
./scripts/vps-verify-observability.sh
```

From laptop (tailnet SSH tunnel):

```bash
ssh -N \
  -L 3000:127.0.0.1:3000 \
  -L 9090:127.0.0.1:9090 \
  -L 8002:127.0.0.1:8002 \
  haadmin@<vps-tailscale-ip>
```

Then open Grafana (`http://127.0.0.1:3000/`) and check:

- HealthArchive - Ops Overview
- HealthArchive - Pipeline Health
- HealthArchive - Usage (Private, Aggregate)

### Quarterly Upgrade Cadence

```bash
sudo apt-get update && sudo apt-get -y upgrade
sudo systemctl restart prometheus prometheus-alertmanager \
  prometheus-node-exporter prometheus-postgres-exporter \
  grafana-server healtharchive-pushover-relay healtharchive-admin-proxy
./scripts/vps-verify-observability.sh
```

### Dashboard Updates

```bash
cd /opt/healtharchive-backend
git pull
sudo ./scripts/vps-install-observability-dashboards.sh --apply
```

### Credential Rotation

**Backend admin token:**

```bash
# Update /etc/healtharchive/backend.env and /etc/healtharchive/observability/prometheus_backend_admin_token
sudo systemctl restart healtharchive-api prometheus healtharchive-admin-proxy
```

**Grafana admin password:**

```bash
# Update /etc/healtharchive/observability/grafana_admin_password
sudo ./scripts/vps-install-observability-grafana.sh --apply --skip-apt --skip-db-role
```

**Grafana Postgres password:**

```bash
# Update /etc/healtharchive/observability/postgres_grafana_password
sudo ./scripts/vps-install-observability-grafana.sh --apply --skip-apt
# Update data source in Grafana UI if needed
```

**Alert webhook URL:**

```bash
# Update /etc/healtharchive/observability/alertmanager_webhook_url
sudo ./scripts/vps-install-observability-alerting.sh --apply
```

### Prometheus Retention Tuning

```bash
sudo ./scripts/vps-install-observability-prometheus.sh --apply --skip-apt \
  --retention-time 15d --retention-size 1GB
curl -s http://127.0.0.1:9090/-/ready
```

### Troubleshooting

```bash
# Service status
systemctl status grafana-server prometheus prometheus-alertmanager --no-pager -l

# Verify loopback-only binding
ss -lntp | grep -E ':3000|:8002|:9090|:9093|:9100|:9187|:9911'

# Check Prometheus targets
curl -s http://127.0.0.1:9090/api/v1/targets | head
```

---

## See Also

- [monitoring-and-alerting.md](monitoring-and-alerting.md) - External monitors and Healthchecks setup
- [observability-and-private-stats.md](../../observability-and-private-stats.md) - Public/private boundary contract
