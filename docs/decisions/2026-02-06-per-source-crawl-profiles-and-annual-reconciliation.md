# Decision: Per-source crawl profiles + annual reconciliation (2026-02-06)

Status: accepted

## Context

- We run long-lived annual crawls for multiple sources with meaningfully different behavior (crawl rate, blocking/noise, restart tolerance).
- A single global set of crawl tool options forces bad tradeoffs:
  - too strict for “noisy” sources (unnecessary restarts),
  - too lax for “clean” sources (slower detection of real problems),
  - and makes tuning changes hard to apply safely to already-created annual jobs.
- Constraints:
  - Do not compromise crawl completeness or accuracy.
  - Prefer changes that are safe under repeated restarts and partial failures.
  - Keep operator workflows simple (one command to reconcile; visibility via metrics/alerts).

## Decision

- We will maintain **per-source crawl tuning profiles** in code and use them when creating annual jobs.
- We will provide an operator-safe reconciliation mechanism to update tool options on already-created annual jobs without editing the DB manually.
- We will treat crawl churn (restarts/new crawl phases) as an operational signal and alert on sustained churn.

## Rationale

Per-source profiles keep the default configuration aligned with reality: different sites have different noise and block patterns. A reconciliation command gives us a controlled way to apply improved defaults to existing annual jobs (so retries/restarts adopt the new settings) without sacrificing reproducibility or integrity.

## Alternatives considered

- Keep one global profile — rejected: forces poor compromises and increases churn.
- Tune ad-hoc in production DB for each annual job — rejected: error-prone, hard to audit, and drifts from code defaults.
- Recreate annual jobs to apply new defaults — rejected: operationally risky and makes continuity/completeness harder to reason about.

## Consequences

### Positive

- Tuning is explicit, per-source, and versioned with code.
- Operators have a single reconciliation workflow for already-created annual jobs.
- Monitoring can distinguish “slow crawl” vs “churn” problems per source.

### Negative / risks

- Profiles can become stale; requires periodic review based on observed behavior.
- Reconciliation updates don’t change a currently running container mid-flight; improvements apply on the next restart/retry cycle.

## Verification / rollout

- Verify the reconciliation command reports intended deltas before applying:
  - `ha-backend reconcile-annual-tool-options --year <YEAR>`
  - `ha-backend reconcile-annual-tool-options --year <YEAR> --apply`
- Verify metrics reflect:
  - per-source crawl rate alerts (`HealthArchiveCrawlRateSlow*`)
  - churn alert (`HealthArchiveCrawlNewPhaseChurn`)
- Rollback: revert profile changes in `ha_backend/job_registry.py` and re-run reconciliation (apply) to restore prior options for annual jobs.

## References

- Related canonical docs:
  - `docs/operations/thresholds-and-tuning.md`
  - `docs/operations/monitoring-and-alerting.md`
- Related playbooks/runbooks:
  - `docs/tutorials/debug-crawl.md`
  - `docs/operations/playbooks/annual-campaign.md`
