# Automation-first crawl alerting and dashboarding (Implemented 2026-02-23)

**Status:** Implemented | **Scope:** Shift crawl monitoring from noisy throughput/churn notifications to automation-first, actionability-focused alerts with Grafana trend visibility.

## Outcomes

- Removed non-actionable crawl throughput/churn alerts (`HealthArchiveCrawlRateSlow*`, `HealthArchiveCrawlNewPhaseChurn`) from Prometheus rules.
- Split job-level unreadable/writability crawl alerts to exclude `Errno 107` stale-mount cases, routing those to storage watchdog/root-cause alerts instead.
- Made `HealthArchiveWorkerDownWhileJobsPending` automation-aware:
  - longer wait window,
  - deploy-lock suppression,
  - fallback behavior when worker auto-start metrics are unavailable/disabled.
- Added watchdog freshness alerts for:
  - `healtharchive-worker-auto-start.timer`
  - `healtharchive-crawl-auto-recover.timer`
- Added worker auto-start watchdog metrics/counters for start attempts/success/fail outcomes and last-attempt timestamps.
- Improved Alertmanager grouping/inhibition in the installer-generated config:
  - group by `alertname`, `source`, `job_id`
  - inhibit derivative storage/crawl alerts when storage root-cause alerts are firing
- Added Grafana `HealthArchive - Pipeline Health` panels for:
  - crawl rate (raw + 30m avg),
  - progress age,
  - restart counts,
  - crawl phase churn counts,
  - watchdog activity/freshness/enablement.
- Added Grafana access quickstart docs (SSH port-forward preferred; Tailscale Serve optional) and linked them from monitoring docs.

## Canonical Docs Updated

- `docs/operations/monitoring-and-alerting.md`
- `docs/operations/monitoring-and-ci-checklist.md`
- `docs/operations/observability-and-private-stats.md`
- `docs/operations/thresholds-and-tuning.md`
- `docs/operations/healtharchive-ops-roadmap.md`
- `docs/deployment/systemd/README.md`
- `docs/planning/roadmap.md`

## Decisions Created

- None (existing alert-fatigue and observability decisions remained sufficient for this iteration)

## Historical Context

Detailed implementation, validation, and VPS rollout verification history is preserved in git commits and operator terminal logs; this file records durable outcomes and where they are documented.
