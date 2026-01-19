# Annual crawl resiliency hardening (implemented) — 2026-01-19

This is a historical implementation note capturing a production incident follow-up for the 2026 annual campaign.

## Problem statement

During the 2026 annual campaign, we observed:

- **Retry churn / starvation**: jobs could remain `retryable` and not make forward progress due to queue tie-breaking and long backoff delays.
- **Restart thrash** on long annual crawls (notably `canada.ca`): low timeout thresholds triggered repeated adaptive restarts + long backoffs, reducing throughput.
- **Config error failures** (e.g., invalid Zimit args) that consumed time and retries without a clear “this is a config bug” signal.
- **Limited observability** into restart/worker-throttle state without manually inspecting `.archive_state.json`.

HealthArchive policy is **completeness-first** archival, so “page caps” and other early-stop controls are not acceptable for annual crawls.

## Goals

- Make annual crawl defaults more resilient to long-tail timeouts and transient network/protocol noise.
- Make the annual job pick order deterministic in a single-worker environment.
- Prevent retry churn on invalid CLI/config failures by classifying them as configuration errors.
- Improve monitoring to detect restart thrash and state-file health issues.

## Work implemented

### Safer annual defaults

- Annual job tool options now default to:
  - `max_container_restarts = 20`
  - `error_threshold_timeout = 50`
  - `error_threshold_http = 50`
  - `backoff_delay_minutes = 2`
- Files:
  - `src/ha_backend/job_registry.py`

### Deterministic annual queue order

- `ha-backend schedule-annual` staggers `queued_at` timestamps (hc → phac → cihr).
- Files:
  - `src/ha_backend/cli.py`
  - Doc note: `docs/operations/annual-campaign.md`

### Configuration error classification

- Persist combined log path when available and classify “invalid CLI args” failures as `infra_error_config`.
- Files:
  - `src/ha_backend/jobs.py`

### Auto-recover guardrails

- Auto-recover now enforces annual minimums for restart budget and thresholds when recovering jobs.
- Files:
  - `scripts/vps-crawl-auto-recover.py`

### Monitoring improvements

- `vps-crawl-metrics-textfile.py` now exports `.archive_state.json` health + counters:
  - state file probe OK/errno
  - parse OK
  - mtime age seconds
  - current workers, reductions, container restarts, VPN rotations, temp dir count
- Files:
  - `scripts/vps-crawl-metrics-textfile.py`
  - `docs/operations/playbooks/storagebox-sshfs-stale-mount-recovery.md`
  - `docs/reference/archive-tool.md`

### Documentation corrections

- Corrected misleading mention of unsupported Zimit page-cap args and clarified that annual crawls should not use caps.
- Files:
  - `src/archive_tool/docs/documentation.md`
  - `docs/tutorials/debug-crawl.md`
  - `docs/architecture.md`

### Tests

- Added/updated tests for ordering, auto-recover tool option enforcement, config error classification, and state-file metrics.
- Files:
  - `tests/test_cli_schedule_annual.py`
  - `tests/test_ops_crawl_auto_recover_tool_options.py`
  - `tests/test_jobs_persistent.py`
  - `tests/test_ops_crawl_metrics_textfile_state.py`

## Definition of done (checked)

- [x] Annual defaults updated in code.
- [x] Annual queue ordering made deterministic.
- [x] Invalid CLI/config failures classified as `infra_error_config`.
- [x] Auto-recover enforces annual minimum guardrails.
- [x] Metrics exporter includes state-file health + restart counters.
- [x] Docs updated to reflect reality and policy (no annual caps).
- [x] Tests updated/added and pass in CI gate (`make ci`).

## Follow-ups (not part of this change)

- Confirm production deployment and validate dashboards/alerts incorporate the new state-file metrics.
- Ensure indexing runs after crawl completion and that WARC discovery/counting is consistent between CLI/status output and the indexing pipeline.

## References

- Decision record:
  - `docs/decisions/2026-01-19-annual-crawl-resiliency-and-queue-order.md`
