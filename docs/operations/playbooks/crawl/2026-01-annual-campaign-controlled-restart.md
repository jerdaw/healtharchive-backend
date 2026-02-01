# 2026 Annual Campaign Controlled Restart Plan

**Date:** 2026-01-27
**Severity:** Sev1 (major degradation - crawls running 27 days without completion)
**Status:** Planning

## Executive Summary

The 2026 annual campaign (3 jobs: hc, phac, cihr) has been running since Jan 1 with persistent stability issues:

- **Job 6 (HC):** 67 temp dirs, 30 auto-recoveries, state file 2.4 days stale
- **Job 7 (PHAC):** stalled 36+ minutes, 11 container restarts, 12 temp dirs
- **Job 8 (CIHR):** regressed from 45% to 38%, permission errors, 4 temp dirs

**Goal:** Controlled restart that preserves all captured WARCs while fixing infrastructure issues.

**Risk:** Data loss if WARCs in `.tmp*` directories are deleted before consolidation.

---

## Pre-Flight: Current State Assessment

### Known Issues

1. **Stale sshfs mounts (Errno 107)** on jobs 7 and 8 output directories
2. **Permission denied** on Job 8's `.tmp_zcuywum/collections`
3. **Disk usage at 68%** (9GB/12.8h burn rate)
4. **State file staleness** (Job 6: 2.4 days, suggesting state not persisting)

### Data to Preserve

| Job | Temp Dirs | Est. WARCs | Notes |
|-----|-----------|------------|-------|
| 6 (hc) | 67 | Unknown (many) | Excessive temp dirs suggest many restart phases |
| 7 (phac) | 12 | Unknown | 11 container restarts |
| 8 (cihr) | 4 | 11 discovered | 38% complete on latest run |

---

## Phase 1: Snapshot and Document Current State (15 min)

**Purpose:** Create recovery point before any changes.

### 1.1 Full Status Snapshot

```bash
cd /opt/healtharchive-backend

# Save full status to file
./scripts/vps-crawl-status.sh --year 2026 > /tmp/crawl-status-$(date -u +%Y%m%dT%H%M%SZ).txt 2>&1

# Document disk state
df -h | tee /tmp/disk-state-$(date -u +%Y%m%dT%H%M%SZ).txt

# Document mount state
mount | grep healtharchive | tee /tmp/mounts-$(date -u +%Y%m%dT%H%M%SZ).txt

# Document running containers
docker ps --format 'table {{.ID}}\t{{.Image}}\t{{.Names}}\t{{.Status}}' | tee /tmp/docker-ps-$(date -u +%Y%m%dT%H%M%SZ).txt
```

### 1.2 Inventory Existing WARCs (Critical)

**IMPORTANT:** Record all WARC locations BEFORE any changes.

```bash
# For each job, document WARCs found
for job_dir in /srv/healtharchive/jobs/*/20260101*; do
  echo "=== $job_dir ===" >> /tmp/warc-inventory.txt
  find "$job_dir" -name "*.warc.gz" -o -name "*.warc" 2>/dev/null | \
    while read f; do
      stat --format="%s %Y %n" "$f" 2>/dev/null || echo "STAT_FAILED: $f"
    done >> /tmp/warc-inventory.txt
done

# Count totals
echo "=== WARC Counts ===" >> /tmp/warc-inventory.txt
for job_dir in /srv/healtharchive/jobs/*/20260101*; do
  count=$(find "$job_dir" -name "*.warc.gz" -o -name "*.warc" 2>/dev/null | wc -l)
  echo "$job_dir: $count WARCs" >> /tmp/warc-inventory.txt
done

cat /tmp/warc-inventory.txt
```

### 1.3 Document State Files

```bash
# Copy state files for reference
mkdir -p /tmp/state-backup-$(date -u +%Y%m%d)
for job_dir in /srv/healtharchive/jobs/*/20260101*; do
  job_name=$(basename "$job_dir")
  cp -v "$job_dir/.archive_state.json" "/tmp/state-backup-$(date -u +%Y%m%d)/${job_name}.state.json" 2>/dev/null || echo "No state file: $job_dir"
done
```

---

## Phase 2: Stop All Crawl Activity (10 min)

**Purpose:** Prevent further changes while we assess and repair.

### 2.1 Stop Worker Service

```bash
sudo systemctl stop healtharchive-worker.service
sudo systemctl status healtharchive-worker.service --no-pager -l
```

### 2.2 Disable Auto-Recovery Timers (Temporarily)

```bash
# Disable crawl auto-recover
sudo mv /etc/healtharchive/crawl-auto-recover-enabled /etc/healtharchive/crawl-auto-recover-enabled.disabled 2>/dev/null || echo "Already disabled"

# Disable storage hotpath auto-recover
sudo mv /etc/healtharchive/storage-hotpath-auto-recover-enabled /etc/healtharchive/storage-hotpath-auto-recover-enabled.disabled 2>/dev/null || echo "Already disabled"

# Disable worker auto-start
sudo mv /etc/healtharchive/worker-auto-start-enabled /etc/healtharchive/worker-auto-start-enabled.disabled 2>/dev/null || echo "Already disabled"
```

### 2.3 Stop Any Running Zimit Containers

```bash
# List zimit containers
docker ps --format '{{.ID}} {{.Image}} {{.Names}}' | grep -E 'zimit|openzim'

# Stop them gracefully (adjust IDs as needed)
docker ps --format '{{.ID}} {{.Image}}' | grep -E 'zimit|openzim' | awk '{print $1}' | xargs -r docker stop

# Verify stopped
docker ps | grep -E 'zimit|openzim' || echo "No running zimit containers"
```

---

## Phase 3: Fix Infrastructure Issues (20 min)

### 3.1 Fix Stale Mounts

Follow `docs/operations/playbooks/storage/storagebox-sshfs-stale-mount-recovery.md`:

```bash
# Check Storage Box base mount
ls -la /srv/healtharchive/storagebox >/dev/null && echo "OK: storagebox readable" || echo "BAD: storagebox unreadable"

# Identify stale job mounts
for job_dir in /srv/healtharchive/jobs/*/20260101*; do
  ls "$job_dir" >/dev/null 2>&1 && echo "OK: $job_dir" || echo "STALE: $job_dir"
done

# Unmount stale hot paths (adjust paths based on actual findings)
sudo umount -l /srv/healtharchive/jobs/phac/20260101T000502Z__phac-20260101 2>/dev/null || true
sudo umount -l /srv/healtharchive/jobs/cihr/20260101T000502Z__cihr-20260101 2>/dev/null || true

# Re-apply tiering with repair
sudo ./scripts/vps-warc-tiering-bind-mounts.sh --apply --repair-stale-mounts

# Verify mounts are healthy
for job_dir in /srv/healtharchive/jobs/*/20260101*; do
  ls "$job_dir" >/dev/null 2>&1 && echo "OK: $job_dir" || echo "STILL_BROKEN: $job_dir"
done
```

### 3.2 Fix Permission Issues

```bash
# For Job 8 (cihr) with permission denied on .tmp_zcuywum
job_dir="/srv/healtharchive/jobs/cihr/20260101T000502Z__cihr-20260101"

# Use Docker alpine container to fix perms (avoids needing host sudo on files)
docker run --rm -v "$job_dir:/output" alpine chmod -R a+rX /output/.tmp* 2>/dev/null || {
  # Fallback: use sudo if Docker approach fails
  sudo chmod -R a+rX "$job_dir"/.tmp* 2>/dev/null || echo "Permission fix may need manual intervention"
}

# Verify
ls -la "$job_dir"/.tmp*/collections/ 2>/dev/null | head -5
```

### 3.3 Verify Disk Space

```bash
df -h /srv/healtharchive/jobs

# If above 70%, identify large temp dirs for later cleanup
du -sh /srv/healtharchive/jobs/*/20260101*/.tmp* 2>/dev/null | sort -h | tail -20
```

---

## Phase 4: Assess and Consolidate Existing WARCs (30 min)

**Purpose:** Secure all captured data before any restart decisions.

### 4.1 Verify WARCs Per Job

```bash
source /etc/healtharchive/backend.env
cd /opt/healtharchive-backend
source .venv/bin/activate

# Job 6 (HC)
ha-backend show-job --id 6
echo "--- WARC Discovery ---"
python3 -c "
from ha_backend.indexing.warc_discovery import discover_temp_warcs_for_job
from ha_backend.db import get_session
from ha_backend.models import ArchiveJob
session = next(get_session())
job = session.query(ArchiveJob).get(6)
warcs = discover_temp_warcs_for_job(job)
print(f'Job 6 (HC): {len(warcs)} WARCs discovered')
for w in warcs[:5]: print(f'  {w}')
if len(warcs) > 5: print(f'  ... and {len(warcs)-5} more')
"

# Job 7 (PHAC)
ha-backend show-job --id 7
echo "--- WARC Discovery ---"
python3 -c "
from ha_backend.indexing.warc_discovery import discover_temp_warcs_for_job
from ha_backend.db import get_session
from ha_backend.models import ArchiveJob
session = next(get_session())
job = session.query(ArchiveJob).get(7)
warcs = discover_temp_warcs_for_job(job)
print(f'Job 7 (PHAC): {len(warcs)} WARCs discovered')
for w in warcs[:5]: print(f'  {w}')
if len(warcs) > 5: print(f'  ... and {len(warcs)-5} more')
"

# Job 8 (CIHR)
ha-backend show-job --id 8
echo "--- WARC Discovery ---"
python3 -c "
from ha_backend.indexing.warc_discovery import discover_temp_warcs_for_job
from ha_backend.db import get_session
from ha_backend.models import ArchiveJob
session = next(get_session())
job = session.query(ArchiveJob).get(8)
warcs = discover_temp_warcs_for_job(job)
print(f'Job 8 (CIHR): {len(warcs)} WARCs discovered')
for w in warcs[:5]: print(f'  {w}')
if len(warcs) > 5: print(f'  ... and {len(warcs)-5} more')
"
```

### 4.2 Verify WARC Integrity (Level 1)

**CRITICAL:** Do this BEFORE any consolidation or cleanup.

```bash
# Verify WARCs for each job (Level 1 = gzip integrity check)
ha-backend verify-warcs --job-id 6 --level 1 --json-out /tmp/verify-warcs-6.json
ha-backend verify-warcs --job-id 7 --level 1 --json-out /tmp/verify-warcs-7.json
ha-backend verify-warcs --job-id 8 --level 1 --json-out /tmp/verify-warcs-8.json

# Review results
for f in /tmp/verify-warcs-*.json; do
  echo "=== $f ==="
  python3 -c "import json; d=json.load(open('$f')); print(f\"passed={d.get('passed',0)} failed={d.get('failed',0)}\")"
done
```

**If verification shows failures:** Use `--apply-quarantine` to move corrupt WARCs aside (only if job is NOT running):

```bash
# ONLY if failures detected and you want to quarantine corrupt files:
# ha-backend verify-warcs --job-id <ID> --level 1 --apply-quarantine
```

### 4.3 Consolidate WARCs to Stable Location

**Purpose:** Move WARCs from `.tmp*` to stable `warcs/` directory with hardlinks.

```bash
# Dry-run first for each job
ha-backend consolidate-warcs --job-id 6 --dry-run
ha-backend consolidate-warcs --job-id 7 --dry-run
ha-backend consolidate-warcs --job-id 8 --dry-run

# If dry-run looks good, apply consolidation
ha-backend consolidate-warcs --job-id 6
ha-backend consolidate-warcs --job-id 7
ha-backend consolidate-warcs --job-id 8

# Verify stable WARCs exist
for job_id in 6 7 8; do
  job_dir=$(python3 -c "
from ha_backend.db import get_session
from ha_backend.models import ArchiveJob
session = next(get_session())
job = session.query(ArchiveJob).get($job_id)
print(job.output_dir)
")
  echo "=== Job $job_id: $job_dir/warcs/ ==="
  ls -la "$job_dir/warcs/" 2>/dev/null | head -5 || echo "No stable warcs dir"
  ls -la "$job_dir/warcs/manifest.json" 2>/dev/null || echo "No manifest"
done
```

---

## Phase 5: Decision Point - Restart Strategy

At this point, you have:
- All WARCs consolidated to stable `warcs/` directories
- Infrastructure issues fixed
- Worker stopped

### Option A: Resume Existing Crawls (Lower Risk, Slower)

Use this if:
- WARCs are mostly complete for some sources
- You want to preserve crawl continuity
- Time is not critical

**Pros:** Preserves existing progress, zimit may resume from checkpoint
**Cons:** May hit same stability issues, existing adaptation budgets may be exhausted

### Option B: Fresh Crawls with WARC Consolidation (Recommended)

Use this if:
- Crawls have been unstable for weeks
- Adaptation budgets are exhausted
- You want a clean slate but keep existing WARCs

**Pros:** Fresh adaptation budgets, cleaner state, more predictable behavior
**Cons:** Starts crawl from scratch (but WARCs consolidate in final build)

### Option C: Mark as Completed and Index Partial Data

Use this if:
- Time pressure to have SOME data available
- Current WARCs represent meaningful coverage
- You plan to run a supplemental crawl later

**Pros:** Immediate availability of partial data
**Cons:** Incomplete coverage, may need follow-up crawl

---

## Phase 6A: Resume Existing Crawls

### 6A.1 Recover Stale Jobs

```bash
# Mark jobs as retryable (preserves state for resume)
ha-backend recover-stale-jobs --older-than-minutes 5 --dry-run
ha-backend recover-stale-jobs --older-than-minutes 5 --apply
```

### 6A.2 Reset Retry Budgets (If Needed)

If jobs have exhausted container restart budgets:

```bash
# Check current restart counts
for job_id in 6 7 8; do
  job_dir=$(python3 -c "
from ha_backend.db import get_session
from ha_backend.models import ArchiveJob
session = next(get_session())
job = session.query(ArchiveJob).get($job_id)
print(job.output_dir)
")
  echo "=== Job $job_id ==="
  cat "$job_dir/.archive_state.json" 2>/dev/null | python3 -m json.tool | grep -E "restarts|reductions|rotations"
done

# If restart budget exhausted, reset state (preserves temp_dirs)
for job_id in 6 7 8; do
  job_dir=$(python3 -c "
from ha_backend.db import get_session
from ha_backend.models import ArchiveJob
session = next(get_session())
job = session.query(ArchiveJob).get($job_id)
print(job.output_dir)
")
  # Edit state to reset counts but keep temp_dirs
  python3 -c "
import json
from pathlib import Path
state_file = Path('$job_dir/.archive_state.json')
if state_file.exists():
    state = json.loads(state_file.read_text())
    state['container_restarts_done'] = 0
    state['worker_reductions_done'] = 0
    state['vpn_rotations_done'] = 0
    state_file.write_text(json.dumps(state, indent=2))
    print(f'Reset adaptation counts for job $job_id')
"
done
```

### 6A.3 Restart Worker

```bash
# Re-enable automation
sudo mv /etc/healtharchive/crawl-auto-recover-enabled.disabled /etc/healtharchive/crawl-auto-recover-enabled 2>/dev/null || true
sudo mv /etc/healtharchive/storage-hotpath-auto-recover-enabled.disabled /etc/healtharchive/storage-hotpath-auto-recover-enabled 2>/dev/null || true

# Start worker
sudo systemctl start healtharchive-worker.service
sudo systemctl status healtharchive-worker.service --no-pager -l

# Monitor for 5 minutes
sleep 60 && ./scripts/vps-crawl-status.sh --year 2026 | head -40
```

---

## Phase 6B: Fresh Crawls with WARC Consolidation (Recommended)

### 6B.1 Prepare Jobs for Fresh Start

**CRITICAL:** Do NOT use `--overwrite` flag - it deletes prior WARCs!

```bash
# For each job, delete state file but KEEP temp dirs and WARCs
for job_id in 6 7 8; do
  job_dir=$(python3 -c "
from ha_backend.db import get_session
from ha_backend.models import ArchiveJob
session = next(get_session())
job = session.query(ArchiveJob).get($job_id)
print(job.output_dir)
")

  echo "=== Job $job_id: $job_dir ==="

  # Backup state file first
  cp "$job_dir/.archive_state.json" "/tmp/state-backup-$(date -u +%Y%m%d)/job-$job_id-pre-fresh.json" 2>/dev/null || true

  # Remove state file (crawl will start fresh but discover existing WARCs)
  rm -f "$job_dir/.archive_state.json"
  rm -f "$job_dir/.zimit_resume.yaml"

  # Verify temp dirs still exist (DO NOT DELETE)
  echo "Temp dirs preserved:"
  ls -d "$job_dir"/.tmp* 2>/dev/null | wc -l
done
```

### 6B.2 Reset Job Status in Database

```bash
# Reset jobs to queued status with fresh retry count
source /etc/healtharchive/backend.env
cd /opt/healtharchive-backend
source .venv/bin/activate

python3 -c "
from ha_backend.db import get_session
from ha_backend.models import ArchiveJob
from datetime import datetime, timezone

session = next(get_session())

for job_id in [6, 7, 8]:
    job = session.query(ArchiveJob).get(job_id)
    if job:
        print(f'Resetting job {job_id} ({job.source.code}): {job.status} -> queued')
        job.status = 'queued'
        job.retry_count = 0
        job.crawler_exit_code = None
        job.crawler_status = None
        job.started_at = None
        job.finished_at = None
        job.queued_at = datetime.now(timezone.utc)

session.commit()
print('Jobs reset successfully')
"
```

### 6B.3 Consider Staggered Restarts

To reduce resource contention:

```bash
# Option: Run one job at a time instead of all three
# Start with the smallest (CIHR)
ha-backend run-db-job --id 8  # Run synchronously to monitor

# Or use detached mode
python3 ./scripts/vps-run-db-job-detached.py --job-id 8
```

### 6B.4 Restart Worker for Automated Processing

```bash
# Re-enable automation
sudo mv /etc/healtharchive/crawl-auto-recover-enabled.disabled /etc/healtharchive/crawl-auto-recover-enabled 2>/dev/null || true
sudo mv /etc/healtharchive/storage-hotpath-auto-recover-enabled.disabled /etc/healtharchive/storage-hotpath-auto-recover-enabled 2>/dev/null || true

# Start worker
sudo systemctl start healtharchive-worker.service

# Worker will pick up queued jobs automatically
```

---

## Phase 6C: Mark as Completed and Index Partial Data

### 6C.1 Set Jobs to Completed

```bash
source /etc/healtharchive/backend.env
cd /opt/healtharchive-backend
source .venv/bin/activate

python3 -c "
from ha_backend.db import get_session
from ha_backend.models import ArchiveJob
from datetime import datetime, timezone

session = next(get_session())

for job_id in [6, 7, 8]:
    job = session.query(ArchiveJob).get(job_id)
    if job:
        print(f'Marking job {job_id} ({job.source.code}) as completed')
        job.status = 'completed'
        job.crawler_exit_code = 0
        job.crawler_status = 'completed_partial'  # Indicates manual completion
        job.finished_at = datetime.now(timezone.utc)

session.commit()
print('Jobs marked as completed')
"
```

### 6C.2 Run Indexing Manually

```bash
# Index each job
ha-backend index-job --id 6
ha-backend index-job --id 7
ha-backend index-job --id 8

# Verify indexing
for job_id in 6 7 8; do
  echo "=== Job $job_id ==="
  ha-backend show-job --id $job_id | grep -E "status|indexed_page"
done
```

---

## Phase 7: Post-Restart Verification

### 7.1 Monitor Crawl Progress

```bash
# Check every 30 minutes for the first 2 hours
watch -n 1800 './scripts/vps-crawl-status.sh --year 2026 | head -60'

# Or manual checks
./scripts/vps-crawl-status.sh --year 2026
```

### 7.2 Verify Disk Space Trend

```bash
# Monitor disk usage
watch -n 300 'df -h /srv/healtharchive/jobs && echo "---" && du -sh /srv/healtharchive/jobs/*/20260101*/.tmp* 2>/dev/null | sort -h | tail -10'
```

### 7.3 Check for Recurring Issues

```bash
# Check auto-recovery frequency
cat /srv/healtharchive/ops/watchdog/crawl-auto-recover.json | python3 -m json.tool | tail -30

# Check for new Errno 107 errors
journalctl -u healtharchive-worker --since "1 hour ago" | grep -i "errno 107" || echo "No Errno 107 errors"
```

---

## Phase 8: Post-Indexing Cleanup

**ONLY after jobs are `indexed`:**

### 8.1 Safe Temp Cleanup

```bash
# Verify jobs are indexed
for job_id in 6 7 8; do
  ha-backend show-job --id $job_id | grep -E "^Status:"
done

# Dry-run cleanup
ha-backend cleanup-job --id 6 --mode temp-nonwarc --dry-run
ha-backend cleanup-job --id 7 --mode temp-nonwarc --dry-run
ha-backend cleanup-job --id 8 --mode temp-nonwarc --dry-run

# Apply cleanup (consolidates WARCs, rewrites snapshot paths, deletes .tmp*)
ha-backend cleanup-job --id 6 --mode temp-nonwarc
ha-backend cleanup-job --id 7 --mode temp-nonwarc
ha-backend cleanup-job --id 8 --mode temp-nonwarc
```

### 8.2 Verify Replay Works

```bash
# Test replay URLs for a few snapshots
./scripts/vps-replay-smoke-textfile.py --dry-run
```

---

## Rollback Procedures

### If Crawl Immediately Fails After Restart

```bash
# Stop worker
sudo systemctl stop healtharchive-worker.service

# Check logs
journalctl -u healtharchive-worker --since "10 minutes ago" | tail -100

# Recover stale jobs
ha-backend recover-stale-jobs --older-than-minutes 5 --apply

# Investigate before restarting
```

### If WARCs Are Accidentally Lost

```bash
# Check if stable WARCs exist
ls -la /srv/healtharchive/jobs/*/20260101*/warcs/

# If no stable WARCs and .tmp* deleted, recovery options:
# 1. Restore from Storage Box cold tier (if tiered)
# 2. Restore from backup (if available)
# 3. Re-crawl from scratch (last resort)
```

---

## Data Quality Verification Checklist

After indexing completes, verify:

### Coverage Check

```bash
# Compare indexed pages to previous annual campaigns
source /etc/healtharchive/backend.env
cd /opt/healtharchive-backend
source .venv/bin/activate

python3 -c "
from ha_backend.db import get_session
from ha_backend.models import Snapshot, ArchiveJob
from sqlalchemy import func

session = next(get_session())

# 2026 campaign stats
for job_id in [6, 7, 8]:
    job = session.query(ArchiveJob).get(job_id)
    count = session.query(Snapshot).filter(Snapshot.job_id == job_id).count()
    print(f'{job.source.code} (2026): {count} snapshots, indexed_page_count={job.indexed_page_count}')
"
```

### Search Quality Spot Check

```bash
# Run golden queries
./scripts/search-eval-capture.sh --out-dir /tmp/ha-search-eval-2026 --page-size 20

# Check results
ls -la /tmp/ha-search-eval-2026/
```

### API Health

```bash
curl -s https://api.healtharchive.ca/api/health | python3 -m json.tool
curl -s https://api.healtharchive.ca/api/stats | python3 -m json.tool
curl -s "https://api.healtharchive.ca/api/search?q=vaccines&source=hc" | python3 -c "import sys,json; d=json.load(sys.stdin); print(f'Results: {d.get(\"totalResults\", 0)}')"
```

---

## Timeline Estimates

| Phase | Duration | Notes |
|-------|----------|-------|
| Phase 1: Snapshot | 15 min | Document current state |
| Phase 2: Stop activity | 10 min | Stop worker and containers |
| Phase 3: Fix infra | 20 min | Mounts, permissions |
| Phase 4: Assess/Consolidate | 30 min | WARC verification and consolidation |
| Phase 5: Decision | 10 min | Choose restart strategy |
| Phase 6: Execute restart | 15 min | Apply chosen strategy |
| Phase 7: Monitor | Ongoing | Until crawls complete |
| Phase 8: Cleanup | 15 min | After indexing |

**Total active time:** ~2 hours
**Crawl completion:** 2-7 days depending on strategy and source sizes

---

## References

- `docs/operations/playbooks/storage/storagebox-sshfs-stale-mount-recovery.md`
- `docs/operations/playbooks/crawl/crawl-stalls.md`
- `docs/operations/playbooks/core/incident-response.md`
- `docs/operations/playbooks/storage/warc-integrity-verification.md`
- `docs/operations/playbooks/crawl/cleanup-automation.md`
- `docs/tutorials/debug-crawl.md`
- `src/archive_tool/docs/documentation.md`
