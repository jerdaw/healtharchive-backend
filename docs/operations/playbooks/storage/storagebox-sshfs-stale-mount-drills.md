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

## 2) Drill: persistent failed-apply alert condition (safe, no paging)

Goal: validate the Phase 2 alert condition logic for
`HealthArchiveStorageHotpathApplyFailedPersistent` **without** writing anything
to the live node_exporter collector path and without triggering notifications.

1) Create a synthetic metrics file under `/tmp` (not the collector directory):

```bash
cat >/tmp/healtharchive_storage_hotpath_auto_recover.alertcheck.prom <<'EOF'
healtharchive_storage_hotpath_auto_recover_enabled 1
healtharchive_storage_hotpath_auto_recover_apply_total 3
healtharchive_storage_hotpath_auto_recover_last_apply_ok 0
healtharchive_storage_hotpath_auto_recover_last_apply_timestamp_seconds 0
EOF
```

2) Evaluate the alert predicate locally (safe/offline):

```bash
python3 - <<'PY'
import time
from pathlib import Path

metrics = {}
for line in Path("/tmp/healtharchive_storage_hotpath_auto_recover.alertcheck.prom").read_text().splitlines():
    line = line.strip()
    if not line or line.startswith("#"):
        continue
    k, v = line.split(None, 1)
    metrics[k] = float(v)

ok = (
    metrics.get("healtharchive_storage_hotpath_auto_recover_enabled", 0) == 1
    and metrics.get("healtharchive_storage_hotpath_auto_recover_apply_total", 0) > 0
    and metrics.get("healtharchive_storage_hotpath_auto_recover_last_apply_ok", 1) == 0
    and (time.time() - metrics.get("healtharchive_storage_hotpath_auto_recover_last_apply_timestamp_seconds", time.time())) > 86400
)
print("ALERT_CONDITION_TRUE" if ok else "ALERT_CONDITION_FALSE")
PY
```

Expected output: `ALERT_CONDITION_TRUE`.

3) Clean up:

```bash
rm -f /tmp/healtharchive_storage_hotpath_auto_recover.alertcheck.prom
```

Optional syntax check (safe):

```bash
promtool check rules /opt/healtharchive-backend/ops/observability/alerting/healtharchive-alerts.yml
```

## 3) Drill: alert pipeline (no paging)

This validates Prometheus rule loading + Alertmanager ingestion without sending notifications.

Precondition:

- Alertmanager routes `severity="drill"` to a null receiver. This is handled by the repo installer:
  - `scripts/vps-install-observability-alerting.sh`

### 3.1 Trigger the drill alert metric (auto-cleanup)

```bash
cd /opt/healtharchive-backend
sudo ./scripts/vps-alert-pipeline-drill.sh --apply --duration-seconds 600
```

### 3.2 Confirm Prometheus sees the alert

```bash
curl -s http://127.0.0.1:9090/api/v1/alerts | rg 'HealthArchiveAlertPipelineDrill' || true
```

### 3.3 Confirm Alertmanager received the alert (but does not notify)

```bash
curl -s http://127.0.0.1:9093/api/v2/alerts | rg 'HealthArchiveAlertPipelineDrill' || true
```

After ~10 minutes, the script removes the metric file and the alert should resolve.

If you ever need to clean up manually:

```bash
sudo rm -f /var/lib/node_exporter/textfile_collector/healtharchive_alert_pipeline_drill.prom
```

## 4) Full recovery drill (staging or scheduled maintenance only)

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

## 5) Phase 4 rollout and burn-in evidence capture

Use this during the first week after shipping watchdog/alert updates.

### 5.1 Daily snapshot (safe)

```bash
cd /opt/healtharchive-backend
python3 scripts/vps-storage-watchdog-burnin-report.py --json > /tmp/storage-watchdog-burnin-$(date -u +%Y%m%d).json
cat /tmp/storage-watchdog-burnin-$(date -u +%Y%m%d).json
```

Optional (recommended): enable the daily snapshot timer so you don’t rely on a
human remembering.

```bash
cd /opt/healtharchive-backend
sudo ./scripts/vps-bootstrap-ops-dirs.sh
sudo install -m 0644 -o root -g root /dev/null /etc/healtharchive/storage-watchdog-burnin-enabled
sudo systemctl enable --now healtharchive-storage-watchdog-burnin-snapshot.timer
```

Artifacts are written under:

- `/srv/healtharchive/ops/burnin/storage-watchdog/latest.json`
- `/srv/healtharchive/ops/burnin/storage-watchdog/storage-watchdog-burnin-YYYYMMDD.json`

Expected:

- `status` is usually `ok`.
- `status=warn` means stale targets are currently detected (`detectedTargetsNow=true`) and should be triaged.
- `status=fail` means persistent failed-apply or metrics writer failure and requires immediate triage.

### 5.2 End-of-week clean check gate

```bash
cd /opt/healtharchive-backend
python3 scripts/vps-storage-watchdog-burnin-report.py --window-hours 168 --require-clean
```

Expected exit code:

- `0`: `status=ok` (no persistent failed-apply signal and no currently detected targets).
- `1`: `status=warn` or `status=fail`; investigate before escalation.
