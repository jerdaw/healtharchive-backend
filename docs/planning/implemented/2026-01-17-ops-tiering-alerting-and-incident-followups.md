# Tiering alerting + incident follow-ups (Implemented 2026-01-18)

**Status:** Implemented | **Scope:** Close high-leverage operational gaps by enabling tiering health metrics/alerting and completing follow-ups from early 2026 incidents.

## Outcomes

- Tiering health observability:
  - Enabled tiering health metrics written to node_exporter textfile collector.
  - Added alerting for tiering failures and for stale metrics (no updates) to avoid silent breakage.
- Incident follow-ups closed:
  - Documented the operational failure modes and the recovery procedures discovered during incidents.
  - Captured deferred follow-ups as backlog items where implementation was not yet appropriate.
- Replay resilience posture:
  - Documented the “canary replay job” idea as a future option to distinguish replay failures from storage-tiering failures.

## Canonical Docs Updated

- `docs/operations/monitoring-and-alerting.md`
- `docs/operations/incidents/2026-01-16-replay-smoke-503-and-warctieringfailed.md`
- `docs/operations/incidents/2026-01-09-annual-crawl-hc-job-stalled.md`
- `docs/operations/healtharchive-ops-roadmap.md`
- `docs/deployment/systemd/README.md`

## Decisions Created (if any)

- None (follow-ups were captured in canonical incident notes and roadmaps).

## Historical Context

This plan was executed to move existing monitoring/automation from “implemented but unwired” to “enabled and actionable” on the single-VPS deployment. Detailed implementation history is preserved in git.
