# Monitoring & Alerting Strategy - Annual Crawl Campaign

**Last Updated:** 2026-01-27

## Overview

This document defines the monitoring strategy for the HealthArchive annual crawl campaign. We use a combination of systemd timers, Python scripts, and Prometheus `node_exporter` textfile collectors to expose custom metrics about crawl health, restart stability, and progress.

## Metric Sources

Custom metrics are written to the node_exporter textfile collector directory:

- `/var/lib/node_exporter/textfile_collector/`

Primary files (single-VPS annual campaign):

- `healtharchive_crawl.prom`
  - Written by `scripts/vps-crawl-metrics-textfile.py`
  - Triggered every **1 minute** by `healtharchive-crawl-metrics.timer`
- `healtharchive_storage_hotpath_auto_recover.prom`
  - Written by `scripts/vps-storage-hotpath-auto-recover.py`
  - Triggered every **1 minute** by `healtharchive-storage-hotpath-auto-recover.timer` (sentinel-gated)
- `healtharchive_worker_auto_start.prom`
  - Written by `scripts/vps-worker-auto-start.py`
  - Triggered every **2 minutes** by `healtharchive-worker-auto-start.timer` (sentinel-gated)

### Key Metric Families

| Metric Name | Type | Description |
| :--- | :--- | :--- |
| `healtharchive_crawl_running_jobs` | Gauge | Count of currently active jobs in the DB. |
| `healtharchive_worker_active` | Gauge | 1 = worker systemd unit is active. |
| `healtharchive_jobs_pending_crawl` | Gauge | Count of jobs in `status in (queued, retryable)`. |
| `healtharchive_jobs_infra_error_recent_total{minutes="10"}` | Gauge | Count of jobs recently failing due to infra errors (windowed). |
| `healtharchive_worker_should_be_running` | Gauge | 1 = pending crawl jobs exist and Storage Box mount is readable. |
| `healtharchive_crawl_running_job_state_file_ok` | Gauge | 1 = `.archive_state.json` is readable and valid. 0 = Probe failed (SSHFS/Permissions issue). |
| `healtharchive_crawl_running_job_container_restarts_done` | Gauge | Cumulative count of Zimit container restarts for the current job. |
| `healtharchive_crawl_running_job_last_progress_age_seconds` | Gauge | Time since the last "pages crawled" increment in the logs. |
| `healtharchive_crawl_running_job_stalled` | Gauge | 1 = Progress stalled > 1 hour. |
| `healtharchive_crawl_running_job_output_dir_ok` | Gauge | 1 = Output directory is accessible. |
| `healtharchive_crawl_running_job_log_probe_ok` | Gauge | 1 = Combined log file is readable. |
| `healtharchive_crawl_running_job_crawl_rate_ppm` | Gauge | Pages per minute crawl rate (from state file). |
| `healtharchive_crawl_running_job_progress_known` | Gauge | 1 = Progress metrics available from state file. |
| `healtharchive_crawl_metrics_timestamp_seconds` | Gauge | Unix timestamp when metrics were last written. |
| `healtharchive_jobs_infra_error_recent_total{window="10m"}` | Gauge | Count of jobs with infra errors in rolling window. |

## Alerting Thresholds

Alerts are defined in:

- `ops/observability/alerting/healtharchive-alerts.yml`

### 1) Worker availability (high-signal)

**Alert:** `HealthArchiveWorkerDownWhileJobsPending`

- **Threshold:** `healtharchive_worker_should_be_running == 1 and healtharchive_worker_active == 0` for 10m.
- **Meaning:** There is pending crawl work and storage appears usable, but the worker service is down.
- **Action:** Check `healtharchive-worker.service` logs and Storage Box hot-path health. If automation is enabled, check `healtharchive-storage-hotpath-auto-recover.timer` + state.

### 2) SSHFS/Mount Stability

**Alert:** `HealthArchiveCrawlOutputDirUnreadable` (and related probe alerts)

- **Threshold:** `healtharchive_crawl_running_job_output_dir_ok == 0` for 2m.
- **Meaning:** A running crawl job cannot access its output directory. Errno 107 typically means a stale SSHFS/FUSE mount.
- **Action:** Follow the Storage Box stale mount recovery playbook and/or ensure hot-path auto-recover is enabled and succeeding.

**Alert:** `HealthArchiveStorageHotpathStaleUnrecovered`

- **Threshold:** `healtharchive_storage_hotpath_auto_recover_detected_targets > 0` for 10m (when the automation is enabled).
- **Meaning:** Hot-path auto-recover still sees stale/unreadable paths after 10 minutes.
- **Action:** Inspect `/srv/healtharchive/ops/watchdog/storage-hotpath-auto-recover.json` and consider manual unmount + tiering re-apply.

### 3) Restart stability

**Alert:** `HealthArchiveCrawlContainerRestartsHigh`

- **Threshold:** `healtharchive_crawl_running_job_container_restarts_done >= 10` (for 15m).
- **Meaning:** The crawler is requiring many adaptive container restarts; this can be a normal resiliency mechanism, but sustained growth can indicate timeouts or I/O instability.
- **Action:** Review worker logs and combined logs around restarts; check for repeated timeouts on the same URL or storage errors.

### 4) Progress Stalls

**Alert:** `HealthArchiveCrawlStalled`

- **Threshold:** `healtharchive_crawl_running_job_stalled == 1` (for 30m).
- **Meaning:** The crawler is running but hasn't archived a new page in over an hour.
- **Action:** Check if the crawler is stuck on a massive PDF or looped trap.

### 5) Crawl Rate (throughput)

**Alert:** `HealthArchiveCrawlRateSlow`

- **Threshold:** `healtharchive_crawl_running_job_crawl_rate_ppm < 5` (for 30m, when progress is known).
- **Meaning:** The crawler is running but archiving fewer than 5 pages per minute for an extended period.
- **Action:** Check for network issues, site rate limiting, or resource constraints. Consider adjusting worker count or Docker resource limits.

### 6) Infrastructure Errors

**Alert:** `HealthArchiveInfraErrorsHigh`

- **Threshold:** `healtharchive_jobs_infra_error_recent_total{window="10m"} >= 3` (for 5m).
- **Meaning:** Multiple jobs are failing due to infrastructure errors (errno 107 stale mount, permission denied, etc.) in a short window.
- **Action:** Check Storage Box mount health, run hot-path recovery, verify output directory permissions.

### 7) Metrics Freshness

**Alert:** `HealthArchiveCrawlMetricsStale`

- **Threshold:** `(time() - healtharchive_crawl_metrics_timestamp_seconds) > 600` (for 5m).
- **Meaning:** The crawl metrics textfile hasn't been updated in over 10 minutes.
- **Action:** Check if `healtharchive-crawl-metrics.timer` is running and `vps-crawl-metrics-textfile.py` is succeeding.

## Indexing Monitoring

Indexing runs **after** the crawl completes.

- **Active Indexing:** Check worker logs for `Indexing for job <ID> completed successfully`.
- **Failure Detection:** `healtharchive_job_crawl_status{status="completed"}` AND `healtharchive_job_indexed_pages == 0` for > 1 hour indicates a broken pipeline.
