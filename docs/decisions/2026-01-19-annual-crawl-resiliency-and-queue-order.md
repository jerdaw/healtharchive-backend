# Decision: Annual crawl resiliency defaults and deterministic queue order (2026-01-19)

Status: accepted

## Context

- HealthArchive’s annual campaign crawls are **completeness-first** (full-fidelity backups), not “best effort within a page cap”.
- A 2026 annual crawl incident showed multiple failure modes that increased operator load and reduced progress:
  - “Noisy but progressing” sites (notably `canada.ca`) hit low adaptive timeout thresholds, causing repeated restarts and long backoff delays.
  - The single-worker queue pick order was ambiguous when multiple jobs were enqueued with identical `queued_at`, leading to non-deterministic job selection and starvation.
  - Invalid crawler CLI args (e.g., unsupported Zimit flags) caused immediate job failure and retry churn.
- Constraints:
  - Production currently uses a **single worker loop** and a small VPS.
  - We want safe-by-default automation and predictable operations.

## Decision

- For annual campaign jobs, we will use resiliency-oriented defaults that tolerate “noisy but progressing” crawls:
  - `max_container_restarts >= 20`
  - `error_threshold_timeout = 50`
  - `error_threshold_http = 50`
  - `backoff_delay_minutes = 2`
- `ha-backend schedule-annual` will stagger per-source `queued_at` timestamps so the single-worker pick order is deterministic (hc → phac → cihr).
- We will treat invalid CLI / config failures (e.g., “unrecognized arguments”) as `infra_error_config` so the worker does not churn retry budgets.
- Annual campaign policy remains: **no page/depth caps**. Use scope rules to bound crawls, not limits that risk incompleteness.

## Rationale

- Long annual crawls inevitably hit timeouts and transient network/protocol issues; restart thrash and long backoffs reduce throughput and increase operator intervention.
- Deterministic `queued_at` ordering makes operations predictable and prevents “queue tie” ambiguity in a single-worker environment.
- Classifying invalid CLI args as configuration errors surfaces the real problem quickly and avoids burning retries on a doomed job.
- Scope rules preserve the completeness-first mission without relying on early-termination caps.

## Alternatives considered

- Keep low default thresholds + long backoff — rejected: causes restart thrash and slows progress on long crawls.
- Add page/depth caps to guarantee completion — rejected: conflicts with completeness-first archival goals and can silently truncate captures.
- Add more workers/parallelism immediately — deferred: increases ops surface and resource needs; can be revisited once baseline stability is proven.

## Consequences

### Positive

- Fewer “thrash loops” and shorter recovery delays for long annual crawls.
- Predictable queue order reduces starvation and makes on-call behavior easier to reason about.
- Faster identification of invalid crawler argument/config mistakes.

### Negative / risks

- Higher thresholds may delay intervention for truly stuck crawls; mitigation is improved metrics (state file + restart counters) and stalled/progress-age alerts.
- More container restarts increase resource usage; mitigated by restart caps and monitoring.

## Verification / rollout

- Code defaults:
  - Annual job defaults include the new tool options (`src/ha_backend/job_registry.py`).
  - `schedule-annual` sets distinct `queued_at` per source (`src/ha_backend/cli.py`).
- Automation:
  - Auto-recover enforces annual minima when recovering jobs (`scripts/vps-crawl-auto-recover.py`).
- Observability:
  - Metrics exporter emits `.archive_state.json` health + restart counters (`scripts/vps-crawl-metrics-textfile.py`).
- Tests:
  - `tests/test_cli_schedule_annual.py`
  - `tests/test_ops_crawl_auto_recover_tool_options.py`
  - `tests/test_jobs_persistent.py`
  - `tests/test_ops_crawl_metrics_textfile_state.py`

Rollback:

- Revert the defaults and ordering logic in the above files; redeploy and restart the worker.

## References

- Related canonical docs:
  - `docs/operations/annual-campaign.md`
  - `docs/reference/archive-tool.md`
  - `docs/architecture.md`
- Related scripts:
  - `scripts/vps-crawl-status.sh`
  - `scripts/vps-crawl-auto-recover.py`
  - `scripts/vps-crawl-metrics-textfile.py`
