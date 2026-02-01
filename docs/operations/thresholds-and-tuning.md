# Operational Thresholds and Tuning Guide

This document centralizes all operational thresholds used in HealthArchive automation, monitoring, and safeguards.

**Last updated**: 2026-02-01

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
| **Threshold** | 85% | `src/ha_backend/worker/main.py:DISK_HEADROOM_THRESHOLD_PERCENT` | Allows ~11GB buffer for multi-GB annual crawls |
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
| **Warning** | >85% | 30 minutes | `ops/observability/alerting/healtharchive-alerts.yml:33-44` | Page on-call during business hours |
| **Critical** | >92% | 10 minutes | `ops/observability/alerting/healtharchive-alerts.yml:46-57` | Page on-call immediately |

**Tuning guidance**:
- Warning duration (30min) gives time to react without false positives
- Critical threshold (92%) leaves ~6GB for emergency response
- Don't raise critical above 95% - risk of sudden disk-full

**Current baseline**: 82% disk usage (14GB free on 75GB VPS)

See: `docs/operations/disk-baseline-and-cleanup.md`

---

### Cleanup Automation

| Parameter | Value | Location | Purpose |
|-----------|-------|----------|---------|
| **Min age** | 14 days | `ops/automation/cleanup-automation.toml:min_age_days` | Avoid cleaning recent jobs |
| **Keep latest per source** | 2 | `ops/automation/cleanup-automation.toml:keep_latest_per_source` | Preserve recent snapshots |
| **Max jobs per run** | 1 | `ops/automation/cleanup-automation.toml:max_jobs_per_run` | Conservative incremental cleanup |
| **Cleanup mode** | `temp-nonwarc` | `scripts/vps-cleanup-automation.py:123` | Preserves WARCs (safe) |

**Tuning guidance**:
- Increase `max_jobs_per_run` to 3-5 if disk fills faster than weekly cleanup
- Decrease `min_age_days` to 7 if disk pressure is chronic
- Increase `keep_latest_per_source` to 3+ if operators need more history

**Not yet implemented**: Disk threshold trigger (Phase 2.1 of `2026-02-01-operational-resilience-improvements.md`)

---

## Crawl Recovery Thresholds

### Stall Detection

| Parameter | Value | Location | Rationale |
|-----------|-------|----------|-----------|
| **Stall threshold** | 5400s (90 min) | `scripts/vps-crawl-auto-recover.py:337` | Balance between false positives and timely recovery |
| **Progress metric** | `crawled` count unchanged | Parsed from combined log | Reliable indicator of actual progress |
| **Guard window** | 600s (10 min) | `scripts/vps-crawl-auto-recover.py:342` | Avoid interrupting healthy crawls |

**Tuning guidance**:
- Lower to 3600s (60min) for fast sites (e.g., small test crawls)
- Raise to 7200s (120min) for very slow sites or flaky networks
- Don't lower below 1800s (30min) - risks false positives during normal slow periods

---

### Recovery Rate Limits

| Parameter | Value | Location | Rationale |
|-----------|-------|----------|-----------|
| **Per-job daily cap** | 3 | `scripts/vps-crawl-auto-recover.py:374` | Prevents restart loops for fundamentally broken jobs |
| **Soft recovery enabled** | True | `scripts/vps-crawl-auto-recover.py:350` | Mark stalled jobs retryable without stopping healthy crawls |

**Tuning guidance**:
- Increase per-job cap to 5 for known-flaky sources (e.g., sites with frequent timeouts)
- Disable soft recovery (`--no-soft-recover-when-guarded`) only for debugging

**Recovery enhancements** (auto-applied):
- `enable_adaptive_restart=True`
- `max_container_restarts=20` (annual jobs)
- See: `scripts/vps-crawl-auto-recover.py:175-241` (`_ensure_recovery_tool_options`)

---

## Storage Hot-Path Recovery Thresholds

### Stale Mount Detection

| Parameter | Value | Location | Rationale |
|-----------|-------|----------|-----------|
| **Min failure age** | 120s (2 min) | `scripts/vps-storage-hotpath-auto-recover.py:465` | Avoid acting on transient failures |
| **Confirm runs** | 2 consecutive | `scripts/vps-storage-hotpath-auto-recover.py:471` | Require persistence before acting |
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
| **Cooldown** | 15 minutes | `scripts/vps-storage-hotpath-auto-recover.py:477` | Prevent flapping after recovery |
| **Hourly cap** | 2 | `scripts/vps-storage-hotpath-auto-recover.py:482` | Global safety limit |
| **Daily cap** | 6 global, 3/job | `scripts/vps-storage-hotpath-auto-recover.py:488,495` | Prevent runaway automation |

**Tuning guidance**:
- Increase cooldown to 30min if recovery attempts fail repeatedly
- Increase hourly/daily caps cautiously - investigate root cause instead
- Don't bypass caps in automation - they prevent pathological loops

---

## SSHFS Mount Options

| Option | Value | Location | Purpose |
|--------|-------|----------|---------|
| **reconnect** | Enabled | `docs/deployment/systemd/healtharchive-storagebox-sshfs.service:27` | Auto-reconnect on connection loss |
| **ServerAliveInterval** | 15s | systemd service | Send keepalive every 15 seconds |
| **ServerAliveCountMax** | 3 | systemd service | Disconnect after 3 missed keepalives (45s total) |
| **kernel_cache** | Enabled | systemd service | Performance optimization |

**Tuning guidance**:
- Lower `ServerAliveInterval` to 10s if mounts go stale frequently
- Don't raise `ServerAliveCountMax` - delays detection of stale connections
- `reconnect` should always be enabled

**Known issue**: Stale mounts still occur despite hardened options (root cause under investigation).

See: `docs/planning/2026-02-01-operational-resilience-improvements.md`, Phase 3

---

## Deploy Lock Protection

| Parameter | Value | Location | Purpose |
|-----------|-------|----------|---------|
| **Max age** | 2 hours | `scripts/vps-*-auto-recover.py` (various) | Stale lock detection |
| **Lock file** | `/tmp/healtharchive-backend-deploy.lock` | Deploy script + watchdogs | Prevent watchdog/deploy conflicts |
| **Lock mechanism** | `flock` | `scripts/vps-deploy.sh` | Atomic lock acquisition |

**Tuning guidance**:
- Increase max age if deploys routinely take >2 hours (investigate why)
- Don't decrease below 1 hour - normal deploys can take 30-45 minutes

---

## Infra Error Cooldown

| Parameter | Value | Location | Rationale |
|-----------|-------|----------|-----------|
| **Cooldown** | 10 minutes | `src/ha_backend/worker/main.py:77` | Prevent retry storms when infra is unhealthy |
| **Infra errors** | Errno 107, Errno 5, `OSError` during job launch | `src/ha_backend/infra_errors.py` | Infrastructure failures (not crawl failures) |

**Tuning guidance**:
- Increase to 20min if infrastructure is persistently unstable
- Decrease to 5min if false positives are common (careful!)

See: `docs/planning/implemented/2026-01-24-infra-error-and-storage-hotpath-hardening.md`

---

## Archive Tool (Crawler) Adaptive Thresholds

### Error Thresholds

| Parameter | Default | Annual Jobs Override | Location | Purpose |
|-----------|---------|---------------------|----------|---------|
| **Error threshold (timeout)** | 30 | 50 | Tool option | Timeout errors before triggering restart |
| **Error threshold (HTTP)** | 30 | 50 | Tool option | HTTP errors before triggering restart |
| **Backoff delay** | 5 min | 2 min | Tool option | Delay before resuming after restart |

**Tuning guidance**:
- Annual jobs use higher thresholds (50) to tolerate noisy sites
- Lower thresholds (20-25) for clean sites to detect issues faster
- Don't set timeout threshold <10 - too aggressive for flaky networks

---

### Container Restart Limits

| Parameter | Default | Annual Jobs | Location | Purpose |
|-----------|---------|-------------|----------|---------|
| **Max restarts** | 6 | 20 | Tool option | Container restart budget |
| **Stall timeout** | 30 min | 60 min | Tool option | Time without progress before restart |

**Tuning guidance**:
- Annual jobs get higher restart budget (20) due to long runtime
- Lower max restarts (3-5) for quick test jobs
- Increase stall timeout to 90min for very slow sites

See: `src/archive_tool/constants.py`, `scripts/vps-crawl-auto-recover.py:175-241`

---

## Summary Table: All Thresholds

| Category | Threshold | Value | Priority | Location |
|----------|-----------|-------|----------|----------|
| **Disk** | Worker headroom | 85% | P0 | `worker/main.py` |
| | Alert warning | 85% for 30m | P1 | alerting YAML |
| | Alert critical | 92% for 10m | P0 | alerting YAML |
| **Crawl** | Stall threshold | 90 min | P1 | `vps-crawl-auto-recover.py` |
| | Recovery cap | 3/job/day | P1 | `vps-crawl-auto-recover.py` |
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
- Operational resilience improvements: `docs/planning/2026-02-01-operational-resilience-improvements.md`
