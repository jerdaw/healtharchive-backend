# Runbook: CrawlStateFileProbeFailure

**Alert Name:** `CrawlStateFileProbeFailure`
**Severity:** Warning
**Trigger:** `healtharchive_crawl_running_job_state_file_ok == 0` for 5m.

## Description

The monitoring script on the VPS cannot read the `.archive_state.json` file for a running job. This almost always indicates that the SSHFS mount to the Hetzner StorageBox has dropped or disconnected.

## Impact

- Metrics will flatline.
- Adaptive Worker scaling will pause (cannot read state).
- **The crawl itself might still be running** (container has its own mount namespace), but new output writes might eventually fail if the host mount is totally dead.

## Diagnosis

1. **Check Mounts**:
   ssh to VPS and run:

   ```bash
   findmnt -T /srv/healtharchive/jobs
   ```

   If it returns nothing or shows "unreachable", the mount is gone.

2. **Check StorageBox Connectivity**:

   ```bash
   ping -c 3 u524803.your-storagebox.de
   ```

3. **Check Permissions**:
   If mount is up, check if the file exists and is readable:

   ```bash
   ls -la /srv/healtharchive/jobs/<source>/<job_timestamp>/.archive_state.json
   ```

## Mitigation

1. **Remount SSHFS**:

   ```bash
   sudo systemctl restart healtharchive-storagebox-sshfs.service
   ```

   Verify mount is back:

   ```bash
   df -h | grep storagebox
   ```

2. **Restart Workers (If simple remount fails)**:
   If the mount was stale, the worker process might be hung on I/O.

   ```bash
   sudo systemctl restart healtharchive-worker.service
   ```
