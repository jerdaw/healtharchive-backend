# Runbook: Crawl Restart Budget Low

**Alert Name:** `HealthArchiveCrawlContainerRestartsHigh`
**Severity:** Warning

## Trigger

This alert fires when a running crawl has consumed most of its adaptive restart
budget for at least 30 minutes:

- `hc`: `container_restarts_done >= 19` (budget `24`)
- `phac`: `container_restarts_done >= 24` (budget `30`)
- `cihr`: `container_restarts_done >= 16` (budget `20`)
- other sources: `container_restarts_done >= 16`

## Meaning

The crawl is at elevated risk of hard failure if restart churn continues. The
usual root causes are:

- repeated navigation or HTTP timeout churn on the source site
- storage instability on the job hot path (`Errno 107`, unreadable logs/state)
- stale running state or stale metrics after the crawl stopped making progress

Treat this as a classification problem first, not a “raise the budget” problem.

## Quick Diagnosis

Start with a read-only snapshot on the VPS:

```bash
cd /opt/healtharchive-backend

./scripts/vps-crawl-status.sh --year 2026 --job-id <JOB_ID> --recent-lines 20000

curl -s http://127.0.0.1:9100/metrics | rg 'healtharchive_crawl_running_job_(container_restarts_done|last_progress_age_seconds|stalled|crawl_rate_ppm|output_dir_ok|output_dir_errno|log_probe_ok|log_probe_errno|state_file_ok|state_parse_ok|temp_dirs_count|errors_timeout|errors_http|errors_other)\{job_id="<JOB_ID>"'

set -a; source /etc/healtharchive/backend.env; set +a
/opt/healtharchive-backend/.venv/bin/ha-backend show-job --id <JOB_ID>
sudo journalctl -u healtharchive-worker.service -n 400 --no-pager
docker ps --format 'table {{.ID}}\t{{.Image}}\t{{.Names}}\t{{.Status}}'
```

Classify the incident:

- **Timeout churn**: logs show repeated `Navigation timeout`, `Page load timed out`, HTTP/network failures, or the same URL families before restart messages.
- **Storage instability**: metrics or logs show `output_dir_errno=107`, `log_probe_errno=107`, unreadable state/log files, or permission errors.
- **State/metrics drift**: DB still says `running`, but there is no active crawl container and no fresh `crawlStatus` or WARC activity.

## Confirm Progress Before Recovering

Use the newest combined log and recent WARCs for the job:

```bash
JOBDIR="/srv/healtharchive/jobs/<source>/<JOB_DIR>"
LOG="$(ls -t "${JOBDIR}"/archive_*.combined.log | head -n 1)"

rg -n '"context":"crawlStatus"' "${LOG}" | tail -n 10
rg -n 'Navigation timeout|Page load timed out|ERR_HTTP2_PROTOCOL_ERROR|Transport endpoint is not connected|Permission denied|No space left on device|Attempting adaptive container restart|Max restarts' "${LOG}" | tail -n 200
find "${JOBDIR}" -name '*.warc.gz' -printf '%TY-%Tm-%Td %TT %p\n' 2>/dev/null | sort | tail -n 10
```

Interpretation:

- If `crawlStatus` is still moving and recent WARCs are appearing, keep the job
  running long enough to capture the repeated failing URL or pattern.
- If progress is flat and the job is near or at budget exhaustion, recover it.

## Recovery

Before running any recovery command below, confirm whether the proposed fix depends
on backend repo changes (for example: new scope filters, updated reconcile logic,
or changed worker/watchdog behavior). If it does, deploy that repo change on the
VPS first and verify the live checkout contains it before proceeding. Do not run
reconcile/recover commands against an undeployed fix.

### Storage branch

If the evidence shows hot-path storage failures (`Errno 107`, unreadable
output/log/state paths), follow the canonical storage repair flow:

1. `sudo systemctl stop healtharchive-worker.service`
2. Repair the stale mount using:
   `docs/operations/playbooks/storage/storagebox-sshfs-stale-mount-recovery.md`
3. Recover the stale job row:

```bash
set -a; source /etc/healtharchive/backend.env; set +a
/opt/healtharchive-backend/.venv/bin/ha-backend recover-stale-jobs --older-than-minutes 5 --apply --source <source> --limit 1
sudo systemctl start healtharchive-worker.service
```

Do not increase restart budgets until storage is healthy.

### Timeout / stalled crawl branch

If storage is healthy but the crawl is churning on timeouts or no longer making
progress:

```bash
sudo systemctl stop healtharchive-worker.service

set -a; source /etc/healtharchive/backend.env; set +a
/opt/healtharchive-backend/.venv/bin/ha-backend recover-stale-jobs \
  --older-than-minutes 5 \
  --require-no-progress-seconds 3600 \
  --apply \
  --source <source> \
  --limit 1

sudo systemctl start healtharchive-worker.service
```

If the same URLs keep repeating across retries, capture them and consider scope
or timeout tuning after the incident is stabilized.

### State / metrics drift branch

If the row is stale (`running` in DB, no live crawl process, no fresh progress),
reconcile it before assuming the crawl is still active:

```bash
sudo systemctl stop healtharchive-worker.service

set -a; source /etc/healtharchive/backend.env; set +a
/opt/healtharchive-backend/.venv/bin/ha-backend recover-stale-jobs --older-than-minutes 5 --apply --source <source> --limit 1

sudo systemctl start healtharchive-worker.service
```

Then confirm the per-job metrics no longer show the old running state.

## Escalation Guidance

Only increase `max_container_restarts` when all of these are true:

- storage is healthy
- the crawl is still producing fresh `crawlStatus` updates and recent WARCs
- the restart churn looks intermittent rather than continuous thrash

If those conditions are not met, raising the budget usually extends a broken run
without improving completeness.
