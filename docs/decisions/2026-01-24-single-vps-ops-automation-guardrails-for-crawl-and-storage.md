# Decision: Single-VPS ops automation guardrails for crawl + storage recovery (2026-01-24)

Status: accepted

## Context

- The single-VPS production deployment experienced a failure mode where Storage Box / FUSE-backed paths became stale and raised `OSError: [Errno 107] Transport endpoint is not connected`, which led to:
  - a retry storm (tight re-pick loop on a fast-failing `retryable` job), and
  - periods where crawl progress stopped until manual operator intervention.
- We need automation that improves resilience without increasing the chance of data loss or corrupting in-flight crawl outputs.
- Constraints:
  - single host, limited ops capacity, completeness-first crawl policy
  - automation must be safe-by-default and easy to disable instantly
  - observability must be strong enough to debug incidents without log-diving

## Decision

- We will implement **conservative, sentinel-gated watchdog automation** for crawl/storage recovery on the single VPS:
  - bounded and rate-limited,
  - idempotent where possible,
  - heavily biased toward “skip” instead of risky actions,
  - and emitting Prometheus textfile metrics for alerting and forensics.
- We will provide an **operator-friendly, detached job execution** path for re-running a specific crawl job without keeping SSH sessions open.
- We will keep documentation English-only (docs portal policy) and capture operational facts and follow-ups in incident notes + implemented roadmaps.

## Rationale

- Sentinel-gated timers and strong rate limits reduce the risk of automation doing harm during partial outages or deploys.
- Textfile metrics (node_exporter) make it possible to alert and to diagnose “stuck but not down” failure modes quickly.
- Detached job execution via transient systemd units prevents operator error and reduces the need for long-lived interactive sessions.

## Alternatives considered

- Add more aggressive auto-recovery (always unmount/remount immediately).
  - Rejected: too risky when a crawl container may be writing; potential for partial writes or frontier loss.
- Add new infrastructure (multi-host, queue workers, object storage).
  - Rejected for now: out of scope for immediate single-VPS stability improvements.
- Leave recovery fully manual.
  - Rejected: does not meet operational goals; increases toil and time-to-recovery.

## Consequences

### Positive

- Retry storms are mitigated and stale hot paths can be repaired earlier (including for queued/retryable jobs).
- Operators can re-run jobs without “keeping a terminal open”.
- Alerts can be based on stable metrics instead of ad-hoc log greps.

### Negative / risks

- Automation adds moving parts; misconfiguration could cause unnecessary churn or flapping if safety caps are removed.
- Some recovery steps remain intentionally manual when they intersect with in-flight writes; this trades faster auto-recovery for safety.

## Verification / rollout

- Verify watchdog scripts in dry-run modes and via unit tests.
- On the VPS, enable the timers only after verifying sentinel files exist and `vps-crawl-status.sh` shows healthy metrics.
- Rollback: remove sentinel files and disable timers; revert to manual playbooks.

## References

- Related canonical docs:
  - `docs/operations/monitoring-and-alerting.md`
  - `docs/deployment/systemd/README.md`
- Related incident notes:
  - `docs/operations/incidents/2026-01-24-infra-error-107-hotpath-thrash-and-worker-stop.md`
- Related implemented roadmap:
  - `docs/roadmaps/implemented/2026-01-24-infra-error-and-storage-hotpath-hardening.md`
