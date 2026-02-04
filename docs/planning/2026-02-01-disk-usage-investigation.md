# Investigation: VPS Disk Usage Mystery (48GB Discrepancy)

**Created**: 2026-02-01
**Status**: Resolved (2026-02-04)
**Priority**: P1 - Impacts crawler operations
**Related**: [Operational Resilience Roadmap](./implemented/2026-02-01-operational-resilience-improvements.md)

---

## Update (2026-02-04): Root cause found, investigation closed

This “mystery discrepancy” turned out to be a **real storage placement problem**, not an ext4 accounting bug:

- Annual crawl output directories (notably CIHR ~50GB, PHAC ~1.2GB) were present on the VPS **root filesystem** under `/srv/healtharchive/jobs/**` instead of being tiered/mounted to the Storage Box.
- This pushed `/dev/sda1` to ~84–86% used and triggered the worker’s disk safety guardrail (≥85%), blocking crawl progress.
- Recovery was to pause crawls, `rsync` the output dirs to the Storage Box, re-apply annual output tiering mounts, and delete the local copies. Root disk dropped to ~19% used afterwards.

Incident note (with the exact commands + timeline):

- `../operations/incidents/2026-02-04-annual-crawl-output-dirs-on-root-disk.md`

## Problem Statement

The VPS local disk shows a **48GB discrepancy** between actual file usage and reported filesystem usage:

| Metric | Value | Source |
|--------|-------|--------|
| Actual files on disk | **14GB** | `du -x -sh /` |
| Filesystem reports used | **62GB** | `df -h /` |
| Discrepancy | **48GB** | Unexplained |

This causes the disk to hover around 82-86%, frequently crossing the **85% worker threshold** which blocks new job processing.

---

## Current State (2026-02-01)

- **Disk**: 82% used (62GB of 75GB)
- **Actual data**: ~14GB (verified with `du -x`)
- **Threshold**: 85% blocks worker from starting new jobs
- **Buffer**: Only ~3% headroom before jobs are blocked again

### Why This Matters

1. Crawl jobs generate GBs of data during operation
2. With only 3% headroom (~2GB), disk can cross 85% within hours
3. When crossed, worker stops picking up new/retryable jobs
4. Manual intervention required to free space

---

## Temporary Measures (Active)

These measures help maintain disk health while we investigate the root cause:

### 1. Reduced Reserved Blocks (Deployed 2026-02-01)

**What**: Changed ext4 reserved blocks from 4% to 1%
```bash
sudo tune2fs -m 1 /dev/sda1
```

**Effect**: Freed ~2.3GB for non-root users, dropped disk from 86% to 83%

**Reversibility**: `sudo tune2fs -m 5 /dev/sda1` to restore default

### 2. Threshold-Triggered Cleanup (Deployed 2026-02-01)

**What**: Automated cleanup that runs when disk exceeds 80%

**Files**:
- Timer: `/etc/systemd/system/healtharchive-disk-threshold-cleanup.timer`
- Service: `/etc/systemd/system/healtharchive-disk-threshold-cleanup.service`
- Config: `/opt/healtharchive-backend/ops/automation/cleanup-automation.toml`

**Behavior**:
- Runs every 30 minutes
- Checks disk usage
- If >80%, cleans up to 5 old indexed jobs aggressively
- If <80%, exits immediately (no-op)

**Limitation**: Can only clean **indexed** jobs older than 14 days. If no such jobs exist, cannot free space.

### 3. Manual Log Rotation and Cleanup

**Periodic commands** (run when disk pressure detected):
```bash
# Truncate old syslogs
sudo truncate -s 0 /var/log/syslog.1

# Force log rotation
sudo logrotate -f /etc/logrotate.conf

# Clean apt cache
sudo apt-get clean

# Docker cleanup (if no containers needed)
docker system prune -f
```

### 4. Monitoring via Watchdog Status

**Command**: `/tmp/watchdog-status.sh` (or the full Python invocation)

Shows current disk usage and whether it's [OK] or [ABOVE THRESHOLD].

---

## Investigation: What We've Tried

### 1. Checked for Deleted Files Held Open

**Command**:
```bash
sudo lsof +L1 2>/dev/null | awk '{print $7}' | grep -E '^[0-9]+$' | \
  awk '{sum+=$1} END {print "Total: " sum/1024/1024/1024 " GB"}'
```

**Result**: Only **43MB** of deleted files held open - NOT the cause.

**Files found**:
- PostgreSQL WAL file: 16MB (deleted but held by postgres)
- systemd-journald: 8MB
- containerd-shim: 12MB
- unattended-upgrades: 8MB

### 2. Checked for Hidden Data Under Mount Points

**Methodology**: Unmounted each sshfs/bind mount and checked underlying directory.

**Results**:
| Mount Point | Data Underneath |
|-------------|-----------------|
| `/srv/healtharchive/storagebox` | 4K (empty) |
| `/srv/healtharchive/jobs/imports/legacy-hc-2025-04-21` | 4K (empty) |
| `/srv/healtharchive/jobs/imports/legacy-cihr-2025-04` | 4K (empty) |
| `/srv/healtharchive/jobs/hc/20260101T000502Z__hc-20260101` | 4K (empty) |
| `/srv/healtharchive/jobs/phac/20260101T000502Z__phac-20260101` | 4K (empty) |
| `/srv/healtharchive/jobs/cihr/20260101T000502Z__cihr-20260101` | 4K (empty) |

**Conclusion**: No hidden data under mounts - NOT the cause.

### 3. Checked Reserved Blocks

**Command**:
```bash
sudo tune2fs -l /dev/sda1 | grep -E "(Reserved|Block)"
```

**Results**:
- Block count: 19,934,715
- Reserved block count: 807,089 (was 4%, now 1%)
- Block size: 4096
- Reserved space: ~3.1GB

**Conclusion**: Reserved blocks only account for ~3GB, not 48GB.

### 4. Checked lost+found

**Command**:
```bash
sudo du -sh /lost+found
```

**Result**: 16K - NOT the cause.

### 5. Verified Local vs Remote Data

**Command**:
```bash
sudo du -x -h --max-depth=3 / 2>/dev/null | sort -h | tail -30
```

**Key findings** (local disk only, `-x` flag):
- `/var/lib/docker/overlay2`: 6.5GB
- `/var/lib/postgresql`: 1.9GB
- `/usr`: 3.1GB
- `/var/log`: 1.1GB
- `/srv/healtharchive` (local): 1.1GB
- `/opt`: 429MB
- **Total**: ~14GB

This confirms the actual data is only 14GB.

### 6. Checked Docker Storage

**Command**:
```bash
docker system df -v
```

**Results**:
- Images: ~5GB total
- Containers: 60MB
- Volumes: 0B
- Build cache: 0B

Docker is accounted for in the 14GB.

### 7. Checked Filesystem Details

**Command**:
```bash
sudo dumpe2fs -h /dev/sda1 2>/dev/null | grep -E "(Reserved|Free|Block)"
```

**Results**:
- Total blocks: 19,934,715 × 4096 = 81.6GB
- Free blocks: 3,376,535 × 4096 = 13.8GB
- Calculated used: 81.6GB - 13.8GB = 67.8GB (matches df)

The filesystem genuinely believes 62GB is used.

---

## What We Haven't Tried Yet

### 1. Filesystem Check (fsck) - REQUIRES MAINTENANCE WINDOW

**Why needed**: `fsck` can detect and fix:
- Orphaned inodes (files with no directory entry)
- Corrupted metadata
- Bad block accounting
- Unreferenced blocks

**Command**:
```bash
sudo e2fsck -f /dev/sda1
```

**⚠️ CRITICAL WARNING**:
- **CANNOT run on mounted filesystem**
- Requires booting into rescue/recovery mode
- May take 30+ minutes on 75GB disk
- Risk of data loss if filesystem is corrupted
- **Schedule during maintenance window only**

### 2. Check for Sparse Files

Sparse files report a larger size than they actually consume on disk. However, `du` should report actual block usage, not apparent size.

**To investigate**:
```bash
# Find files where apparent size differs from disk usage
sudo find / -xdev -type f -exec sh -c 'apparent=$(stat -c%s "$1"); blocks=$(stat -c%b "$1"); disk=$((blocks*512)); if [ $apparent -ne $disk ] && [ $disk -gt 1048576 ]; then echo "$1: apparent=$apparent disk=$disk"; fi' _ {} \;
```

### 3. Check for Extended Attributes or ACLs

Extended attributes can consume additional space not visible in normal file listings.

**To investigate**:
```bash
# Check for files with extended attributes
sudo find / -xdev -type f -exec getfattr -d {} \; 2>/dev/null | grep -v "^$"
```

### 4. Check for Filesystem Corruption Indicators

**Commands to run** (safe while mounted):
```bash
# Check for filesystem errors in dmesg
dmesg | grep -i "ext4\|error\|corrupt\|i/o"

# Check mount options for errors=remount-ro
mount | grep /dev/sda1
```

### 5. Compare Inode Usage

If there are millions of small files or orphaned inodes, this could explain the discrepancy.

**Command**:
```bash
df -i /
sudo dumpe2fs -h /dev/sda1 | grep -i inode
```

---

## Hypotheses

### Most Likely: Filesystem Corruption / Orphaned Blocks

**Theory**: At some point (possibly during a crash, power loss, or unclean shutdown), the filesystem developed orphaned blocks - disk space that's marked as "used" but not associated with any file.

**Evidence supporting this**:
- `du` (which counts file blocks) shows 14GB
- `df` (which reads filesystem metadata) shows 62GB
- No deleted files held open
- No hidden data under mounts
- Reserved blocks only account for 3GB

**Why it would happen**:
- VPS could have been rebooted uncleanly
- Docker container operations can be filesystem-intensive
- sshfs mount/unmount cycles during stale mount recovery

### Alternative: Kernel/Filesystem Bug

**Theory**: A bug in ext4 or the kernel is causing incorrect block accounting.

**Less likely because**: ext4 is extremely mature and well-tested.

### Alternative: Hardware Issue

**Theory**: Disk is reporting incorrect usage due to hardware problems.

**To check**: Review SMART data if available.

---

## Maintenance Window Plan

### Prerequisites

1. **Notify stakeholders**: Crawls will be paused
2. **Backup critical data**: Database, configuration
3. **Document current state**: Job statuses, disk usage
4. **Estimate downtime**: 1-2 hours minimum

### Procedure

1. **Stop all services**:
   ```bash
   sudo systemctl stop healtharchive-worker
   sudo systemctl stop healtharchive-replay
   sudo systemctl stop postgresql
   ```

2. **Unmount all remote filesystems**:
   ```bash
   sudo umount /srv/healtharchive/storagebox
   # All bind mounts will also unmount
   ```

3. **Boot into rescue mode** (Hetzner Cloud Console):
   - Access VPS console
   - Reboot into rescue system
   - Mount root filesystem read-only first:
     ```bash
     mount -o ro /dev/sda1 /mnt
     ```

4. **Run filesystem check**:
   ```bash
   e2fsck -f -y /dev/sda1
   ```

   The `-y` flag auto-answers "yes" to repairs. For interactive mode, omit `-y`.

5. **Review and document findings**:
   - Note any orphaned inodes found
   - Note any blocks recovered
   - Save output to a file

6. **Reboot normally**:
   ```bash
   reboot
   ```

7. **Verify**:
   ```bash
   df -h /
   du -x -sh /
   ```

   If discrepancy is resolved, `df` and `du` should be much closer.

8. **Restart services**:
   ```bash
   sudo systemctl start healtharchive-storagebox-sshfs
   sudo /opt/healtharchive-backend/scripts/vps-warc-tiering-bind-mounts.sh --apply
   sudo bash -lc 'source /etc/healtharchive/backend.env; \
     /opt/healtharchive-backend/.venv/bin/python3 \
     /opt/healtharchive-backend/scripts/vps-annual-output-tiering.py \
     --apply --year 2026'
   sudo systemctl start postgresql
   sudo systemctl start healtharchive-replay
   sudo systemctl start healtharchive-worker
   ```

---

## Risk Assessment

### If We Do Nothing

- Disk will continue to hover around 82-86%
- Crawl jobs will be frequently blocked at 85% threshold
- Manual intervention required every 1-2 days
- Threshold cleanup can only help if old jobs exist to clean

### If We Run fsck

- **Best case**: Recovers 48GB, problem solved permanently
- **Worst case**: Discovers unrecoverable corruption, may need to rebuild VPS
- **Typical case**: Finds and fixes orphaned inodes, recovers most space

### Recommendation

1. **Short term**: Continue with temporary measures, monitor closely
2. **Medium term**: Schedule maintenance window for fsck within 1-2 weeks
3. **Before fsck**: Ensure backups are current (database, configuration)

---

## Monitoring Commands

### Quick Status Check
```bash
/tmp/watchdog-status.sh
```

### Detailed Disk Analysis
```bash
# Actual file usage (local disk only)
sudo du -x -sh /

# Filesystem reported usage
df -h /

# Compare the two
echo "Discrepancy: $(echo "$(df / | tail -1 | awk '{print $3}') - $(sudo du -x -s / 2>/dev/null | awk '{print $1}')" | bc) KB"
```

### Check if Threshold Cleanup is Running
```bash
systemctl status healtharchive-disk-threshold-cleanup.timer
systemctl list-timers healtharchive-disk-threshold-cleanup.timer
```

---

## Timeline

| Date | Event |
|------|-------|
| 2026-02-01 | Issue identified during resilience roadmap work |
| 2026-02-01 | Temporary measures deployed (reserved blocks, threshold cleanup) |
| 2026-02-01 | Investigation completed (deleted files, hidden mounts, etc.) |
| TBD | Maintenance window scheduled for fsck |
| TBD | fsck run, results documented |
| TBD | Issue resolved (or escalated) |

---

## References

- ext4 filesystem documentation: https://www.kernel.org/doc/html/latest/filesystems/ext4/
- e2fsck manual: `man e2fsck`
- Hetzner Cloud rescue mode: https://docs.hetzner.com/cloud/servers/getting-started/rescue-system/
