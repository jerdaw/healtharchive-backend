# Monitoring & Alerting Strategy - Annual Crawl Campaign

**Last Updated:** 2026-01-19

## Overview

This document defines the monitoring strategy for the HealthArchive annual crawl campaign. We use a combination of systemd timers, Python scripts, and Prometheus `node_exporter` textfile collectors to expose custom metrics about crawl health, restart stability, and progress.

## Metric Sources

All custom metrics are written to:
`/var/lib/node_exporter/textfile_collector/healtharchive_crawl.prom`

The collector script `vps-crawl-metrics-textfile.py` is triggered every **1 minute** by `healtharchive-crawl-metrics.timer`.

### Key Metric Families

| Metric Name | Type | Description |
| :--- | :--- | :--- |
| `healtharchive_crawl_running_jobs` | Gauge | Count of currently active jobs in the DB. |
| `healtharchive_crawl_running_job_state_file_ok` | Gauge | 1 = `.archive_state.json` is readable and valid. 0 = Probe failed (SSHFS/Permissions issue). |
| `healtharchive_crawl_running_job_container_restarts_done` | Gauge | Cumulative count of Zimit container restarts for the current job. |
| `healtharchive_crawl_running_job_last_progress_age_seconds` | Gauge | Time since the last "pages crawled" increment in the logs. |
| `healtharchive_crawl_running_job_stalled` | Gauge | 1 = Progress stalled > 1 hour. |
| `healtharchive_crawl_running_job_output_dir_ok` | Gauge | 1 = Output directory is accessible. |
| `healtharchive_crawl_running_job_log_probe_ok` | Gauge | 1 = Combined log file is readable. |

## Alerting Thresholds

### 1. SSHFS/Mount Stability

**Alert:** `CrawlStateFileProbeFailure`

- **Threshold:** `healtharchive_crawl_running_job_state_file_ok == 0` for 5m.
- **Meaning:** The monitoring script cannot read the job's state file. This usually means the SSHFS mount to the storagebox has dropped or permissions are broken.
- **Action:** Check `findmnt`, re-mount SSHFS.
- **Aggregator:** Also monitors `CrawlOutputDirProbeFailure` and `CrawlLogProbeFailure` for deeper infra visibility.

### 2. Restart Budget Stability

**Alert:** `CrawlRestartBudgetLow`

- **Threshold:** `healtharchive_crawl_running_job_container_restarts_done > 15` (for 30m).
- **Meaning:** The annual job (limit 20 restarts) is nearing exhaustion.
- **Action:** Review worker logs. If restarts are due to "timeout" or "http errors", the adaptive system is working. If restarts are rapid (thrashing), manual intervention might be needed to pause the job.

### 3. Progress Stalls

**Alert:** `CrawlProgressStalled`

- **Threshold:** `healtharchive_crawl_running_job_last_progress_age_seconds > 3600` (1 hour).
- **Meaning:** The crawler is running but hasn't archived a new page in over an hour.
- **Action:** Check if the crawler is stuck on a massive PDF or looped trap.

## Indexing Monitoring

Indexing runs **after** the crawl completes.

- **Active Indexing:** Check worker logs for `Indexing for job <ID> completed successfully`.
- **Failure Detection:** `healtharchive_job_crawl_status{status="completed"}` AND `healtharchive_job_indexed_pages == 0` for > 1 hour indicates a broken pipeline.
