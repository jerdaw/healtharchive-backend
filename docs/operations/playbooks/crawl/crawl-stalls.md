# Crawl stalls (monitoring + recovery)

Use this playbook when a crawl job is **running** but appears **stalled** (no progress for an extended period), or when you receive the `HealthArchiveCrawlStalled` alert.

Quick triage (recommended first):

```bash
cd /opt/healtharchive-backend
./scripts/vps-crawl-status.sh --year 2026
```

Notes:

- The status script is read-only (no restarts, no DB writes); it’s safe mid-crawl.
- If the combined log is very large and you only want **recent** timeout signals, use:
  - `./scripts/vps-crawl-status.sh --year 2026 --recent-lines 20000`

## 1) Identify the stalled job

On the VPS:

```bash
set -a; source /etc/healtharchive/backend.env; set +a
/opt/healtharchive-backend/.venv/bin/ha-backend list-jobs --status running --limit 10
```

Then inspect the specific job:

```bash
/opt/healtharchive-backend/.venv/bin/ha-backend show-job --id JOB_ID
```

## 2) Confirm “no progress”

Find the newest combined log for the job’s output directory:

```bash
JOBDIR="/srv/healtharchive/jobs/SOURCE/YYYYMMDDTHHMMSSZ__name"
ls -lt "${JOBDIR}"/archive_*.combined.log | head -n 5
LOG="$(ls -t "${JOBDIR}"/archive_*.combined.log | head -n 1)"
```

Check the most recent crawlStatus line(s):

```bash
rg -n '"context":"crawlStatus"' "${LOG}" | tail -n 5
```

If `crawled` is not increasing for a long time (often with repeated `Navigation timeout` warnings), treat it as stalled.

## 3) Recovery (safe-by-default)

If you confirm the crawl is stalled and you want to restart it, do:

```bash
# Stop the worker (interrupts the current crawl process).
sudo systemctl stop healtharchive-worker.service

# Mark the running job retryable so the worker can pick it up again.
set -a; source /etc/healtharchive/backend.env; set +a
/opt/healtharchive-backend/.venv/bin/ha-backend recover-stale-jobs \
  --older-than-minutes 5 \
  --require-no-progress-seconds 3600 \
  --apply \
  --source SOURCE \
  --limit 5

# Start the worker again.
sudo systemctl start healtharchive-worker.service
```

Then confirm the worker picked the job up again and crawlStatus is moving:

```bash
sudo systemctl status healtharchive-worker.service --no-pager
sudo journalctl -u healtharchive-worker.service -n 50 --no-pager
```

## Notes

- `archive_tool` has built-in monitoring/adaptation; most stalls should self-heal, but this recovery is the “break glass” operator workflow.
- Optional: you can enable the `healtharchive-crawl-auto-recover.timer` watchdog (sentinel: `/etc/healtharchive/crawl-auto-recover-enabled`) once you’re confident in the thresholds/caps.
- To periodically validate the watchdog logic safely on production, run the drills in:
  - `crawl-auto-recover-drills.md`
- The watchdog is designed to avoid interrupting a healthy crawl; when another job is actively making progress, it may “soft recover” zombie `status=running` jobs by marking them `retryable` without restarting the worker.
- If enabled via systemd, the watchdog can also **auto-start** underfilled annual jobs (`--ensure-min-running-jobs`) to maintain concurrency.
  - See: `docs/operations/thresholds-and-tuning.md` and the “queue fill / auto-start” drills in `crawl-auto-recover-drills.md`.
- If the watchdog is enabled but prints `SKIP ... max recoveries reached`, you can still do the manual recovery above, or (carefully) run the watchdog script once with a higher cap:
  ```bash
  sudo bash -lc 'set -a; source /etc/healtharchive/backend.env; set +a; /opt/healtharchive-backend/.venv/bin/python3 /opt/healtharchive-backend/scripts/vps-crawl-auto-recover.py --apply --max-recoveries-per-job-per-day 4'
  ```
- If stalls repeat for the same URL(s), consider narrowing scope rules or adjusting crawler timeouts in the source’s job configuration.
