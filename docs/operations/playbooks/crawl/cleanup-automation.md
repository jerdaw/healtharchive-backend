# Cleanup automation (safe temp cleanup)

Goal: remove `.tmp*` crawl directories from older indexed jobs without breaking replay.

Canonical refs:

- cleanup command: `ha-backend cleanup-job --mode temp-nonwarc`
- systemd unit templates: `../../../deployment/systemd/README.md`
- replay retention note: `../../growth-constraints.md`

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

Warning: starting `healtharchive-cleanup-automation.service` will **apply** cleanup (it is the automation entrypoint).
Use the script directly for a dry-run preview.

```bash
sudo bash -lc 'set -a; source /etc/healtharchive/backend.env; set +a; /opt/healtharchive-backend/.venv/bin/python3 /opt/healtharchive-backend/scripts/vps-cleanup-automation.py --config /opt/healtharchive-backend/ops/automation/cleanup-automation.toml --out-dir /tmp --out-file healtharchive_cleanup_dryrun.prom'
cat /tmp/healtharchive_cleanup_dryrun.prom
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
