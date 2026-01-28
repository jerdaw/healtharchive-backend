# Storage Box / `sshfs` stale mount recovery (Errno 107)

Use this playbook when HealthArchive crawls/indexing/metrics start failing with:

- `OSError: [Errno 107] Transport endpoint is not connected`

This typically indicates a **stale FUSE mount** (often `sshfs`) where the mountpoint still exists, but basic filesystem operations (`stat`, `ls`, `is_dir`) fail.

Operational note:

- The worker skips jobs that recently failed with `crawler_status=infra_error` for a short cooldown window to prevent retry storms. This reduces alert noise but does not fix the underlying mount issue; use this playbook (or the hot-path auto-recover automation) to repair the stale mountpoint.

For background and the full implementation plan (prevention + automation + integrity), see:

- `../../roadmaps/implemented/2026-01-08-storagebox-sshfs-stale-mount-recovery-and-integrity.md`
- Drills (safe on production): `storagebox-sshfs-stale-mount-drills.md`

---

## Quick triage (60 seconds)

On the VPS (`/opt/healtharchive-backend`):

1) Snapshot current crawl state:

```bash
./scripts/vps-crawl-status.sh --year "$(date -u +%Y)"
```

Optional: if Phase 2 automation has been enabled, check whether it is already
attempting recovery (it is disabled-by-default unless the sentinel exists):

```bash
systemctl status healtharchive-storage-hotpath-auto-recover.timer --no-pager -l || true
ls -la /etc/healtharchive/storage-hotpath-auto-recover-enabled 2>/dev/null || true
cat /srv/healtharchive/ops/watchdog/storage-hotpath-auto-recover.json 2>/dev/null || true
```

If the worker auto-start watchdog is enabled (optional), check it too:

```bash
systemctl status healtharchive-worker-auto-start.timer --no-pager -l || true
ls -la /etc/healtharchive/worker-auto-start-enabled 2>/dev/null || true
cat /srv/healtharchive/ops/watchdog/worker-auto-start.json 2>/dev/null || true
```

2) Confirm Storage Box base mount health:

```bash
mount | rg '/srv/healtharchive/storagebox'
ls -la /srv/healtharchive/storagebox >/dev/null && echo "OK: storagebox readable" || echo "BAD: storagebox unreadable"
```

3) Identify broken “hot paths” (job output dirs):

```bash
./scripts/vps-crawl-status.sh --year "$(date -u +%Y)" | rg '^Output dir:'
ls -la /srv/healtharchive/jobs/hc/  # replace with source path(s) as needed
```

If you see `Transport endpoint is not connected` or `d?????????` for job output dirs, continue.

---

## Recovery procedure (safe ordering)

### 1) Stop the worker

Stop the worker first to prevent repeated filesystem touches while mounts are broken:

```bash
sudo systemctl stop healtharchive-worker.service
```

Note: if `healtharchive-worker-auto-start.timer` is enabled, it may restart the worker while you are mid-repair. Either:

- temporarily disable the timer, or
- temporarily remove `/etc/healtharchive/worker-auto-start-enabled`,

then re-enable after recovery.

Optional: if you suspect a crawler container is still running and stuck on IO, inspect it:

```bash
docker ps --format 'table {{.ID}}\t{{.Image}}\t{{.Names}}\t{{.Status}}' | rg 'zimit|openzim' || true
```

Only stop a container if you’re sure it is part of the broken job and not making progress.

---

### 2) Identify stale mountpoints (targeted)

This incident class often affects **specific job output directories** (not necessarily the whole Storage Box mount).

For each affected job output dir (examples shown):

```bash
mount | rg '/srv/healtharchive/jobs/(hc|phac|cihr)/'
sudo findmnt -T /srv/healtharchive/jobs/hc/<JOB_DIR> || true
```

If `ls` against a path returns `Transport endpoint is not connected`, treat it as stale.

---

### 3) Unmount stale hot paths (use `umount` first, then `-l` only if needed)

For each stale mountpoint, try:

```bash
sudo umount /srv/healtharchive/jobs/<source>/<JOB_DIR>
```

If it fails and the path is still broken/unstat’able, use lazy unmount:

```bash
sudo umount -l /srv/healtharchive/jobs/<source>/<JOB_DIR>
```

Notes:

- Use **targeted** unmounts only (specific job dirs), not broad parent directories.
- `umount -l` is an emergency tool; use it only for confirmed-stale mountpoints.

---

### 4) Re-apply tiering mounts

1) Re-apply WARC tiering bind mounts (manifest-driven):

```bash
sudo ./scripts/vps-warc-tiering-bind-mounts.sh --apply
```

If you have confirmed-stale mountpoints and want the script to attempt targeted
repair automatically (still requires the worker to be stopped first):

```bash
sudo ./scripts/vps-warc-tiering-bind-mounts.sh --apply --repair-stale-mounts
```

If this fails with Errno 107 under `/srv/healtharchive/jobs/imports/...`, unmount those stale import mountpoints too and re-run.

If the systemd unit is in a `failed` state, clear it and re-run (prevents repeated `WarcTieringFailed` alerts):

```bash
systemctl is-failed healtharchive-warc-tiering.service && sudo systemctl reset-failed healtharchive-warc-tiering.service || true
sudo systemctl start healtharchive-warc-tiering.service
```

2) Re-apply annual output tiering (campaign job output dirs → Storage Box):

Preferred (avoids the systemd unit’s internal worker stop/start):

```bash
sudo /opt/healtharchive-backend/.venv/bin/python3 /opt/healtharchive-backend/scripts/vps-annual-output-tiering.py --apply --year "$(date -u +%Y)"
```

If you want the script to attempt targeted repair for stale mountpoints (Errno 107),
pass:

```bash
sudo /opt/healtharchive-backend/.venv/bin/python3 /opt/healtharchive-backend/scripts/vps-annual-output-tiering.py --apply --repair-stale-mounts --year "$(date -u +%Y)"
```

Alternative (uses the systemd unit, which stops/starts the worker internally):

```bash
sudo systemctl start healtharchive-annual-output-tiering.service
```

---

### 5) Recover job state (stuck `running` → `retryable`)

Load the backend env (production DB connection):

```bash
set -a; source /etc/healtharchive/backend.env; set +a
```

Recover stale jobs:

```bash
/opt/healtharchive-backend/.venv/bin/ha-backend recover-stale-jobs --older-than-minutes 5 --apply --limit 25
```

If a job ended up `failed` due to the mount issue and you want it to run again:

```bash
/opt/healtharchive-backend/.venv/bin/ha-backend retry-job --id <JOB_ID>
```

---

### 6) Restart the worker

```bash
sudo systemctl start healtharchive-worker.service
```

---

## Replay note (after mount repairs)

If replay smoke tests start returning `503` for previously indexed jobs after a mount/tiering incident, restart replay to refresh its view of `/srv/healtharchive/jobs`:

```bash
sudo systemctl restart healtharchive-replay.service
sudo systemctl start healtharchive-replay-smoke.service
curl -s http://127.0.0.1:9100/metrics | rg '^healtharchive_replay_smoke_'
```

---

## Validation (confirm we’re actually healthy)

### 1) Worker is running and picking jobs

```bash
sudo systemctl status healtharchive-worker.service --no-pager -l
sudo journalctl -u healtharchive-worker.service -n 80 --no-pager -l
```

### 2) Crawls are making progress (not just “running”)

Pick the active job ID and check progress:

```bash
./scripts/vps-crawl-status.sh --year "$(date -u +%Y)" --job-id <JOB_ID>
```

Look for:

- `crawlStatus` counters increasing over time (`crawled` ticks up).
- `healtharchive_crawl_running_job_stalled == 0`
- `last_progress_age_seconds` small (tens of seconds to a few minutes).
- `healtharchive_crawl_running_job_state_parse_ok == 1` (state file readable; no sshfs weirdness)
- `healtharchive_crawl_running_job_container_restarts_done` not climbing rapidly (avoid restart thrash)
- new `.warc.gz` files appearing under the job’s active temp dir.

### 3) Metrics writers are healthy

```bash
sudo systemctl start healtharchive-crawl-metrics.service
sudo systemctl start healtharchive-tiering-metrics.service
sudo systemctl status healtharchive-crawl-metrics.service healtharchive-tiering-metrics.service --no-pager -l
```

---

## If recovery fails

If hot paths are still unreadable after unmount + tiering reapply:

1) Verify Storage Box base mount is readable:

```bash
ls -la /srv/healtharchive/storagebox >/dev/null && echo OK || echo BAD
sudo systemctl status healtharchive-storagebox-sshfs.service --no-pager -l
```

2) Consider restarting the base mount:

```bash
sudo systemctl restart healtharchive-storagebox-sshfs.service
```

3) Re-run tiering reapply steps (WARC tiering + annual output tiering).

If this becomes a recurring pattern, treat it as an infrastructure incident and follow:

- `incident-response.md`

---

## sshfs tuning options

The `healtharchive-storagebox-sshfs.service` uses these sshfs options:

```
-o reconnect,ServerAliveInterval=15,ServerAliveCountMax=3,kernel_cache
```

These defaults are tuned for reliability:

- `reconnect` - automatically reconnect when the SSH connection drops
- `ServerAliveInterval=15` - send SSH keepalives every 15 seconds
- `ServerAliveCountMax=3` - disconnect after 3 missed keepalives (~45s)
- `kernel_cache` - use kernel caching for better performance

If you experience frequent Errno 107 issues, consider these additional options in
`/etc/healtharchive/storagebox.env` (requires service restart):

| Option | Description | When to use |
|--------|-------------|-------------|
| `ServerAliveCountMax=5` | Increase from 3 to tolerate more keepalive misses | Unreliable network with brief dropouts |
| `ConnectTimeout=30` | Limit initial connection wait | Slow network, avoids long hangs |
| `max_write=65536` | Smaller write chunks | Large file writes cause timeouts |
| `workaround=rename` | Better rename handling | If file moves fail intermittently |
| `auto_cache` | Smarter caching based on mtime | If you see stale data |

Note: Changing sshfs options can have unintended effects on performance and behavior.
Test changes in a non-production environment first.
