# Crawl auto-recover drills (safe on production)

Goal: periodically prove that:

- the **crawl auto-recover watchdog** is installed and runnable, and
- the watchdog would take sensible actions for a stalled job,

…without actually stopping services or writing to the production watchdog state/metrics.

## 0) Safety rules

- **Never run the crawl auto-recover watchdog with `--apply` as part of a drill.**
- For drills, always override:
  - `--state-file` (use a `/tmp/...` path)
  - `--lock-file` (use a `/tmp/...` path)
  - `--textfile-out-dir` (use `/tmp`)
  - `--textfile-out-file` (use a drill filename)

The watchdog enforces this automatically when you use drill flags.

## 1) Pick a job ID to simulate

Pick a real job ID from the database (it does not need to be stalled):

```bash
set -a; source /etc/healtharchive/backend.env; set +a
/opt/healtharchive-backend/.venv/bin/ha-backend list-jobs --status running --limit 10
```

Pick one `job_id` from the output, for example `7`.

## 2) Drill: simulate a stalled job (soft recovery path)

This exercises the common “safe” path where another job is still making progress, so the watchdog would avoid worker restarts.

```bash
cd /opt/healtharchive-backend
sudo bash -lc 'set -a; source /etc/healtharchive/backend.env; set +a; \
  /opt/healtharchive-backend/.venv/bin/python3 /opt/healtharchive-backend/scripts/vps-crawl-auto-recover.py \
    --simulate-stalled-job-id 7 \
    --state-file /tmp/healtharchive-crawl-auto-recover.drill.state.json \
    --lock-file /tmp/healtharchive-crawl-auto-recover.drill.lock \
    --textfile-out-dir /tmp \
    --textfile-out-file healtharchive_crawl_auto_recover.drill.prom'
```

Expected output includes:

- `DRILL: simulate-stalled-job-id active`
- `Planned actions (dry-run):`
- `recover-stale-jobs ... --apply --source ...`

Confirm the drill metrics were written:

```bash
cat /tmp/healtharchive_crawl_auto_recover.drill.prom
```

## 3) Drill: simulate a stalled job (full recovery path)

This forces the watchdog to show the “full recovery” plan (stop worker → mark job retryable → start worker) by disabling the guard window.

```bash
cd /opt/healtharchive-backend
sudo bash -lc 'set -a; source /etc/healtharchive/backend.env; set +a; \
  /opt/healtharchive-backend/.venv/bin/python3 /opt/healtharchive-backend/scripts/vps-crawl-auto-recover.py \
    --skip-if-any-job-progress-within-seconds 0 \
    --simulate-stalled-job-id 7 \
    --state-file /tmp/healtharchive-crawl-auto-recover.full-drill.state.json \
    --lock-file /tmp/healtharchive-crawl-auto-recover.full-drill.lock \
    --textfile-out-dir /tmp \
    --textfile-out-file healtharchive_crawl_auto_recover.full-drill.prom'
```

Expected output includes:

- `Planned actions (dry-run):`
- `systemctl stop healtharchive-worker.service`
- `recover-stale-jobs ... --apply --source ...`
- `systemctl start healtharchive-worker.service`

## 4) Cleanup

Drill artifacts are safe to delete:

```bash
rm -f /tmp/healtharchive-crawl-auto-recover*.state.json
rm -f /tmp/healtharchive-crawl-auto-recover*.lock
rm -f /tmp/healtharchive_crawl_auto_recover*.prom
```

