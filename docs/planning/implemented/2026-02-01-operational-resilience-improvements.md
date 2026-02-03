# Operational resilience improvements (Implemented 2026-02-01)

**Status:** Implemented | **Scope:** Single-VPS operational hardening for the 2026 annual campaign: reduce disk-pressure wedges, improve crawl recovery safety, and make watchdog behavior observable and drillable.

## Outcomes

- Disk pressure safeguards:
  - Worker pre-crawl headroom gate (prevents starting new jobs when disk is too full).
  - Safe cleanup automation for indexed jobs (`temp-nonwarc`) plus a disk-threshold safety net.
- Crawl recovery hardening:
  - Crawl auto-recover watchdog uses a 60m stall threshold with a guard window to avoid disrupting healthy crawls.
  - Safe dry-run drills for crawl auto-recover (simulate stalled jobs; verify planned actions end-to-end).
  - Automated “soft recover” option: mark a stalled job retryable without stopping the worker when another job is progressing.
- Storage hot-path hardening (Errno 107):
  - Storage hot-path auto-recover watchdog detects stale/unreadable mountpoints and repairs them with bounded, rate-limited actions.
  - Tiering helpers run with stale-mount repair flags to reduce manual intervention during campaigns.
- Operator UX:
  - Clearer operator workflows for recovering stalled jobs and validating watchdog behavior via drills and metrics.
- Follow-up captured:
  - Ongoing `df` vs `du` disk-usage discrepancy investigation kept as an active plan for a future maintenance window.

## Canonical Docs Updated

- `docs/operations/thresholds-and-tuning.md`
- `docs/deployment/systemd/README.md`
- `docs/operations/playbooks/crawl/crawl-stalls.md`
- `docs/operations/playbooks/crawl/crawl-auto-recover-drills.md`
- `docs/operations/playbooks/crawl/cleanup-automation.md`
- `docs/operations/playbooks/storage/storagebox-sshfs-stale-mount-recovery.md`

## Decisions Created (if any)

- `docs/decisions/2026-02-03-crawl-job-db-state-reconciliation.md`

## Historical Context

This plan was executed during the 2026 annual campaign while prioritizing safe-by-default automation (sentinel-gated, capped, observable). Detailed implementation history is preserved in git.
