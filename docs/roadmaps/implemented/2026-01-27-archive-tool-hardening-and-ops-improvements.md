# Archive Tool Hardening and Ops Improvements (2026-01-27)

Implementation plan completed 2026-01-27. This work addressed 35+ improvements across 5 phases, hardening the archive_tool crawler and ops automation.

## Summary

This plan improved reliability, observability, and code quality across:
- `archive_tool` subpackage (crawler orchestration)
- VPS automation scripts (crawl recovery, metrics, storage hotpath)
- Prometheus alerting rules
- Backend job management

## Phases

### Phase 1: Pre-Crawl Critical Fixes
- Docker memory/CPU limits (configurable via environment)
- CIHR stall_timeout override in job registry
- Thread lock for state file operations
- OSError handling for stale mounts
- Pre-crawl output directory writability check
- Exception handling hardening in main.py

### Phase 2: Operational Automation Improvements
- Deploy lock check in crawl-auto-recover.py
- Prometheus textfile metrics for crawl recovery
- Lock file to prevent concurrent recovery runs
- fsync for durable state writes
- OSError handling in log file discovery

### Phase 3: Monitoring and Observability
- Per-job error type counters in crawl metrics
- Search error type breakdown in runtime metrics
- Alert for slow crawl rate (<5 ppm for 30m)
- Alert for high infra_error rate (>=3 in 10m)
- Alert for stale crawl metrics (>10m old)

### Phase 4: Architecture and Code Quality
- Extracted timeout magic numbers to constants.py
- Extracted CLI defaults to constants.py
- Moved late imports to module top in utils.py
- Consolidated stats regex pattern to constants.py
- Docker resource limits configurable via environment variables:
  - `HEALTHARCHIVE_DOCKER_MEMORY_LIMIT` (default: "4g")
  - `HEALTHARCHIVE_DOCKER_CPU_LIMIT` (default: "1.5")
  - `HEALTHARCHIVE_ZIMIT_DOCKER_IMAGE` (default: "ghcr.io/openzim/zimit")

### Phase 5: Documentation
- Comprehensive docstrings for monitor.py key functions
- Comprehensive docstrings for docker_runner.py key functions
- Comprehensive docstrings for state.py key functions
- Inline comments for main.py complex logic
- Inline comments for state.py persistence logic

## Key Files Changed

**archive_tool subpackage:**
- `src/archive_tool/constants.py` - Centralized timeout/CLI/resource constants
- `src/archive_tool/docker_runner.py` - Docker orchestration with resource limits
- `src/archive_tool/main.py` - Main loop with improved comments
- `src/archive_tool/monitor.py` - Log monitoring with docstrings
- `src/archive_tool/state.py` - State persistence with thread safety
- `src/archive_tool/cli.py` - CLI using named constants

**Backend:**
- `src/ha_backend/jobs.py` - Retry cap enforcement
- `src/ha_backend/job_registry.py` - CIHR stall timeout
- `src/ha_backend/runtime_metrics.py` - Error type breakdown
- `src/ha_backend/infra_errors.py` - Network errno handling
- `src/ha_backend/crawl_stats.py` - Error count metrics

**Automation scripts:**
- `scripts/vps-crawl-auto-recover.py` - Deploy lock, metrics, lock file
- `scripts/vps-crawl-metrics-textfile.py` - Error type metrics
- `scripts/vps-storage-hotpath-auto-recover.py` - fsync
- `scripts/vps-worker-auto-start.py` - fsync helper

**Alerting:**
- `ops/observability/alerting/healtharchive-alerts.yml` - New alerts

## Testing

All existing tests continue to pass. The improvements focus on runtime resilience rather than new testable features. Future work (deferred): add integration tests for main.py stage loop.

## Canonical Docs Updated

- `docs/reference/archive-tool.md` - Points to constants for resource limits
- Environment variables documented in implementation plan (this file)

## Related

- Previous hardening: `2026-01-24-infra-error-and-storage-hotpath-hardening.md`
- Previous crawl throughput: `2026-01-23-annual-crawl-throughput-and-artifacts.md`
