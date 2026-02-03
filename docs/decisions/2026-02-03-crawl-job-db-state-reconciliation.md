# Decision: Crawl job DB state reconciliation (2026-02-03)

Status: accepted

## Context

HealthArchive crawl jobs have two overlapping “sources of truth” during execution:

- **DB state** (`archive_jobs.status`) used by the worker, monitoring, and operator tooling.
- **Runtime state** (active processes / held job locks) that proves a crawl is currently running.

During the 2026 annual campaign we observed cases where a crawl was **actively running** but the
DB reported the job as **not** running (e.g., `retryable`). This drift can happen after manual
operator interventions (e.g., marking a stuck job retryable), restarts, or older runners that did
not reliably update DB status.

When DB state drifts from runtime reality:

- monitoring and stall detection becomes misleading,
- automation can make incorrect decisions (e.g., “recover” a job that is already running),
- operators lose the ability to reason about campaign progress from `ha-backend` job listings.

## Decision

- When crawl auto-recovery runs in **apply mode** (and automation is enabled), we will
  **reconcile DB job status back to `running`** when a job is clearly running according to:
  - a **held per-job lock** (strong signal), or
  - an **active crawl process** that is attributable to the job’s `output_dir` (fallback for older runners).
- Reconciliation will be:
  - **sentinel-gated** (only when crawl auto-recover automation is enabled),
  - **bounded and conservative** (strong signals only),
  - **idempotent** (safe to re-run),
  - **dry-run visible** (prints what would change when not applying).

## Rationale

This is the smallest durable fix that keeps DB state usable as the operator interface without
introducing new manual steps.

The crawl auto-recover watchdog is already:

- periodic (timer-based),
- sentinel-gated,
- and coupled to crawl-health monitoring.

Using it as the place to repair DB drift keeps the system consistent and avoids a second “operator
must remember to run X” workflow.

## Alternatives considered

- **Do nothing; require manual DB repair** — rejected because it is fragile, slow, and leads to
  repeated operator confusion during long-running campaigns.
- **Create a separate “reconcile-job-state” command** — rejected because it adds another tool that
  operators must remember to run, and it would still need the same safety rules/signals.
- **Treat runtime state as truth for monitoring only (no DB writes)** — rejected because drift
  persists and downstream tooling continues to show incorrect job state.

## Consequences

### Positive

- `ha-backend list-jobs --status running` reflects reality during campaigns.
- Stall detection and recovery logic operates on accurate `status=running` jobs.
- Fewer unsafe/incorrect recoveries when jobs were manually marked `retryable` but still running.

### Negative / risks

- Risk of incorrectly marking a job `running` if the signal is misattributed.
  - Mitigation: prefer held job locks; fallback process matching is conservative and requires a
    job to look “in-flight” (`started_at` set, `finished_at` unset) and to match output-dir related
    processes.

## Verification / rollout

- Verify in dry-run/drill mode using `scripts/vps-crawl-auto-recover.py` with simulated jobs.
- In production apply mode, watchdog logs will show reconciliation actions, e.g.:
  - “synced … job(s) to status=running based on held job locks”
  - “synced … job(s) to status=running based on active crawl processes”
- Rollback: disable crawl auto-recover automation by removing the sentinel file
  `/etc/healtharchive/crawl-auto-recover-enabled` (and/or revert the change).

## References

- Implementation: `scripts/vps-crawl-auto-recover.py` (DB drift reconciliation block)
- Operator docs: `docs/operations/playbooks/crawl/crawl-stalls.md`, `docs/operations/playbooks/crawl/crawl-auto-recover-drills.md`
- Related thresholds: `docs/operations/thresholds-and-tuning.md`
