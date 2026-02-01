# Implementation Plan: Operational Resilience Improvements

**Created**: 2026-02-01
**Status**: Active
**Focus**: Address recurring operational issues: stale mounts, disk pressure, crawl stalls

---

## Problem Statement

Based on production observations from the 2026 annual campaign:

1. **Stale SSHFS mounts** recur despite existing auto-recovery
   - Jobs 7 (PHAC) and 8 (CIHR) currently have Errno 107 stale mounts
   - Storage hot-path watchdog cannot tier/recover due to stale state
   - Root cause unknown; the base SSHFS mount has hardened options but stale mounts still occur

2. **Disk pressure** blocks crawl progress
   - Currently at 87% (above 85% worker threshold)
   - Worker correctly refuses to start new jobs, but no proactive cleanup
   - No event-driven cleanup triggers

3. **Excessive crawl stalls** cause recovery fatigue
   - HC job has had 31 auto-recoveries since 2026-01-01
   - No distinction between recoverable stalls (network) vs non-recoverable (scope issues)
   - Existing watchdog works but may be hitting caps or cooldowns

4. **Operational visibility** is fragmented
   - Each watchdog has Prometheus metrics but no consolidated operator view
   - Threshold rationale is scattered across docs

---

## Existing Implementation (Already Done)

### Stale Mount Recovery - `vps-storage-hotpath-auto-recover.py`
- **Detection**: Probes running job output dirs, next queued/retryable jobs, manifest hot paths for Errno 107
- **Observation tracking**: Requires 2 consecutive runs + 120s min age before acting
- **Rate limiting**: 15min cooldown, 2/hour cap, 6/day global cap, 3/job/day cap
- **Recovery sequence**: Stop worker → unmount stale → restart storagebox → re-apply tiering → recover jobs → restart worker/replay
- **Drill mode**: `--simulate-broken-path` for testing

### SSHFS Mount Hardening - `healtharchive-storagebox-sshfs.service`
- Already has: `reconnect`, `ServerAliveInterval=15`, `ServerAliveCountMax=3`, `kernel_cache`
- Restart on failure with 5s delay

### Crawl Stall Recovery - `vps-crawl-auto-recover.py`
- **Stall detection**: Parses crawl log for progress, 5400s (90min) threshold
- **Safety guards**: Skip if another job has progress within 600s, 3/job/day cap
- **Soft recovery**: Mark stalled job retryable without stopping worker (if another job is healthy)
- **Auto-config**: Patches job with self-healing options before retry (enable_adaptive_restart, max_container_restarts=20, etc.)

### Cleanup Automation - `vps-cleanup-automation.py`
- Config-driven: min_age_days, keep_latest_per_source, max_jobs_per_run
- Safe cleanup via `temp-nonwarc` mode (preserves WARCs)
- Timer-based execution

### Worker Disk Check - `worker/main.py`
- Pre-crawl check at 85% threshold
- Skips job selection if disk too full

---

## What's Actually Missing

| Gap | Impact | Priority |
|-----|--------|----------|
| No disk threshold cleanup trigger | Jobs blocked until manual cleanup | P1 |
| No unified watchdog status view | Operators must check 3+ JSON files | P2 |
| Stale mount root cause unknown | Recoveries happen but issue recurs | P1 |
| No stall type classification | All stalls treated equally | P3 |
| Threshold rationale undocumented | Tuning requires archaeology | P2 |

---

## Phase 1: Immediate Remediation (Operator Actions)

These are manual steps for the current situation. No code changes.

### 1.1 Clear Stale Mounts (Jobs 7 & 8)

```bash
# On VPS - stop worker first
sudo systemctl stop healtharchive-worker

# Identify stale mounts
findmnt -t fuse.sshfs | grep healtharchive
mount | grep "/srv/healtharchive/jobs"

# Lazy unmount stale paths
sudo umount -l /srv/healtharchive/jobs/phac/20260101T000502Z__phac-20260101
sudo umount -l /srv/healtharchive/jobs/cihr/20260101T000502Z__cihr-20260101

# Verify mounts cleared
ls -la /srv/healtharchive/jobs/phac/20260101T000502Z__phac-20260101
ls -la /srv/healtharchive/jobs/cihr/20260101T000502Z__cihr-20260101

# Re-apply tiering with repair flag
sudo /opt/healtharchive-backend/scripts/vps-warc-tiering-bind-mounts.sh \
  --apply --repair-stale-mounts

# Run annual tiering with repair
sudo -u haadmin bash -lc 'set -a; source /etc/healtharchive/backend.env; set +a; \
  /opt/healtharchive-backend/.venv/bin/python3 \
  /opt/healtharchive-backend/scripts/vps-annual-output-tiering.py \
  --apply --repair-stale-mounts --year 2026'

# Restart worker
sudo systemctl start healtharchive-worker
```

**Verification**: Run `vps-crawl-status.sh --year 2026` and confirm no Errno 107 errors.

### 1.2 Free Disk Space

```bash
# Check what's consuming space
du -sh /srv/healtharchive/jobs/*/20260101* 2>/dev/null | sort -h

# Run Docker cleanup
docker system prune -a -f

# Check journal size and vacuum if needed
sudo journalctl --disk-usage
sudo journalctl --vacuum-size=200M

# Check container logs
sudo find /var/lib/docker/containers -name "*.log" -size +50M -exec ls -lh {} \;

# Verify disk usage dropped
df -h /
```

**Target**: Get below 85% to unblock worker job selection.

### 1.3 Check Why Watchdog Isn't Auto-Recovering

```bash
# Check storage hotpath watchdog state
cat /srv/healtharchive/ops/watchdog/storage-hotpath-auto-recover.json | jq .

# Look for rate limiting
# - last_apply_utc vs now (15min cooldown)
# - recoveries.global count in last hour/day
# - deploy lock active?

# Check crawl auto-recover state
cat /srv/healtharchive/ops/watchdog/crawl-auto-recover.json | jq .

# Run watchdogs manually in dry-run to see what they'd do
sudo -u haadmin bash -lc 'set -a; source /etc/healtharchive/backend.env; set +a; \
  /opt/healtharchive-backend/.venv/bin/python3 \
  /opt/healtharchive-backend/scripts/vps-storage-hotpath-auto-recover.py'
```

**Document findings**: Why is manual intervention still needed? Caps? Cooldowns? Deploy lock?

---

## Phase 2: Proactive Disk Management (NEW)

### 2.1 Disk Threshold Cleanup Trigger

**Problem**: Current cleanup is timer-only (runs on schedule). When disk hits 85%, jobs are blocked until someone intervenes.

**Solution**: Add threshold-triggered cleanup mode.

**Files to modify**:
- `scripts/vps-cleanup-automation.py` - Add `--threshold-mode` flag
- `ops/automation/cleanup-automation.toml` - Add threshold config

**Design**:
```python
# Add to vps-cleanup-automation.py

def check_disk_usage() -> int:
    """Return disk usage percentage for /srv/healtharchive."""
    st = os.statvfs("/srv/healtharchive")
    used = st.f_blocks - st.f_bavail
    return int(100 * used / st.f_blocks)

# In main(), add threshold mode:
if args.threshold_mode:
    usage = check_disk_usage()
    if usage < config.threshold_trigger_percent:  # e.g., 80%
        print(f"Disk at {usage}%, below threshold {config.threshold_trigger_percent}%. Skipping.")
        return 0
    print(f"Disk at {usage}%, above threshold. Running cleanup.")
    # Continue with normal cleanup logic, but increase max_jobs_per_run temporarily
```

**New systemd timer**: `healtharchive-disk-threshold-cleanup.timer`
- Runs every 30 minutes
- Only cleans if disk > 80%
- More aggressive than regular cleanup (clean up to 5 jobs per run)

**Config additions** (cleanup-automation.toml):
```toml
# Threshold-triggered cleanup
threshold_trigger_percent = 80
threshold_max_jobs_per_run = 5
```

**Verification**:
- `make ci` passes
- Timer runs without errors
- Disk stays below 85% during normal operations

### 2.2 Worker Disk Check Enhancement

**Problem**: Worker silently skips jobs when disk is full. No notification.

**Solution**: Add warning log and metric.

**Files to modify**:
- `src/ha_backend/worker/main.py`

**Changes**:
```python
# After disk check in run_worker_loop
if not has_headroom:
    log.warning(
        "Disk usage at %d%%, above %d%% threshold. "
        "Skipping job selection. Consider running cleanup or freeing space.",
        usage, DISK_HEADROOM_THRESHOLD_PERCENT
    )
```

**Verification**: Worker logs show clear message when disk blocks job selection.

---

## Phase 3: Stale Mount Root Cause Investigation

### 3.1 Gather Diagnostic Data

**Problem**: Stale mounts keep happening but we don't know why.

**Investigation tasks** (operator actions):

1. **Check Storage Box connection limits**:
   ```bash
   # How many sshfs connections from this host?
   ss -tn | grep 23 | wc -l

   # Check Hetzner Storage Box documentation for connection limits
   ```

2. **Check network stability during stale periods**:
   ```bash
   # Add to cron: log connectivity every 5 min
   ping -c 1 u524803.your-storagebox.de >> /var/log/storagebox-ping.log
   ```

3. **Check if bind mounts survive base mount restart**:
   - The hot-path bind mounts are mounted on top of directories
   - If the base sshfs mount becomes stale, the bind mounts may also become stale
   - But restarting sshfs may not fix the bind mounts automatically

4. **Correlation with other events**:
   - Do stale mounts correlate with Docker restarts?
   - Network interface changes?
   - VPS maintenance windows?

**Document findings** in: `docs/operations/incidents/2026-02-stale-mount-investigation.md`

### 3.2 Improve Watchdog Logging

**Problem**: Watchdog logs don't capture enough context about why mounts went stale.

**Solution**: Add diagnostic logging when stale mounts are detected.

**Files to modify**:
- `scripts/vps-storage-hotpath-auto-recover.py`

**Changes**:
```python
# When stale mount detected, log more context
def _log_stale_mount_diagnostics(path: str, job_id: int | None) -> None:
    """Log diagnostic info when a stale mount is detected."""
    # Check base sshfs mount status
    storagebox_ok, errno = _probe_readable_dir(Path("/srv/healtharchive/storagebox"))
    # Check if path is a bind mount vs direct sshfs mount
    mount_info = _get_mount_info(path)
    # Log network connectivity
    # ...
```

---

## Phase 4: Unified Monitoring (NEW)

### 4.1 Watchdog Status CLI Command

**Problem**: Operators must check 3+ JSON files and Prometheus metrics separately.

**Solution**: Add `ha-backend watchdog-status` command.

**Files to create/modify**:
- `src/ha_backend/cli.py` - Add `watchdog-status` subcommand

**Output design**:
```
HealthArchive Watchdog Status
=============================
Timestamp: 2026-02-01T12:30:00Z

[Crawl Auto-Recovery]
  Enabled:     Yes (sentinel present)
  Last run:    2026-02-01T12:13:10Z (17 min ago)
  Result:      skip (no_stalled_jobs)
  Running:     1 job(s)
  Stalled:     0 job(s)
  Recoveries:  64 total, 0 today (cap: 3/job/day)

[Storage Hot-Path Recovery]
  Enabled:     Yes (sentinel present)
  Last run:    2026-02-01T12:13:10Z (17 min ago)
  Result:      skip (deploy_lock)
  Detected:    2 stale target(s)
  Last apply:  2026-01-24T06:28:01Z (FAILED)
  Error:       stale mountpoint (Errno 107) at output_dir=...

[Disk Cleanup]
  Enabled:     Yes (config present)
  Last run:    2026-01-28T00:00:00Z
  Candidates:  5 eligible
  Applied:     2 cleaned

[Current Health]
  Disk usage:  87% [ABOVE 85% THRESHOLD]
  Base sshfs:  OK (readable)
  Stale mounts: 2 (jobs 7, 8)
  Running jobs: 1 (job 6)
  Queued jobs:  0 (blocked by disk)

[Recommendations]
  - Clear stale mounts on jobs 7, 8 (see Phase 1.1)
  - Free disk space to below 85% (see Phase 1.2)
```

### 4.2 Threshold Documentation

**Problem**: Operational thresholds are scattered and rationale undocumented.

**Solution**: Create `docs/operations/thresholds-and-tuning.md`

**Contents**:
| Threshold | Value | Location | Rationale | Tuning guidance |
|-----------|-------|----------|-----------|-----------------|
| Disk headroom | 85% | worker/main.py | Allows ~11GB buffer for multi-GB crawls | Lower if crawls are smaller |
| Disk warning alert | 85% for 30m | alerting YAML | Gives time to react before critical | Tighten if disk fills quickly |
| Disk critical alert | 92% for 10m | alerting YAML | Emergency threshold | Don't raise above 95% |
| Crawl stall threshold | 5400s (90min) | vps-crawl-auto-recover.py | Balance between false positives and slow detection | Lower for fast sites |
| Recovery cooldown | 15min | vps-storage-hotpath-auto-recover.py | Prevents flapping | Increase if infra is unstable |
| Hourly recovery cap | 2 | vps-storage-hotpath-auto-recover.py | Limits flapping | Increase cautiously |
| Daily recovery cap | 6 global, 3/job | vps-*-auto-recover.py | Prevents runaway automation | Increase for flaky sources |
| SSHFS keepalive | 15s | systemd service | Balance between overhead and detection | Lower if mounts go stale frequently |
| Deploy lock max age | 2hr | vps-*-auto-recover.py | Stale lock detection | Match longest expected deploy |

---

## Phase 5: Stall Classification (Stretch Goal)

### 5.1 Stall Type Detection

**Problem**: All stalls are treated equally. Some are recoverable (network timeouts), some are not (bad scope rules).

**Design** (for future):
```python
# Stall types based on crawl log analysis
STALL_TYPES = {
    "network": "Progress stopped, no error spike",
    "timeout": "High timeout error rate",
    "http_error": "High HTTP error rate",
    "scope": "Crawl rate zero, no errors (possible scope loop)",
    "resource": "Container OOM or disk full",
}

# Recovery budget varies by type
RECOVERY_BUDGET = {
    "network": 5,      # Likely recoverable
    "timeout": 3,      # Moderate
    "http_error": 3,   # Moderate
    "scope": 1,        # Likely non-recoverable
    "resource": 0,     # Needs operator
}
```

**Deferred**: Requires more crawl metrics infrastructure first.

---

## Implementation Order

| Phase | Item | Effort | Priority | Status |
|-------|------|--------|----------|--------|
| 1.1 | Clear stale mounts | 30 min | P0 | Manual - operator |
| 1.2 | Free disk space | 30 min | P0 | Manual - operator |
| 1.3 | Investigate watchdog behavior | 1 hr | P0 | Manual - operator |
| 2.1 | Disk threshold cleanup trigger | 2-3 hrs | P1 | ✅ Done (2026-02-01) - needs VPS deployment |
| 2.2 | Worker disk warning log | 30 min | P2 | ✅ Already implemented |
| 3.1 | Root cause investigation | Ongoing | P1 | Manual - operator |
| 3.2 | Watchdog diagnostic logging | 1 hr | P2 | ✅ Done (2026-02-01) |
| 4.1 | Watchdog status CLI | 2-3 hrs | P2 | ✅ Done (2026-02-01) |
| 4.2 | Threshold documentation | 1 hr | P2 | ✅ Done (2026-02-01) |
| 5.1 | Stall classification | 4-6 hrs | P3 | Deferred |

---

## Success Metrics

1. **Stale mounts**: Zero unrecovered stale mounts lasting >1 hour
2. **Disk pressure**: Disk usage stays below 85% during normal operations
3. **Crawl recoveries**: Reduce HC job recoveries from 31 to <15 per campaign
4. **Operator intervention**: Reduce from weekly to monthly for routine issues
5. **Mean time to recovery**: Automated recovery completes within 15 minutes

---

## Critical Files Reference

**Watchdog scripts** (already exist):
- `scripts/vps-storage-hotpath-auto-recover.py` (1269 lines) - stale mount recovery
- `scripts/vps-crawl-auto-recover.py` (723 lines) - crawl stall recovery
- `scripts/vps-cleanup-automation.py` (290 lines) - temp file cleanup
- `scripts/vps-warc-tiering-bind-mounts.sh` (241 lines) - bind mount management

**State files**:
- `/srv/healtharchive/ops/watchdog/crawl-auto-recover.json`
- `/srv/healtharchive/ops/watchdog/storage-hotpath-auto-recover.json`

**Sentinel files** (opt-in automation):
- `/etc/healtharchive/crawl-auto-recover-enabled`
- `/etc/healtharchive/storage-hotpath-auto-recover-enabled`
- `/etc/healtharchive/cleanup-automation-enabled`

**Systemd services**:
- `healtharchive-storagebox-sshfs.service` - base SSHFS mount (has hardened options)
- `healtharchive-worker.service` - job worker
- Various `.timer` units for watchdogs

---

## Notes

- Phase 1 should be executed immediately by the operator
- The existing automation is comprehensive but may be hitting rate limits or deploy locks
- SSHFS mount options are already hardened; root cause is likely elsewhere
- All automation remains opt-in via sentinel files (safe-by-default)
- Watchdog status CLI would significantly improve operator experience
