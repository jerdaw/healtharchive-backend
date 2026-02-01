# VPS Deployment Instructions: Disk Threshold Cleanup

**Created**: 2026-02-01
**Feature**: Phase 2.1 - Disk Threshold Cleanup Trigger

---

## Overview

This deploys the disk threshold cleanup automation that prevents disk pressure by cleaning up old jobs when disk usage exceeds 80%.

---

## Step 1: Pull Latest Changes

```bash
# SSH to VPS
ssh vps

# Navigate to repo
cd /opt/healtharchive-backend

# Pull latest code
sudo -u haadmin git pull
```

Expected output: Should show the new files and changes.

---

## Step 2: Install Systemd Units

```bash
# Copy timer unit
sudo cp docs/deployment/systemd/healtharchive-disk-threshold-cleanup.timer \
  /etc/systemd/system/

# Copy service unit
sudo cp docs/deployment/systemd/healtharchive-disk-threshold-cleanup.service \
  /etc/systemd/system/

# Reload systemd
sudo systemctl daemon-reload

# Verify units are recognized
systemctl list-unit-files | grep healtharchive-disk-threshold-cleanup
```

Expected output:
```
healtharchive-disk-threshold-cleanup.service      static
healtharchive-disk-threshold-cleanup.timer        disabled
```

---

## Step 3: Test the Script in Dry-Run Mode

```bash
# Load environment
sudo -u haadmin bash -lc 'set -a; source /etc/healtharchive/backend.env; set +a; \
  /opt/healtharchive-backend/.venv/bin/python3 \
  /opt/healtharchive-backend/scripts/vps-cleanup-automation.py \
  --threshold-mode'
```

Expected output (if disk < 80%):
```
Disk usage at XX%, below threshold 80%. Skipping cleanup.
```

Or (if disk >= 80%):
```
Disk usage at XX%, above threshold 80%. Running aggressive cleanup.
```

---

## Step 4: Test in Apply Mode (Optional)

**ONLY RUN THIS IF DISK IS ABOVE 80% AND YOU WANT TO CLEAN UP JOBS**

```bash
# Apply cleanup
sudo -u haadmin bash -lc 'set -a; source /etc/healtharchive/backend.env; set +a; \
  /opt/healtharchive-backend/.venv/bin/python3 \
  /opt/healtharchive-backend/scripts/vps-cleanup-automation.py \
  --threshold-mode --apply'
```

Check what was cleaned:
```bash
# Last few lines should show:
# healtharchive_cleanup_applied_total X
# healtharchive_cleanup_threshold_triggered 1
```

---

## Step 5: Enable and Start the Timer

```bash
# Enable timer to start on boot
sudo systemctl enable healtharchive-disk-threshold-cleanup.timer

# Start the timer now
sudo systemctl start healtharchive-disk-threshold-cleanup.timer

# Verify timer is active
systemctl status healtharchive-disk-threshold-cleanup.timer
```

Expected output:
```
● healtharchive-disk-threshold-cleanup.timer - HealthArchive disk threshold cleanup timer
     Loaded: loaded (/etc/systemd/system/healtharchive-disk-threshold-cleanup.timer; enabled)
     Active: active (waiting) since ...
```

---

## Step 6: Verify Timer Schedule

```bash
# Show next run time
systemctl list-timers healtharchive-disk-threshold-cleanup.timer
```

Expected output: Should show next run time (within 30 minutes).

---

## Step 7: Monitor Metrics (Optional)

```bash
# Check metrics file (created after first run)
cat /var/lib/node_exporter/textfile_collector/healtharchive_cleanup.prom | grep threshold
```

Expected output:
```
healtharchive_cleanup_threshold_triggered 0
healtharchive_cleanup_disk_usage XX
```

---

## Verification Checklist

- [ ] Git pull successful
- [ ] Systemd units installed and recognized
- [ ] Dry-run test shows correct disk usage check
- [ ] Timer enabled and active
- [ ] Timer scheduled (next run time visible)
- [ ] Metrics file created after first run

---

## Rollback (If Needed)

```bash
# Stop and disable timer
sudo systemctl stop healtharchive-disk-threshold-cleanup.timer
sudo systemctl disable healtharchive-disk-threshold-cleanup.timer

# Remove units
sudo rm /etc/systemd/system/healtharchive-disk-threshold-cleanup.{timer,service}
sudo systemctl daemon-reload
```

---

## Expected Behavior

**When disk < 80%**:
- Script exits immediately with "Skipping cleanup" message
- No jobs are cleaned
- Metrics show `threshold_triggered 0`

**When disk >= 80%**:
- Script runs aggressive cleanup (up to 5 jobs)
- Cleans oldest indexed jobs first
- Respects keep_latest_per_source (keeps 2 most recent per source)
- Metrics show `threshold_triggered 1`

**Timer runs every 30 minutes**:
- Most runs will exit early (disk < 80%)
- Only runs cleanup when disk pressure detected
- Prevents disk from reaching 85% worker threshold

---

## Troubleshooting

**Timer not running?**
```bash
sudo systemctl status healtharchive-disk-threshold-cleanup.timer
sudo journalctl -u healtharchive-disk-threshold-cleanup.timer -f
```

**Service failing?**
```bash
sudo systemctl status healtharchive-disk-threshold-cleanup.service
sudo journalctl -u healtharchive-disk-threshold-cleanup.service -n 50
```

**Check logs**:
```bash
# Show last service run
sudo journalctl -u healtharchive-disk-threshold-cleanup.service -n 100
```

---

## Integration with Existing Cleanup

This threshold-triggered cleanup **coexists** with the existing weekly cleanup timer:
- **Weekly cleanup**: Runs on schedule, cleans 1 job per run (conservative)
- **Threshold cleanup**: Runs every 30min but only cleans if disk > 80%, cleans up to 5 jobs (aggressive)

Both use the same config file and same cleanup logic, just different triggers and job limits.
