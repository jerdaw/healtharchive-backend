# Post-Reboot Annual Job Tiering Verification

**Type**: Validation Runbook
**Category**: Operations / Storage Tiering
**Last updated**: 2026-02-06

## Purpose

After a VPS reboot, verify that:
1. Storage Box is mounted correctly
2. Bind mounts for annual jobs are healthy
3. Postgres is running and can connect
4. The tiering script can find jobs and verify their state

**When to use this**: After any VPS reboot, especially during annual campaign season (December-February).

---

## Prerequisites

- SSH access to VPS
- Worker should be stopped during validation (or use `--allow-repair-running-jobs` cautiously)

---

## Verification Checklist

### 1. Verify Storage Box Mount

```bash
# Check mount is present and accessible
findmnt /srv/healtharchive/storagebox
ls -ld /srv/healtharchive/storagebox

# Should NOT show Errno 107 (stale mount)
ls /srv/healtharchive/storagebox/jobs
```

**Expected**: Directory listing works without "Transport endpoint not connected" error.

**If mount is missing**: Follow storage box mounting procedure in main deployment docs.

---

### 2. Verify Bind Mounts

```bash
# List all bind mounts for annual jobs
findmnt | grep /srv/healtharchive/jobs | grep storagebox
```

**Expected**: See entries like:
```
/srv/healtharchive/jobs/2025_annual_hc [...] /srv/healtharchive/storagebox/jobs/2025_annual_hc
```

**If bind mounts are missing**: Re-run tiering script (see step 5).

---

### 3. Verify Postgres is Running

```bash
# Check postgres status
systemctl status postgresql

# Test database connection
export $(cat /etc/healtharchive/env.production | xargs)
psql "${HEALTHARCHIVE_DATABASE_URL}" -c "SELECT COUNT(*) FROM archive_jobs WHERE status='running';"
```

**Expected**: Postgres is active, query returns successfully.

**If connection fails**:
- Ensure Postgres is started: `sudo systemctl start postgresql`
- Verify env file exists and is readable
- Check `DATABASE_URL` format in env file

---

### 4. Verify Job Discovery

For each active annual job, verify the output directory is accessible:

```bash
# Example for 2025 annual jobs
ha-backend show-job --id <annual_job_id> --warc-details
```

**Expected**:
- `WARC source: stable` (if job is indexed)
- No `Errno 107` errors
- File sizes and counts are reasonable

**If errors occur**: Note job ID and continue to step 5.

---

### 5. Run Tiering Script in Dry-Run Mode

```bash
# This will detect and report any issues
/opt/healtharchive-backend/.venv/bin/python3 \
  /opt/healtharchive-backend/scripts/vps-annual-output-tiering.py \
  --year 2025
```

**Expected output**:
- No database connection errors
- All annual jobs show `OK (already mounted)` or are correctly identified for tiering
- If you see `WARN ... reason=unexpected_mount_type`, the output dir is mounted but not as a bind mount (higher staleness risk).
  - Plan a maintenance window to convert it (stop the worker first).

**If tiering script fails with database error**:
```bash
# Load environment and retry
export $(cat /etc/healtharchive/env.production | xargs)
/opt/healtharchive-backend/.venv/bin/python3 \
  /opt/healtharchive-backend/scripts/vps-annual-output-tiering.py \
  --year 2025
```

---

### 6. Repair Stale Mounts (If Needed)

If step 5 shows `STALE (Errno 107)` entries:

```bash
# Stop worker first!
sudo systemctl stop healtharchive-worker

# Run tiering script with repair flag
sudo /opt/healtharchive-backend/.venv/bin/python3 \
  /opt/healtharchive-backend/scripts/vps-annual-output-tiering.py \
  --year 2025 \
  --apply \
  --repair-stale-mounts \
  --allow-repair-running-jobs

# If step 5 shows `WARN ... reason=unexpected_mount_type` entries:
# (maintenance only; converts direct sshfs mounts into bind mounts)
sudo /opt/healtharchive-backend/.venv/bin/python3 \
  /opt/healtharchive-backend/scripts/vps-annual-output-tiering.py \
  --year 2025 \
  --apply \
  --repair-unexpected-mounts \
  --allow-repair-running-jobs
```

**After repair**: Re-run step 4 to verify jobs are now accessible.

---

### 7. Restart Worker

```bash
# Restart worker (will now use healthy mounts)
sudo systemctl start healtharchive-worker

# Verify worker can pick up jobs
sudo journalctl -u healtharchive-worker -f
```

**Expected**: Worker logs show normal job selection, no `RuntimeError` about root device.

---

## Common Issues and Fixes

### Issue: "Cannot connect to database"

**Symptom**: Tiering script or `ha-backend` commands fail with Postgres connection error.

**Fix**:
```bash
export $(cat /etc/healtharchive/env.production | xargs)
# Retry command
```

### Issue: "Annual job output directory still on /dev/sda1"

**Symptom**: Worker refuses to start annual job crawl.

**Fix**:
1. Check Storage Box mount (step 1)
2. Re-run tiering script with `--apply --repair-stale-mounts` (step 6)
3. Verify bind mounts are created (step 2)
4. Restart worker (step 7)

### Issue: "Transport endpoint not connected (Errno 107)"

**Symptom**: Cannot access annual job directories.

**Fix**:
```bash
# Unmount stale mount
sudo umount -l /srv/healtharchive/jobs/2025_annual_hc

# Re-run tiering script
sudo /opt/healtharchive-backend/.venv/bin/python3 \
  /opt/healtharchive-backend/scripts/vps-annual-output-tiering.py \
  --year 2025 \
  --apply
```

---

## See Also

- [Production Single VPS Deployment](../../../deployment/production-single-vps.md) - Main VPS runbook
- [Annual Output Tiering Design](../../../planning/implemented/2025-12_annual-output-tiering.md) - Technical details
