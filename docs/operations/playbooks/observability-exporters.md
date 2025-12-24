# Exporters install playbook (node + Postgres; private; VPS)

Goal: install the minimal exporter set for private observability:

- node exporter (host CPU/mem/disk)
- postgres exporter (DB health)

This playbook keeps exporters **loopback-only** so nothing is exposed publicly.

Canonical boundary doc (read first):

- `../observability-and-private-stats.md`

## Preconditions

- You are on the VPS and can `sudo`.
- Phase 2 scaffold has been applied:
  - `/srv/healtharchive/ops/observability/` exists
  - `/etc/healtharchive/observability/` exists
- Postgres is installed and running locally (production default).

## Procedure (recommended: one command)

1. From the repo on the VPS:

   - `cd /opt/healtharchive-backend`

2. Dry-run first (prints actions):

   - `./scripts/vps-install-observability-exporters.sh`

3. Apply:

   - `sudo ./scripts/vps-install-observability-exporters.sh --apply`

What this does:

- Installs packages (`prometheus-node-exporter`, `prometheus-postgres-exporter`).
- Creates a DB role `postgres_exporter` with `pg_monitor`.
- Writes exporter credentials (root-owned) to:
  - `/etc/healtharchive/observability/postgres_exporter.env`
  - `/etc/healtharchive/observability/postgres_exporter_password`
- Forces exporters to bind only to loopback:
  - `127.0.0.1:9100` and `127.0.0.1:9187`.

## Verify

1. Confirm the metrics endpoints respond locally:

   - `curl -s http://127.0.0.1:9100/metrics | head`
   - `curl -s http://127.0.0.1:9187/metrics | head`

2. Confirm the exporters are loopback-only:

   - `ss -lntp | rg ':9100|:9187' || ss -lntp | grep -E ':9100|:9187'`

   Expect the `Local Address:Port` to be `127.0.0.1:9100` and `127.0.0.1:9187`.

3. Confirm systemd services are active:

   - `systemctl --no-pager status prometheus-node-exporter.service prometheus-postgres-exporter.service || true`

   (Service names may vary slightly by distro; the install script handles common names.)

## Rollback

- Stop + disable exporters:
  - `sudo systemctl disable --now prometheus-node-exporter.service prometheus-postgres-exporter.service || true`
- Remove unit overrides:
  - `sudo rm -rf /etc/systemd/system/prometheus-node-exporter.service.d /etc/systemd/system/prometheus-postgres-exporter.service.d`
  - `sudo systemctl daemon-reload`
- Uninstall packages:
  - `sudo apt-get remove -y prometheus-node-exporter prometheus-postgres-exporter`
- Remove exporter secrets:
  - `sudo rm -f /etc/healtharchive/observability/postgres_exporter.env /etc/healtharchive/observability/postgres_exporter_password`
