# Operational Thresholds and Tuning Guide

This document centralizes all operational thresholds used in HealthArchive automation, monitoring, and safeguards.

**Last updated**: 2026-02-06

---

## Overview

HealthArchive uses conservative thresholds to prevent runaway automation and ensure system stability. All automation is opt-in via sentinel files and includes multiple safety caps.

**General principles**:
- Automation is safe-by-default (dry-run unless explicitly enabled)
- Rate limits prevent flapping (cooldowns + per-hour/day caps)
- Deploy locks prevent conflicts during maintenance
- All thresholds are tunable but have sensible defaults

---

## Disk Management Thresholds

### Worker Pre-Crawl Disk Check

| Parameter | Value | Location | Rationale |
|-----------|-------|----------|-----------|
| **Threshold** | 85% | `src/ha_backend/worker/main.py` (`DISK_HEADROOM_THRESHOLD_PERCENT`) | Allows ~11GB buffer for multi-GB annual crawls |
| **Check frequency** | Every job selection | Worker loop | Prevents mid-crawl disk-full failures |
| **Action** | Skip job selection | Worker logs warning | Jobs remain queued until space is freed |

**Tuning guidance**:
- Lower to 80% if crawls are consistently small (e.g., test jobs)
- Raise to 88% if disk is oversized and buffer is excessive
- Don't raise above 90% - leaves too little margin for error

**Related alerts**: `HealthArchiveDiskUsageHigh` (warning), `HealthArchiveDiskUsageCritical`

---

### Alerting Thresholds

| Severity | Threshold | Duration | Location | Action |
|----------|-----------|----------|----------|--------|
| **Warning** | >85% | 30 minutes | `ops/observability/alerting/healtharchive-alerts.yml` (`HealthArchiveDiskUsageHigh`) | Page on-call during business hours |
| **Critical** | >92% | 10 minutes | `ops/observability/alerting/healtharchive-alerts.yml` (`HealthArchiveDiskUsageCritical`) | Page on-call immediately |

**Tuning guidance**:
- Warning duration (30min) gives time to react without false positives
- Critical threshold (92%) leaves ~6GB for emergency response
- Don't raise critical above 95% - risk of sudden disk-full

See: `docs/operations/disk-baseline-and-cleanup.md` (current baseline + cleanup posture)

---

### Cleanup Automation

| Parameter | Value | Location | Purpose |
|-----------|-------|----------|---------|
| **Min age** | 14 days | `ops/automation/cleanup-automation.toml` (`min_age_days`) | Avoid cleaning recent jobs |
| **Keep latest per source** | 2 | `ops/automation/cleanup-automation.toml` (`keep_latest_per_source`) | Preserve recent snapshots |
| **Max jobs per run (weekly)** | 1 | `ops/automation/cleanup-automation.toml` (`max_jobs_per_run`) | Conservative incremental cleanup |
| **Threshold trigger** | 80% | `ops/automation/cleanup-automation.toml` (`threshold_trigger_percent`) | Only run threshold cleanup when disk exceeds this |
| **Max jobs per run (threshold)** | 5 | `ops/automation/cleanup-automation.toml` (`threshold_max_jobs_per_run`) | More aggressive cleanup under disk pressure |
| **Cleanup mode** | `temp-nonwarc` | `scripts/vps-cleanup-automation.py` (`ha-backend cleanup-job --mode temp-nonwarc`) | Preserves WARCs (safe) |

**Tuning guidance**:
- Increase `threshold_max_jobs_per_run` to 7-10 only if disk pressure is chronic and the cleanup is consistently safe
- Decrease `min_age_days` to 7 if disk pressure is chronic
- Increase `keep_latest_per_source` to 3+ if operators need more history

**Implementation notes**:
- Weekly cleanup: `docs/deployment/systemd/healtharchive-cleanup-automation.service` + `docs/deployment/systemd/healtharchive-cleanup-automation.timer`
- Disk threshold cleanup: `docs/deployment/systemd/healtharchive-disk-threshold-cleanup.service` + `docs/deployment/systemd/healtharchive-disk-threshold-cleanup.timer` (runs every 30 min; no-op when below threshold)

---

## Crawl Recovery Thresholds

### Stall Detection

| Parameter | Value | Location | Rationale |
|-----------|-------|----------|-----------|
| **Stall threshold** | 3600s (60 min) | `scripts/vps-crawl-auto-recover.py` (`--stall-threshold-seconds`, default: 3600) | Balance between false positives and timely recovery |
| **Progress metric** | `crawled` count unchanged | Parsed from combined log | Reliable indicator of actual progress |
| **Guard window** | 600s (10 min) | `scripts/vps-crawl-auto-recover.py` (`--skip-if-any-job-progress-within-seconds`, default: 600) | Avoid interrupting healthy crawls |

**Tuning guidance**:
- Lower to 1800s (30min) for fast sites (e.g., small test crawls)
- Raise to 5400s (90min) or 7200s (120min) for very slow sites or flaky networks
- Don't lower below 1800s (30min) — risks false positives during normal slow periods

---

### Recovery Rate Limits

| Parameter | Value | Location | Rationale |
|-----------|-------|----------|-----------|
| **Per-job daily cap** | 3 | `scripts/vps-crawl-auto-recover.py` (`--max-recoveries-per-job-per-day`, default: 3) | Prevents restart loops for fundamentally broken jobs |
| **Soft recovery enabled** | True | `scripts/vps-crawl-auto-recover.py` (`--soft-recover-when-guarded`, default: true) | Mark stalled jobs retryable without stopping healthy crawls |

**Tuning guidance**:
- Increase per-job cap to 5 for known-flaky sources (e.g., sites with frequent timeouts)
- Disable soft recovery (`--no-soft-recover-when-guarded`) only for debugging

**Recovery enhancements** (auto-applied):
- `enable_adaptive_restart=True`
- `max_container_restarts` floor from source profile (`hc=24`, `phac=30`, `cihr=20`)
- See: `scripts/vps-crawl-auto-recover.py` (`_ensure_recovery_tool_options`)

---

## Crawl Auto-Start (Queue Fill)

When enabled, the crawl auto-recover watchdog can also act as a **queue fill** mechanism:
if there are **no stalled jobs**, but the annual campaign is running **fewer than N jobs**, it can auto-start
one queued/retryable annual job via `systemd-run`.

This is designed to avoid the operational failure mode where a stalled job gets marked `retryable` but never
returns to running because the worker is already busy with another crawl.

### Auto-Start Thresholds

| Parameter | Value | Location | Rationale |
|-----------|-------|----------|-----------|
| **Min running jobs** | 3 | `docs/deployment/systemd/healtharchive-crawl-auto-recover.service` (`--ensure-min-running-jobs`) | Keep annual campaign concurrency stable |
| **Per-job daily cap** | 3 | `scripts/vps-crawl-auto-recover.py` (`--max-starts-per-job-per-day`, default: 3) | Prevent auto-start loops |
| **Disk safety limit** | 88% | `docs/deployment/systemd/healtharchive-crawl-auto-recover.service` (`--start-max-disk-usage-percent`) | Avoid starting new crawls when disk is near full |

**Implementation notes**:
- Auto-start only considers jobs with `config.campaign_kind="annual"` and matching `config.campaign_year`.
- Auto-start runs the job using `systemd-run` (detached) and applies Docker caps via env vars:
  - `HEALTHARCHIVE_DOCKER_CPU_LIMIT` (default: 1.0; configurable via `--start-docker-cpu-limit`)
  - `HEALTHARCHIVE_DOCKER_MEMORY_LIMIT` (default: 3g; configurable via `--start-docker-memory-limit`)

---

## Storage Hot-Path Recovery Thresholds

### Stale Mount Detection

| Parameter | Value | Location | Rationale |
|-----------|-------|----------|-----------|
| **Min failure age** | 120s (2 min) | `scripts/vps-storage-hotpath-auto-recover.py` (`--min-failure-age-seconds`, default: 120) | Avoid acting on transient failures |
| **Confirm runs** | 2 consecutive | `scripts/vps-storage-hotpath-auto-recover.py` (`--confirm-runs`, default: 2) | Require persistence before acting |
| **Detection signal** | Errno 107 | Probed via `os.stat()` | "Transport endpoint is not connected" |

**Probed locations**:
1. Running job output dirs
2. Next queued/retryable job output dirs (prevents retry storms)
3. Manifest hot paths (tiering bind mounts)

**Tuning guidance**:
- Don't lower `min_failure_age` - transient failures are common
- Don't reduce `confirm_runs` - single observations may be false positives

---

### Recovery Rate Limits

| Parameter | Value | Location | Rationale |
|-----------|-------|----------|-----------|
| **Cooldown** | 15 minutes | `scripts/vps-storage-hotpath-auto-recover.py` (`--cooldown-seconds`, default: 900) | Prevent flapping after recovery |
| **Hourly cap** | 2 | `scripts/vps-storage-hotpath-auto-recover.py` (`--max-recoveries-per-hour`, default: 2) | Global safety limit |
| **Daily cap** | 6 global, 3/job | `scripts/vps-storage-hotpath-auto-recover.py` (`--max-recoveries-per-day`, default: 6; `--max-recoveries-per-job-per-day`, default: 3) | Prevent runaway automation |

**Tuning guidance**:
- Increase cooldown to 30min if recovery attempts fail repeatedly
- Increase hourly/daily caps cautiously - investigate root cause instead
- Don't bypass caps in automation - they prevent pathological loops

---

## SSHFS Mount Options

| Option | Value | Location | Purpose |
|--------|-------|----------|---------|
| **reconnect** | Enabled | `docs/deployment/systemd/healtharchive-storagebox-sshfs.service` | Auto-reconnect on connection loss |
| **ServerAliveInterval** | 15s | systemd service | Send keepalive every 15 seconds |
| **ServerAliveCountMax** | 3 | systemd service | Disconnect after 3 missed keepalives (45s total) |
| **kernel_cache** | Enabled | systemd service | Performance optimization |

**Tuning guidance**:
- Lower `ServerAliveInterval` to 10s if mounts go stale frequently
- Don't raise `ServerAliveCountMax` - delays detection of stale connections
- `reconnect` should always be enabled

**Known issue**: Stale mounts still occur despite hardened options (root cause under investigation).

See: `docs/planning/implemented/2026-02-01-operational-resilience-improvements.md`

---

## Deploy Lock Protection

| Parameter | Value | Location | Purpose |
|-----------|-------|----------|---------|
| **Max age** | 2 hours | `scripts/vps-crawl-auto-recover.py` + `scripts/vps-storage-hotpath-auto-recover.py` (`--deploy-lock-max-age-seconds`, default: 2h) | Stale lock detection |
| **Lock file** | `/tmp/healtharchive-backend-deploy.lock` | Deploy script + watchdogs | Prevent watchdog/deploy conflicts |
| **Lock mechanism** | `flock` | `scripts/vps-deploy.sh` | Atomic lock acquisition |

**Tuning guidance**:
- Increase max age if deploys routinely take >2 hours (investigate why)
- Don't decrease below 1 hour - normal deploys can take 30-45 minutes

---

## Infra Error Cooldown

| Parameter | Value | Location | Rationale |
|-----------|-------|----------|-----------|
| **Cooldown** | 10 minutes | `src/ha_backend/worker/main.py` (`INFRA_ERROR_RETRY_COOLDOWN_MINUTES`) | Prevent retry storms when infra is unhealthy |
| **Infra errors** | Errno 107, Errno 5, `OSError` during job launch | `src/ha_backend/infra_errors.py` | Infrastructure failures (not crawl failures) |

**Tuning guidance**:
- Increase to 20min if infrastructure is persistently unstable
- Decrease to 5min if false positives are common (careful!)

See: `docs/planning/implemented/2026-01-24-infra-error-and-storage-hotpath-hardening.md`

---

## Archive Tool (Crawler) Adaptive Thresholds

### Annual Per-Source Profiles

Annual jobs are source-tuned (not one-size-fits-all). Canonical values live in `src/ha_backend/job_registry.py` and are reconciled by `scripts/vps-crawl-auto-recover.py` during recovery/auto-start flows.

| Source | Initial workers | Stall timeout | Timeout/HTTP threshold | Backoff | Max restarts | Rationale |
|--------|-----------------|---------------|------------------------|---------|--------------|-----------|
| `hc` | 2 | 75 min | 55 / 55 | 2 min | 24 | Moderate tolerance for canada.ca long-tail behavior. |
| `phac` | 2 | 90 min | 65 / 65 | 3 min | 30 | Highest tolerance due historically high restart churn. |
| `cihr` | 3 | 45 min | 35 / 35 | 1 min | 20 | Faster/cleaner profile to improve throughput and fault detection. |

**Tuning guidance**:
- Change source profiles in `job_registry` first; keep watchdog reconciliation aligned.
- For completeness-first posture, increase tolerance (stall/restart budget) before lowering scope.
- Only reduce thresholds when repeated evidence shows low false-positive restart risk.

### One-Time Annual Backfill/Reconciliation

When migrating an existing campaign from shared defaults to per-source tuning, reconcile existing annual jobs in-place:

```bash
# Review changes first (dry-run)
ha-backend reconcile-annual-tool-options --year 2026

# Apply changes
ha-backend reconcile-annual-tool-options --year 2026 --apply
```

What this command does:
- Reconciles baseline annual values to source profile values (`hc`, `phac`, `cihr`)
- Preserves explicit non-baseline overrides (except restart floor enforcement)
- Ensures annual safety defaults (`enable_monitoring`, `enable_adaptive_restart`, `skip_final_build`, `docker_shm_size=1g`)

See: `src/archive_tool/constants.py`, `scripts/vps-crawl-auto-recover.py`

---

## Summary Table: All Thresholds

| Category | Threshold | Value | Priority | Location |
|----------|-----------|-------|----------|----------|
| **Disk** | Worker headroom | 85% | P0 | `worker/main.py` |
| | Alert warning | 85% for 30m | P1 | alerting YAML |
| | Alert critical | 92% for 10m | P0 | alerting YAML |
| **Crawl** | Stall threshold | 60 min | P1 | `vps-crawl-auto-recover.py` |
| | Recovery cap | 3/job/day | P1 | `vps-crawl-auto-recover.py` |
| | New-crawl-phase churn | >=3 (30m) | P1 | alerting YAML |
| | Slow-rate alert (HC) | <1.5 ppm (30m) | P1 | alerting YAML |
| | Slow-rate alert (PHAC) | <1.5 ppm (30m) | P1 | alerting YAML |
| | Slow-rate alert (CIHR) | <3 ppm (30m) | P1 | alerting YAML |
| **Storage** | Stale mount age | 120s | P1 | `vps-storage-hotpath-auto-recover.py` |
| | Recovery cooldown | 15 min | P1 | `vps-storage-hotpath-auto-recover.py` |
| | Recovery cap | 6/day global | P1 | `vps-storage-hotpath-auto-recover.py` |
| **Infra** | Retry cooldown | 10 min | P1 | `worker/main.py` |
| **SSHFS** | Keepalive interval | 15s | P1 | systemd service |

---

## Tuning Workflow

When adjusting thresholds:

1. **Document the change**: Update this file with new values and rationale
2. **Test in staging** (if available): Validate behavior before production
3. **Monitor metrics**: Watch Prometheus/Grafana for impact
4. **Iterate conservatively**: Small adjustments, measure, repeat
5. **Update automation**: Adjust watchdog caps if needed

**Anti-patterns**:
- Disabling safety caps to "fix" underlying issues
- Tuning based on single incidents without trend analysis
- Raising thresholds indefinitely instead of fixing root cause

---

## Related Documentation

- Disk baseline: `docs/operations/disk-baseline-and-cleanup.md`
- Alerting strategy: `docs/operations/monitoring-and-alerting.md`
- Stale mount playbook: `docs/operations/playbooks/storage/storagebox-sshfs-stale-mount-recovery.md`
- Crawl stall playbook: `docs/operations/playbooks/crawl/crawl-stalls.md`
- Operational resilience improvements: `docs/planning/implemented/2026-02-01-operational-resilience-improvements.md`
