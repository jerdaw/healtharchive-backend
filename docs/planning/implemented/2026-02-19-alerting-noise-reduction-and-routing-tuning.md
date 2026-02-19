# Alerting noise reduction and routing tuning (Implemented 2026-02-19)

**Status:** Implemented | **Scope:** Reduce non-actionable crawl-alert notification churn while preserving urgent outage visibility.

## Outcomes

- Added severity-aware Alertmanager routing in the installer:
  - `critical` alerts keep resolved notifications and shorter repeats.
  - `warning`/`info` alerts suppress resolved notifications and use longer repeats.
- Tuned crawl warning alerts toward operator actionability:
  - `HealthArchiveCrawlContainerRestartsHigh` now fires near source-specific restart-budget exhaustion.
  - `HealthArchiveCrawlRateSlowPHAC` now requires `<1.0 ppm` for `90m` and healthy output-dir/log probes.
- Improved installer dry-run UX:
  - Non-root dry-runs no longer hard-fail when Alertmanager unit discovery is unavailable.
- Added/extended tests for:
  - alert-rule semantics,
  - alerting-installer routing/fallback script invariants.

## Canonical Docs Updated

- `docs/operations/monitoring-and-alerting.md`
- `docs/operations/playbooks/observability/observability-guide.md`
- `docs/operations/healtharchive-ops-roadmap.md`
- `docs/planning/roadmap.md`

## Decisions Created

- `docs/decisions/2026-02-19-alert-fatigue-reduction-for-crawl-alerting.md`

## Historical Context

Detailed implementation and validation history is preserved in git commits and VPS operator logs; this file keeps only durable outcomes.
