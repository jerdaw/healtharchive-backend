# Prometheus install playbook (private; VPS)

Goal: install Prometheus and configure it to scrape HealthArchive metrics **privately**
via loopback-only targets.

Canonical boundary doc (read first):

- `../observability-and-private-stats.md`

## Preconditions

- You are on the VPS and can `sudo`.
- Phase 2 scaffold exists:
  - `/etc/healtharchive/observability/prometheus_backend_admin_token` is set to the backend `HEALTHARCHIVE_ADMIN_TOKEN`.
- Phase 3 exporters are installed and loopback-only:
  - node exporter: `127.0.0.1:9100`
  - postgres exporter: `127.0.0.1:9187` and `pg_up 1`
- Backend API is reachable locally:
  - `curl -s http://127.0.0.1:8001/api/health | head`

## Procedure

1. From the repo on the VPS:

   - `cd /opt/healtharchive-backend`

2. Dry-run first:

   - `./scripts/vps-install-observability-prometheus.sh`

3. Apply:

   - `sudo ./scripts/vps-install-observability-prometheus.sh --apply`

This will:

- install the `prometheus` package
- write `/etc/prometheus/prometheus.yml`
- force Prometheus to listen on `127.0.0.1:9090` via a systemd override
- cap retention (time; and size if supported)

## Verify

1. Confirm Prometheus is up:

   - `curl -s http://127.0.0.1:9090/-/ready`

2. Confirm it is loopback-only:

   - `ss -lntp | grep -E ':9090\b'`

   Expect `127.0.0.1:9090`.

3. Confirm scrape targets are `UP`:

   - `curl -s http://127.0.0.1:9090/api/v1/targets | head`

   Optional (if you have `jq` installed):

   - `curl -s http://127.0.0.1:9090/api/v1/targets | jq '.data.activeTargets[] | {job: .labels.job, scrapeUrl: .scrapeUrl, health: .health, lastError: .lastError}'`

4. Confirm the backend scrape is working:

   - `curl -s "http://127.0.0.1:9090/api/v1/query?query=up%7Bjob%3D%22healtharchive_backend%22%7D" | head`

## Rollback

- Disable Prometheus:
  - `sudo systemctl disable --now prometheus.service`
- Remove the override:
  - `sudo rm -rf /etc/systemd/system/prometheus.service.d`
  - `sudo systemctl daemon-reload`
- Remove package (optional):
  - `sudo apt-get remove -y prometheus`
