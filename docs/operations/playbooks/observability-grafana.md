# Grafana install + tailnet access playbook (private; VPS)

Goal: install Grafana as the operator-only “private stats page”, reachable only
on the tailnet.

Canonical boundary doc (read first):

- `../observability-and-private-stats.md`

## Preconditions

- You are on the VPS and can `sudo`.
- Phase 4 Prometheus is installed and loopback-only on `127.0.0.1:9090`.
- Tailscale is installed and the VPS is connected to your tailnet.
- You have set these secret files:
  - `/etc/healtharchive/observability/grafana_admin_password`
  - `/etc/healtharchive/observability/postgres_grafana_password`

## Procedure

### 1) Install and harden Grafana (loopback-only)

From the repo on the VPS:

- `cd /opt/healtharchive-backend`

Dry-run:

- `./scripts/vps-install-observability-grafana.sh`

Apply:

- `sudo ./scripts/vps-install-observability-grafana.sh --apply`

Note: on Ubuntu, the `grafana` package is not always available in the default apt sources; the installer script will add the Grafana Labs apt repo automatically when needed.

This will:

- bind Grafana to `127.0.0.1:3000` (not public)
- disable anonymous access + signups
- reset the Grafana admin password
- create/update Postgres role `grafana_readonly` with minimal read access for dashboards

### 2) Expose Grafana via tailnet-only HTTPS (Tailscale Serve)

Dry-run:

- `./scripts/vps-enable-tailscale-serve-grafana.sh`

Apply:

- `sudo ./scripts/vps-enable-tailscale-serve-grafana.sh --apply`

Note: Tailscale has changed the `tailscale serve` CLI over time; the script detects the installed version and uses the compatible command form.

Note: this script also checks that Grafana is reachable on loopback first (via `http://127.0.0.1:3000/api/health`) to avoid hanging on misconfigured systems.

Then run:

- `sudo tailscale serve status`

Copy the HTTPS URL and open it from an operator machine that is on the tailnet.

Troubleshooting:

- If you see: `Serve is not enabled on your tailnet`, enable it in the Tailscale admin console (the error message includes a link), then re-run the script.
- If you don’t want to enable Serve yet, use an SSH port-forward over Tailscale instead:
  - `ssh -L 3000:127.0.0.1:3000 haadmin@<tailscale-host>`
  - Then open `http://127.0.0.1:3000`

## Configure data sources (Grafana UI)

This plan keeps secrets out of git and does **not** provision data sources yet.
Configure them once in the UI:

1) Prometheus:

- URL: `http://127.0.0.1:9090`

2) Postgres (read-only):

- Host: `127.0.0.1:5432`
- Database: `healtharchive`
- User: `grafana_readonly`
- Password: value from `/etc/healtharchive/observability/postgres_grafana_password`
- TLS/SSL mode: disable (local loopback)

## Verify

- Grafana is not publicly reachable (it binds to loopback):
  - `ss -lntp | grep -E ':3000\b'`
  - Expect `127.0.0.1:3000`
- Grafana is reachable via tailnet HTTPS:
  - open the `tailscale serve status` URL from a tailnet-connected machine
- Prometheus data source test succeeds.
- Postgres data source test succeeds.

## Rollback

- Remove tailnet exposure:
  - `sudo tailscale serve reset`
- Stop + disable Grafana:
  - `sudo systemctl disable --now grafana-server.service`
- Remove overrides:
  - `sudo rm -rf /etc/systemd/system/grafana-server.service.d`
  - `sudo systemctl daemon-reload`
