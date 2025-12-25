# Observability dashboards (Grafana provisioning; VPS)

Goal: install the Phase 6 (ops health) and Phase 7 (private usage) dashboards into Grafana in a reproducible way.

Canonical boundary doc (read first):

- `../observability-and-private-stats.md`

## Preconditions

- You are on the VPS and can `sudo`.
- Phase 4 Prometheus is installed and scraping targets are `UP`:
  - `curl -s http://127.0.0.1:9090/-/ready`
- Phase 5 Grafana is installed and reachable on loopback:
  - `curl -s http://127.0.0.1:3000/api/health`
- Grafana data sources exist (configured once in the UI):
  - Prometheus data source named `prometheus` pointing to `http://127.0.0.1:9090`
  - Postgres data source named `grafana-postgresql-datasource` pointing to `127.0.0.1:5432` (DB `healtharchive`, user `grafana_readonly`)

## Procedure

1) From the repo on the VPS:

- `cd /opt/healtharchive-backend`
- `git pull`

2) Dry-run the installer script:

- `./scripts/vps-install-observability-dashboards.sh`

3) Apply:

- `sudo ./scripts/vps-install-observability-dashboards.sh --apply`

This will:

- copy dashboard JSON into `/srv/healtharchive/ops/observability/dashboards/healtharchive/`
- write Grafana provisioning config under `/etc/grafana/provisioning/dashboards/`
- restart Grafana

## Verify

1) In Grafana:

- Go to `Dashboards`
- Find the `HealthArchive` folder
- Open:
  - `HealthArchive - Ops Overview`
  - `HealthArchive - Pipeline Health`
  - `HealthArchive - Search Performance`
  - `HealthArchive - Usage (Private, Aggregate)`
  - `HealthArchive - Impact Summary (Private, Aggregate)`

2) Spot-check queries:

- Prometheus (Explore): `up`
  - Expect 4 series with value `1`.
- Usage (Postgres) dashboards:
  - Expect charts to be empty at first if usage_metrics is sparse; they should not error.

## Troubleshooting

- If Grafana fails to start after provisioning dashboards and you see permission errors for `/srv/healtharchive/ops/observability` in `journalctl`:
  - Ensure the Grafana service user can traverse the ops tree by joining the shared ops group:
    - `sudo usermod -aG healtharchive grafana`
    - `sudo systemctl restart grafana-server.service`
  - Re-run the installer script to confirm permissions and restart:
    - `sudo ./scripts/vps-install-observability-dashboards.sh --apply`

- If dashboards appear but panels show “data source not found”:
  - Rename your Grafana data sources to match the expected names:
    - Prometheus: `prometheus`
    - Postgres: `grafana-postgresql-datasource`
  - Or edit the dashboard JSON under `/srv/healtharchive/ops/observability/dashboards/healtharchive/` and re-run the installer script.

## Rollback

- Remove provisioning config:
  - `sudo rm -f /etc/grafana/provisioning/dashboards/healtharchive.yaml`
- Remove dashboard JSON:
  - `sudo rm -rf /srv/healtharchive/ops/observability/dashboards/healtharchive`
- Restart Grafana:
  - `sudo systemctl restart grafana-server.service`
