# Cleanup automation (safe temp cleanup)

Goal: remove `.tmp*` crawl directories from older indexed jobs without breaking replay.

Canonical refs:

- cleanup command: `ha-backend cleanup-job --mode temp-nonwarc`
- systemd unit templates: `../../deployment/systemd/README.md`
- replay retention note: `../../operations/growth-constraints.md`

## What this does

- Picks indexed jobs older than a minimum age.
- Keeps the latest N per source.
- Runs safe cleanup (`temp-nonwarc`) to preserve WARCs.
- Emits node_exporter metrics:
  - `healtharchive_cleanup_applied_total`

## Enablement (VPS)

```bash
sudo touch /etc/healtharchive/cleanup-automation-enabled
sudo systemctl enable --now healtharchive-cleanup-automation.timer
```

## Manual dry-run

```bash
sudo systemctl start healtharchive-cleanup-automation.service
sudo journalctl -u healtharchive-cleanup-automation.service -n 200 --no-pager
curl -s http://127.0.0.1:9100/metrics | rg '^healtharchive_cleanup_'
```

## If cleanup fails

1. Check the job output directory exists and is readable:
   ```bash
   /opt/healtharchive-backend/.venv/bin/ha-backend show-job --id <JOB_ID>
   ```
2. Run the cleanup command manually:
   ```bash
   /opt/healtharchive-backend/.venv/bin/ha-backend cleanup-job --id <JOB_ID> --mode temp-nonwarc --dry-run
   ```

## Config

Edit `ops/automation/cleanup-automation.toml` to adjust age, caps, and retain count.
