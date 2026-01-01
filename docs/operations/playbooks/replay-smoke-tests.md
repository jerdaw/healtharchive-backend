# Replay smoke tests (daily replay validation)

Goal: confirm replay is serving real content for the latest indexed jobs.

Canonical refs:

- replay runbook: `../../deployment/replay-service-pywb.md`
- systemd unit templates: `../../deployment/systemd/README.md`

## What this does

- Picks the latest indexed job per source.
- Uses the first seed URL as a replay target:
  - `https://replay.healtharchive.ca/job-<id>/<seed>`
- Emits node_exporter textfile metrics:
  - `healtharchive_replay_smoke_ok{source="hc"}`

## Enablement (VPS)

```bash
sudo touch /etc/healtharchive/replay-smoke-enabled
sudo systemctl enable --now healtharchive-replay-smoke.timer
```

## Manual check

```bash
sudo systemctl start healtharchive-replay-smoke.service
sudo journalctl -u healtharchive-replay-smoke.service -n 200 --no-pager
curl -s http://127.0.0.1:9100/metrics | rg '^healtharchive_replay_smoke_'
```

## If an alert fires

1. Confirm replay is enabled:
   ```bash
   rg -n 'HEALTHARCHIVE_REPLAY_BASE_URL' /etc/healtharchive/backend.env
   ```
2. Confirm replay service health:
   ```bash
   sudo systemctl status healtharchive-replay.service --no-pager -l
   curl -I https://replay.healtharchive.ca/ | head
   ```
3. If replay is up but a source fails, re-run replay reconcile:
   ```bash
   sudo systemctl start healtharchive-replay-reconcile.service
   ```

## Config

Edit `ops/automation/replay-smoke.toml` to adjust timeouts or sources.
