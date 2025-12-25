# Observability maintenance (Prometheus + Grafana; VPS)

Goal: keep the private observability stack healthy and reproducible with low operator effort.

Canonical boundary doc (read first):

- `../observability-and-private-stats.md`

## What this stack is (ELI5)

- **Prometheus** is the “collector”: it scrapes numbers from services (metrics) and stores them.
- **Grafana** is the “dashboard”: it shows graphs/tables from Prometheus (and Postgres).
- **Alertmanager** is the “notifier”: it sends a message to you when something looks wrong.

In dashboards, a “hit” usually means “a request happened” (e.g., someone used search or viewed a snapshot).

Nothing here should be public. Everything is loopback-only on the VPS and you access it via **Tailscale + SSH port-forwarding**.

## Quick verify (recommended)

On the VPS:

```bash
cd /opt/healtharchive-backend
./scripts/vps-verify-observability.sh
```

From your laptop (tailnet-only SSH tunnel; keep the terminal open):

```bash
ssh -N \
  -L 3000:127.0.0.1:3000 \
  -L 9090:127.0.0.1:9090 \
  -L 8002:127.0.0.1:8002 \
  haadmin@<vps-tailscale-ip>
```

Then open:

- Grafana: `http://127.0.0.1:3000/`
- Prometheus (optional): `http://127.0.0.1:9090/`
- Admin proxy: `http://127.0.0.1:8002/`

In Grafana, check these dashboards (they should show numbers/graphs, not errors):

- `HealthArchive - Ops Overview`
- `HealthArchive - Pipeline Health`
- `HealthArchive - Usage (Private, Aggregate)`

## Quarterly upgrade cadence (recommended)

1) Update packages:

```bash
sudo apt-get update
sudo apt-get -y upgrade
```

2) Restart observability services:

```bash
sudo systemctl restart \
  prometheus \
  prometheus-alertmanager \
  prometheus-node-exporter \
  prometheus-postgres-exporter \
  grafana-server \
  healtharchive-pushover-relay \
  healtharchive-admin-proxy
```

3) Verify:

```bash
cd /opt/healtharchive-backend
./scripts/vps-verify-observability.sh
```

## Dashboards updates

Dashboards are provisioned from JSON in this repo.

On the VPS:

```bash
cd /opt/healtharchive-backend
git pull
sudo ./scripts/vps-install-observability-dashboards.sh --apply
```

## Credential rotation (when needed)

All secrets live under `/etc/healtharchive/observability/` (never commit them).

### Backend admin token (affects: Prometheus scrape + admin proxy)

If you rotate `HEALTHARCHIVE_ADMIN_TOKEN` in `/etc/healtharchive/backend.env`, also update:

- `/etc/healtharchive/observability/prometheus_backend_admin_token`

Then restart:

```bash
sudo systemctl restart healtharchive-api
sudo systemctl restart prometheus
sudo systemctl restart healtharchive-admin-proxy
```

Verify:

- Prometheus targets are healthy: `curl -s http://127.0.0.1:9090/api/v1/targets | head`
- Admin proxy works: `curl -s http://127.0.0.1:8002/api/admin/jobs?limit=1 | head`

### Grafana admin password

1) Update:

- `/etc/healtharchive/observability/grafana_admin_password`

2) Re-apply Grafana config (resets the Grafana admin password from the file):

```bash
cd /opt/healtharchive-backend
sudo ./scripts/vps-install-observability-grafana.sh --apply --skip-apt --skip-db-role
```

### Grafana Postgres password (grafana_readonly)

1) Update:

- `/etc/healtharchive/observability/postgres_grafana_password`

2) Re-apply Grafana config (updates the DB role password):

```bash
cd /opt/healtharchive-backend
sudo ./scripts/vps-install-observability-grafana.sh --apply --skip-apt
```

Then update the Postgres data source in Grafana UI if needed.

### Alert destination (Pushover relay)

This setup routes Alertmanager to a local relay:

- `/etc/healtharchive/observability/alertmanager_webhook_url` should be:
  - `http://127.0.0.1:9911/alertmanager`

If you change it, re-apply alerting to regenerate `/etc/prometheus/alertmanager.yml`:

```bash
cd /opt/healtharchive-backend
sudo ./scripts/vps-install-observability-alerting.sh --apply
```

## Prometheus retention tuning (disk safety)

To change retention (example: 15 days, 1GB cap):

```bash
cd /opt/healtharchive-backend
sudo ./scripts/vps-install-observability-prometheus.sh --apply --skip-apt --retention-time 15d --retention-size 1GB
```

Then verify:

- `curl -s http://127.0.0.1:9090/-/ready`

## Troubleshooting (fast path)

- Check service status:
  - `systemctl status grafana-server prometheus prometheus-alertmanager --no-pager -l`
- Check ports are loopback-only:
  - `ss -lntp | grep -E ':3000|:8002|:9090|:9093|:9100|:9187|:9911'`
- Check Prometheus targets:
  - `curl -s http://127.0.0.1:9090/api/v1/targets | head`
