# Infra-error retry storms + storage hot-path resilience (Implemented 2026-01-24)

**Status:** Implemented | **Scope:** Prevent single-VPS retry storms and “everything stopped” states caused by infrastructure failures (notably Errno 107 stale `sshfs`/FUSE mountpoints) during the 2026 annual campaign.

## Outcomes

- Worker resilience:
  - Added an infra-error cooldown so jobs that end in `crawler_status=infra_error` are not immediately re-selected in a tight loop.
  - Improved logging/operator signal around infra errors vs crawl failures.
- Storage hot-path auto-recover hardening:
  - Detect stale/unreadable mountpoints (Errno 107) not just for running jobs, but also for “next jobs” (queued/retryable) to prevent retry storms.
  - Conservative recovery sequence with caps/cooldowns and deploy-lock avoidance.
  - Tiering helpers support stale-mount repair flags for safer recovery.
- Worker auto-start safety:
  - Added a conservative watchdog to start the worker only when it should be running (jobs pending + storage OK), sentinel-gated.
- Observability:
  - Exported watchdog metrics to node_exporter textfile collector (and documented enablement).

## Canonical Docs Updated

- `docs/deployment/systemd/README.md`
- `docs/operations/playbooks/storage/storagebox-sshfs-stale-mount-recovery.md`
- `docs/operations/playbooks/storage/storagebox-sshfs-stale-mount-drills.md`
- `docs/operations/playbooks/validation/automation-maintenance.md`
- `docs/operations/thresholds-and-tuning.md`

## Decisions Created (if any)

- `docs/decisions/2026-01-24-single-vps-ops-automation-guardrails-for-crawl-and-storage.md`

## Historical Context

This plan was triggered by a 2026-01-24 production incident involving Errno 107 hot-path failures. Detailed implementation history and verification steps are preserved in git.
