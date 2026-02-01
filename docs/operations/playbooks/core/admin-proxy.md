# Admin proxy (browser-friendly ops triage; VPS)

Goal: make it easy to browse admin/metrics endpoints in a browser **without** copying tokens into the browser.

This is a lightweight alternative to building a bespoke admin UI.

## Design

- Runs a tiny reverse proxy on the VPS, bound to loopback only (`127.0.0.1`).
- Proxies **read-only GET** requests to:
  - `/api/admin/**`
  - `/metrics`
- Adds the backend admin token server-side from:
  - `/etc/healtharchive/observability/prometheus_backend_admin_token`
- You access it from your laptop via SSH port-forwarding (tailnet-only SSH).

Security notes:

- No new public ports.
- Browser never sees the admin token.
- Anyone with shell access to the VPS can reach `127.0.0.1`, so treat VPS access as privileged.

## Install / apply (VPS)

1) Pull the latest repo:

- `cd /opt/healtharchive-backend`
- `git pull`

2) Dry-run:

- `./scripts/vps-install-ops-admin-proxy.sh`

3) Apply:

- `sudo ./scripts/vps-install-ops-admin-proxy.sh --apply`

## Verify (VPS)

- `curl -s http://127.0.0.1:8002/-/health`
- `curl -s http://127.0.0.1:8002/api/admin/jobs?limit=1 | head`
- `curl -s http://127.0.0.1:8002/metrics | head`

## Use from your laptop (SSH port-forward)

1) Start a tunnel:

- `ssh -N -L 8002:127.0.0.1:8002 haadmin@<vps-tailscale-ip>`

2) Open in your browser:

- `http://127.0.0.1:8002/`

Useful endpoints:

- `http://127.0.0.1:8002/api/admin/jobs`
- `http://127.0.0.1:8002/api/admin/jobs/status-counts`
- `http://127.0.0.1:8002/api/admin/reports`
- `http://127.0.0.1:8002/api/admin/search-debug?q=covid`
- `http://127.0.0.1:8002/metrics`

## Rollback

- `sudo systemctl disable --now healtharchive-admin-proxy.service`
- `sudo rm -f /etc/systemd/system/healtharchive-admin-proxy.service`
- `sudo rm -f /usr/local/bin/healtharchive-admin-proxy`
- `sudo systemctl daemon-reload`
