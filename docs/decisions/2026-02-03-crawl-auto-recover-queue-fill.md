# Decision: Crawl auto-recover also fills underfilled annual crawls (2026-02-03)

Status: accepted

## Context

- Annual campaigns can have multiple crawl jobs running concurrently (worker + detached `systemd-run` units).
- The crawl auto-recover watchdog can correctly detect stalled running jobs and mark them `retryable`.
- A recovered job can remain `retryable` indefinitely when the worker is already busy running another crawl, leaving the annual campaign underfilled (reduced throughput, slow completion).
- We want a durable, automated way to restore the intended annual crawl concurrency without requiring manual operator intervention.

## Decision

- Extend `scripts/vps-crawl-auto-recover.py` to optionally **auto-start** queued/retryable *annual* jobs when the campaign is underfilled and no stalled jobs are detected.
- Keep the behavior **opt-in** via a systemd flag (`--ensure-min-running-jobs`) and protected by safety rails (deploy lock, disk threshold, per-job daily cap).
- Treat **legacy annual jobs** (created before `campaign_kind` / `campaign_year` existed) as eligible for queue fill when their name/output dir matches the canonical annual suffix `-YYYY0101` (e.g., `phac-20260101`), and backfill missing campaign metadata before auto-starting.

## Rationale

- The crawl auto-recover watchdog already has the correct operational guardrails (sentinel gating, deploy lock avoidance, Prometheus textfile metrics, and a timer cadence).
- Queue fill solves a real operational gap: the system can “recover” a stalled job but still fail to return to the desired running set.
- Starting a job via `systemd-run` is a proven pattern on the VPS for running multiple crawls concurrently without changing the worker architecture.

## Alternatives considered

- Modify the worker to run multiple jobs concurrently.
  - Rejected: changes core runtime model and increases complexity/risk during active annual crawls.
- Add a separate “queue fill” watchdog/service.
  - Rejected: duplicates guardrails/metrics and increases the ops surface area.
- Keep manual operator intervention as the only path.
  - Rejected: not sustainable; increases toil and increases time-to-recovery.

## Consequences

### Positive

- Annual crawls can return to the intended concurrency automatically after a stall recovery.
- Operators can validate behavior safely with dry-run drills.
- Adds explicit observability (`starts_total`) and state tracking (`starts` history) for auto-start actions.

### Negative / risks

- A misconfigured minimum running target could start additional crawls when disk/headroom is insufficient.
- If a source is fundamentally broken, auto-start could create repeated start attempts (mitigated by per-job daily caps).

## Verification / rollout

- Unit tests cover the auto-start decision logic, state recording, and metrics output.
- Systemd template enables queue fill by default on the VPS:
  - `docs/deployment/systemd/healtharchive-crawl-auto-recover.service`
- Operational drill:
  - `docs/operations/playbooks/crawl/crawl-auto-recover-drills.md` (queue fill / auto-start section)
- Rollback:
  - Remove `--ensure-min-running-jobs` from the systemd unit (or set it to `0`) and redeploy.

## References

- Related canonical docs:
  - `docs/operations/thresholds-and-tuning.md`
- Related playbooks/runbooks:
  - `docs/operations/playbooks/crawl/crawl-auto-recover-drills.md`
  - `docs/operations/playbooks/crawl/crawl-stalls.md`
- Related decisions:
  - `docs/decisions/2026-02-03-crawl-job-db-state-reconciliation.md`
