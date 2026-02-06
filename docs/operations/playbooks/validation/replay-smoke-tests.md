# Replay smoke tests (daily replay validation)

Goal: confirm replay is serving real content for the latest indexed jobs.

Canonical refs:

- replay runbook: `../../../deployment/replay-service-pywb.md`
- systemd unit templates: `../../../deployment/systemd/README.md`

## What this does

- Picks the latest indexed job per source (including canary).
- Uses the first seed URL as a replay target (or falls back to the source registry defaults for legacy jobs that lack seeds in `ArchiveJob.config`):
  - `https://replay.healtharchive.ca/job-<id>/<seed>`
- Emits node_exporter textfile metrics:
  - `healtharchive_replay_smoke_target_present{source="hc"}`
  - `healtharchive_replay_smoke_ok{source="hc",job_id="123"}`
  - `healtharchive_replay_smoke_canary_ok` (1 if canary passes, 0 otherwise)

## Canary job

The `hc_canary` source is a small, local-only job (2 pages, ~5 MB) that:
- **Never gets tiered** to Storage Box (always stays on root disk)
- **Baselines pywb health** vs storage tiering health

**Metric interpretation:**

- `canary_ok=1, prod_ok=0` → **Storage tiering issue** (annual job WARCs inaccessible)
- `canary_ok=0, prod_ok=0` → **pywb service issue** (replay service down or broken)
- `canary_ok=1, prod_ok=1` → All healthy

**Creating the canary:**

```bash
ha-backend create-canary-job
```

This is idempotent and safe to re-run.

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
3. If replay is up (`/` is `200`) but smoke requests return `503`, suspect WARC/mount access (often after `sshfs`/tiering incidents).

   1) Ensure the WARC tiering unit is not stuck in a failed state:
   ```bash
   systemctl is-failed healtharchive-warc-tiering.service && sudo systemctl reset-failed healtharchive-warc-tiering.service || true
   sudo systemctl start healtharchive-warc-tiering.service
   sudo systemctl status healtharchive-warc-tiering.service --no-pager -l | sed -n '1,120p'
   ```

   2) Restart replay to refresh its view of `/srv/healtharchive/jobs`:
   ```bash
   sudo systemctl restart healtharchive-replay.service
   ```

   3) Re-run smoke:
   ```bash
   sudo systemctl start healtharchive-replay-smoke.service
   curl -s http://127.0.0.1:9100/metrics | rg '^healtharchive_replay_smoke_'
   ```

4. If replay is up but a source still fails, re-run replay reconcile:
   ```bash
   sudo systemctl start healtharchive-replay-reconcile.service
   ```

## Config

Edit `ops/automation/replay-smoke.toml` to adjust timeouts or sources.
