# Observability alerting (Prometheus + Alertmanager; VPS)

Goal: get notified about real outages without creating pager fatigue.

Canonical boundary doc (read first):

- `../observability-and-private-stats.md`

## Design (low maintenance)

- Prometheus evaluates a small set of **high-signal** rules.
- Alertmanager routes alerts to **one operator channel** (webhook).
- No new public ports: Alertmanager binds to `127.0.0.1:9093`.

Why this approach:

- Fully reproducible “config-as-code” (scripted install + deterministic config files).
- Avoids Grafana alerting provisioning complexity (datasource UIDs, version drift).

## Preconditions

- You are on the VPS and can `sudo`.
- Prometheus is installed and scraping targets:
  - `curl -s http://127.0.0.1:9090/-/ready`
- Node exporter is installed (disk metrics):
  - `curl -s http://127.0.0.1:9100/metrics | head`
- If you use WARC tiering to a Storage Box, enable the tiering metrics writer timer so
  Prometheus can alert on mount/tiering failures:
  - `sudo systemctl enable --now healtharchive-tiering-metrics.timer`

## Choose your operator channel (webhook)

Create a webhook URL for your operator channel (examples):

- Discord channel webhook
- Slack incoming webhook
- Any HTTPS endpoint that accepts Alertmanager webhook JSON

Store it on the VPS:

- `sudoedit /etc/healtharchive/observability/alertmanager_webhook_url`

Keep this URL private (it is effectively a secret).

### Pushover (cleaner/safer pattern)

Pushover does not accept Alertmanager’s webhook JSON directly. Use the included
loopback-only relay so secrets stay under `/etc/healtharchive/observability/`.

1) Put your Pushover secrets on the VPS:

- `sudoedit /etc/healtharchive/observability/pushover_app_token`
- `sudoedit /etc/healtharchive/observability/pushover_user_key`

2) Install the relay:

- `sudo ./scripts/vps-install-observability-pushover-relay.sh --apply`

3) Point Alertmanager at the relay:

- `sudoedit /etc/healtharchive/observability/alertmanager_webhook_url`
- Set the single line to:
  - `http://127.0.0.1:9911/alertmanager`

## Install / apply

1) Pull latest repo on the VPS:

- `cd /opt/healtharchive-backend`
- `git pull`

2) Dry-run:

- `./scripts/vps-install-observability-alerting.sh`

3) Apply:

- `sudo ./scripts/vps-install-observability-alerting.sh --apply`

Optional: if your storage is not on `/`, set the mountpoint explicitly:

- `sudo ./scripts/vps-install-observability-alerting.sh --apply --mountpoint /`

## Verify

1) Alertmanager is up:

- `curl -s http://127.0.0.1:9093/-/ready`

2) Prometheus loaded rules:

- `curl -s http://127.0.0.1:9090/api/v1/rules | head`

3) Confirm loopback-only:

- `ss -lntp | grep -E ':9093\\b|:9090\\b'`

## Test delivery (recommended)

If `amtool` is installed:

- `amtool alert add HealthArchiveTestAlert severity=warning service=healtharchive`

Then confirm the test notification arrives in your operator channel.

If `amtool` is not installed, you can still confirm Alertmanager is receiving alerts in its UI
by SSH port-forwarding:

- `ssh -N -L 9093:127.0.0.1:9093 haadmin@<vps-tailscale-ip>`
- Open `http://127.0.0.1:9093/`

## Alert set (what you get)

Installed rules (minimal, high-signal):

- Backend scrape down (>5m)
- Disk usage >80% (warning) and >90% (critical) on the selected filesystem
- Sustained `/api/search` errors (traffic-gated)
- Job failures increased (failed/index_failed count delta > 0 over 30m)
- Storage Box mount down (if tiering is enabled and metrics are present)
- WARC tiering bind-mount service failed (if tiering is enabled and metrics are present)

## Rollback

- Disable Alertmanager:
  - `sudo systemctl disable --now prometheus-alertmanager.service || sudo systemctl disable --now alertmanager.service`
- Remove the rules file:
  - `sudo rm -f /etc/prometheus/rules/healtharchive-alerts.yml`
- Restart Prometheus:
  - `sudo systemctl restart prometheus.service`
