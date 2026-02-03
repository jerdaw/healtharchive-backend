# Storage Box / `sshfs` recovery drills (safe on production)

Goal: periodically prove that:

- the **watchdog logic** would take the right recovery actions, without actually touching mounts, and
- the **alert pipeline** (Prometheus → Alertmanager) is wired correctly, without paging operators.

These drills are designed to be safe on production **even mid-crawl**.

Canonical background:

- Roadmap context: `../../../planning/implemented/2026-01-08-storagebox-sshfs-stale-mount-recovery-and-integrity.md`
- Real incident recovery procedure: `storagebox-sshfs-stale-mount-recovery.md`

## 0) Safety rules

- **Never run recovery automation with `--apply` as part of a drill.**
- For drills, always use:
  - a temporary `--state-file` and `--lock-file` under `/tmp`
  - a temporary `--textfile-out-dir` under `/tmp`
  so you don’t affect production watchdog state or Prometheus metrics.

## 1) Drill: watchdog planned actions (dry-run simulation)

This validates the Phase 2 watchdog logic without breaking mounts.

1) Pick a real “hot path” to simulate as stale.

Good candidates:

- an annual job output dir: `/srv/healtharchive/jobs/<source>/<job_dir>`
- an imports hot path from tiering: `/srv/healtharchive/jobs/imports/...`

2) Run the watchdog in dry-run simulation mode (do not use `--apply`):

```bash
cd /opt/healtharchive-backend
sudo bash -lc 'set -a; source /etc/healtharchive/backend.env; set +a; \
  /opt/healtharchive-backend/.venv/bin/python3 /opt/healtharchive-backend/scripts/vps-storage-hotpath-auto-recover.py \
    --confirm-runs 1 \
    --min-failure-age-seconds 0 \
    --state-file /tmp/healtharchive-storage-hotpath-drill.state.json \
    --lock-file /tmp/healtharchive-storage-hotpath-drill.lock \
    --textfile-out-dir /tmp \
    --textfile-out-file healtharchive_storage_hotpath_auto_recover.drill.prom \
    --simulate-broken-path /srv/healtharchive/jobs/hc/<JOB_DIR>'
```

3) Confirm output includes:

- `DRILL: simulate-broken-path active`
- `Planned actions (dry-run):`
- a sensible sequence (stop worker → unmount stale mountpoints → re-apply tiering → recover stale jobs → start worker)

If this looks wrong, fix the watchdog logic **before** enabling the production timer.

## 2) Drill: alert pipeline (no paging)

This validates Prometheus rule loading + Alertmanager ingestion without sending notifications.

Precondition:

- Alertmanager routes `severity="drill"` to a null receiver. This is handled by the repo installer:
  - `scripts/vps-install-observability-alerting.sh`

### 2.1 Trigger the drill alert metric (auto-cleanup)

```bash
cd /opt/healtharchive-backend
sudo ./scripts/vps-alert-pipeline-drill.sh --apply --duration-seconds 600
```

### 2.2 Confirm Prometheus sees the alert

```bash
curl -s http://127.0.0.1:9090/api/v1/alerts | rg 'HealthArchiveAlertPipelineDrill' || true
```

### 2.3 Confirm Alertmanager received the alert (but does not notify)

```bash
curl -s http://127.0.0.1:9093/api/v2/alerts | rg 'HealthArchiveAlertPipelineDrill' || true
```

After ~10 minutes, the script removes the metric file and the alert should resolve.

If you ever need to clean up manually:

```bash
sudo rm -f /var/lib/node_exporter/textfile_collector/healtharchive_alert_pipeline_drill.prom
```

## 3) Full recovery drill (staging or scheduled maintenance only)

Only do this on:

- a staging VPS (preferred), or
- a production maintenance window where crawl interruption is acceptable.

High-level steps:

1) Ensure you can tolerate crawl interruption (stop the worker first).
2) Intentionally create a stale mount condition (Errno 107) on a **dedicated test hot path**.
3) Confirm:
   - alerts fire (`HealthArchiveTieringHotPathUnreadable` / `HealthArchiveStorageBoxMountDown`)
   - watchdog recovers (tiering re-apply, stale job recovery)
   - worker resumes and jobs make progress
4) Run post-incident integrity checks:
   - `ha-backend verify-warcs --job-id <ID> --level 1 --since-minutes <window>`
   - follow `warc-integrity-verification.md` if anything fails
