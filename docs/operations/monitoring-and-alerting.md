# Monitoring & Alerting Strategy - Annual Crawl Campaign

**Last Updated:** 2026-02-23

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
| `healtharchive_worker_auto_start_last_run_timestamp_seconds` | Gauge | Last worker auto-start watchdog run time (freshness signal when enabled). |
| `healtharchive_worker_auto_start_last_result{result,reason}` | Gauge | Last worker auto-start watchdog outcome (one-hot by labels). |
| `healtharchive_worker_auto_start_start_attempts_total` | Counter | Total worker auto-start attempts (with success/fail companion counters). |
| `healtharchive_crawl_auto_recover_last_run_timestamp_seconds` | Gauge | Last crawl auto-recover watchdog run time (freshness signal when enabled). |
| `healtharchive_crawl_running_job_state_file_ok` | Gauge | 1 = `.archive_state.json` is readable and valid. 0 = Probe failed (SSHFS/Permissions issue). |
| `healtharchive_crawl_running_job_container_restarts_done` | Gauge | Cumulative count of Zimit container restarts for the current job. |
| `healtharchive_crawl_running_job_last_progress_age_seconds` | Gauge | Time since the last "pages crawled" increment in the logs. |
| `healtharchive_crawl_running_job_stalled` | Gauge | 1 = Progress stalled > 1 hour. |
| `healtharchive_crawl_running_job_output_dir_ok` | Gauge | 1 = Output directory is accessible. |
| `healtharchive_crawl_annual_pending_job_output_dir_writable{source,job_id,status,year}` | Gauge | 1 = Queued/retryable annual job output dir would be writable by the worker user (permission drift detection). |
| `healtharchive_crawl_running_job_log_probe_ok` | Gauge | 1 = Combined log file is readable. |
| `healtharchive_crawl_running_job_crawl_rate_ppm` | Gauge | Pages per minute crawl rate (from crawlStatus log window). |
| `healtharchive_crawl_running_job_new_crawl_phase_count` | Gauge | Count of `New Crawl Phase` stage starts seen in the current combined-log tail window. |
| `healtharchive_crawl_running_job_progress_known` | Gauge | 1 = Progress metrics parsed from crawlStatus logs. |
| `healtharchive_crawl_metrics_timestamp_seconds` | Gauge | Unix timestamp when metrics were last written. |
| `healtharchive_jobs_infra_error_recent_total{window="10m"}` | Gauge | Count of jobs with infra errors in rolling window. |

## Alerting Thresholds

Alerts are defined in:

- `ops/observability/alerting/healtharchive-alerts.yml`

## Alerting Policy (automation-first)

The annual crawl alerting policy is now **automation-first**:

- Built-in watchdogs (worker auto-start, crawl auto-recover, storage hot-path auto-recover) should get a chance to self-heal first.
- Alerts should page/notify primarily when:
  - automation is disabled or unavailable,
  - automation telemetry is stale (you can no longer trust suppression), or
  - automation failed / the condition persisted after the watchdog had multiple runs.
- Crawl throughput and crawl-phase churn are treated as **dashboard signals** (trend analysis), not direct notification signals.

In practice this means:

- `Errno 107` stale-mount symptoms are escalated via storage watchdog alerts, not duplicate per-job output-dir alerts.
- `HealthArchiveWorkerDownWhileJobsPending` waits longer and suppresses while the deploy lock is active when worker auto-start automation is enabled and healthy.
- Separate watchdog-freshness alerts protect against “silent” suppression caused by stopped timers/scripts.

### 1) Worker availability (high-signal, post-auto-start)

**Alert:** `HealthArchiveWorkerDownWhileJobsPending`

- **Threshold (effective):**
  - Base condition: `healtharchive_worker_should_be_running == 1 and healtharchive_worker_active == 0`
  - With worker auto-start enabled and fresh metrics: only alerts after the condition persists for **20m** and the deploy lock is not active.
  - Fallback: if worker auto-start automation is disabled/absent, still alerts on the same base symptom (with the same alert rule).
- **Meaning:** There is pending crawl work and storage appears usable, but the worker service remains down after the automation-first window (or automation is unavailable).
- **Action:** Check `healtharchive-worker.service` logs, recent deploy activity, and worker auto-start watchdog state/metrics.

### 2) SSHFS/Mount Stability

**Alert:** `HealthArchiveCrawlOutputDirUnreadable` (and related probe alerts)

- **Threshold:** `healtharchive_crawl_running_job_output_dir_ok == 0` and `output_dir_errno != 107` for 2m.
- **Meaning:** A running crawl job cannot access its output directory for a **non-stale-mount** reason (permissions/path/etc.).
- **Action:** Investigate the specific non-107 error. `Errno 107` stale mount cases should escalate via the storage hot-path watchdog alerts below.

**Alert:** `HealthArchiveAnnualOutputDirNotWritable`

- **Threshold:** probe-user OK, writable probe = 0, and `writable_errno != 107` for 10m.
- **Meaning:** A queued/retryable annual job output dir is not writable for a **non-stale-mount** reason (commonly permission drift / `Errno 13`).
- **Action:** Run crawl preflight checks for the specific job output dir mount and writability; re-apply annual output tiering if needed.

**Alert:** `HealthArchiveStorageHotpathStaleUnrecovered`

- **Threshold:** `healtharchive_storage_hotpath_auto_recover_detected_targets > 0` for 10m (when the automation is enabled).
- **Meaning:** Hot-path auto-recover still sees stale/unreadable paths after 10 minutes.
- **Action:** Inspect `/srv/healtharchive/ops/watchdog/storage-hotpath-auto-recover.json` and consider manual unmount + tiering re-apply.

**Alert:** `HealthArchiveStorageHotpathApplyFailedPersistent`

- **Threshold:** watchdog enabled, at least one apply attempt, `last_apply_ok == 0`, and last apply timestamp older than 24h (for 30m).
- **Meaning:** Hot-path auto-recover apply mode has remained in a failed terminal state for over a day.
- **Action:** Inspect `/srv/healtharchive/ops/watchdog/storage-hotpath-auto-recover.json` (`last_apply_errors`, `last_apply_warnings`), then follow stale mount recovery playbook steps and re-run a controlled dry-run/apply verification.

### 3) Restart stability

**Alert:** `HealthArchiveCrawlContainerRestartsHigh`

- **Threshold:** restart budget near exhaustion (for 30m):
  - HC: `healtharchive_crawl_running_job_container_restarts_done{source="hc"} >= 19` (budget 24)
  - PHAC: `healtharchive_crawl_running_job_container_restarts_done{source="phac"} >= 24` (budget 30)
  - CIHR: `healtharchive_crawl_running_job_container_restarts_done{source="cihr"} >= 16` (budget 20)
- **Meaning:** The crawler has consumed most of its adaptive restart budget and is at higher risk of hard failure if churn continues.
- **Action:** Review worker logs and combined logs around restarts; check for repeated timeouts on the same URL or storage errors before the job exhausts its restart budget.

### 4) Progress Stalls

**Alert:** `HealthArchiveCrawlStalled`

- **Threshold:** `healtharchive_crawl_running_job_stalled == 1` (for 30m).
- **Meaning:** The crawler is running but hasn't archived a new page in over an hour, and the stall persisted long enough to warrant manual review even if crawl auto-recover is enabled.
- **Action:** Check if the crawler is stuck on a massive PDF or looped trap. If crawl auto-recover is enabled, also inspect its watchdog state/metrics to confirm whether automation attempted recovery.

### 5) Infrastructure Errors

**Alert:** `HealthArchiveInfraErrorsHigh`

- **Threshold:** `healtharchive_jobs_infra_error_recent_total{window="10m"} >= 3` (for 5m).
- **Meaning:** Multiple jobs are failing due to infrastructure errors (errno 107 stale mount, permission denied, etc.) in a short window.
- **Action:** Check Storage Box mount health, run hot-path recovery, verify output directory permissions.

### 6) Metrics Freshness

**Alert:** `HealthArchiveCrawlMetricsStale`

- **Threshold:** `(time() - healtharchive_crawl_metrics_timestamp_seconds) > 600` (for 5m).
- **Meaning:** The crawl metrics textfile hasn't been updated in over 10 minutes.
- **Action:** Check if `healtharchive-crawl-metrics.timer` is running and `vps-crawl-metrics-textfile.py` is succeeding.

**Alert:** `HealthArchiveWorkerAutoStartMetricsStale`

- **Threshold:** worker auto-start enabled and `(time() - last_run_timestamp) > 600` (for 5m).
- **Meaning:** Worker auto-start automation is enabled, but its metrics are stale. You should not assume worker-down alerts are still automation-aware until this is fixed.
- **Action:** Check `healtharchive-worker-auto-start.timer` and `healtharchive-worker-auto-start.service` logs/state.

**Alert:** `HealthArchiveCrawlAutoRecoverMetricsStale`

- **Threshold:** crawl auto-recover enabled and `(time() - last_run_timestamp) > 900` (for 10m).
- **Meaning:** Crawl auto-recover automation is enabled, but its metrics are stale. Automation-first stall recovery may not be running.
- **Action:** Check `healtharchive-crawl-auto-recover.timer` and `healtharchive-crawl-auto-recover.service` logs/state.

## Dashboard-only Crawl Performance Signals (no direct notifications)

These are still monitored, but via Grafana trend panels instead of Alertmanager notifications:

- `healtharchive_crawl_running_job_crawl_rate_ppm`
- `healtharchive_crawl_running_job_new_crawl_phase_count`
- `healtharchive_crawl_running_job_last_progress_age_seconds`
- `healtharchive_crawl_running_job_container_restarts_done`

Use:

- `ops/observability/dashboards/healtharchive-pipeline-health.json`

The dashboard includes longitudinal crawl-rate panels (raw + 30m average) and watchdog activity/freshness panels to support investigation without alert spam.

## Indexing Monitoring

Indexing runs **after** the crawl completes.

- **Active Indexing:** Check worker logs for `Indexing for job <ID> completed successfully`.
- **Failure Detection:** `healtharchive_job_crawl_status{status="completed"}` AND `healtharchive_job_indexed_pages == 0` for > 1 hour indicates a broken pipeline.
