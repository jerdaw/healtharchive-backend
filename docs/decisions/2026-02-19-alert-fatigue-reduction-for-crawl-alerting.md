# Decision: Reduce Crawl Alert Fatigue with Severity-Aware Routing and Threshold Tuning (2026-02-19)

Status: accepted

## Context

- Annual crawls are long-running and naturally noisy; some warning-level alerts were generating frequent notification churn without requiring immediate operator action.
- Existing routing sent both `firing` and `resolved` notifications for all severities, increasing non-critical notification volume.
- Recent observed pattern (notably PHAC slow-rate and restart warnings) showed repeated state flips that were informative but not always actionable.
- Constraints:
  - Preserve rapid operator visibility for outages and genuinely urgent failures.
  - Keep warning signals useful for trend detection without causing alert fatigue.
  - Maintain config-as-code behavior via repo-managed Prometheus/Alertmanager assets.

## Decision

- We will use **severity-aware Alertmanager routing**:
  - `critical`: keep resolved notifications (`send_resolved: true`) and shorter repeat cadence.
  - `warning`/`info`: suppress resolved notifications (`send_resolved: false`) and use a longer repeat cadence.
- We will tune crawl warning thresholds toward **near-actionability**:
  - `HealthArchiveCrawlContainerRestartsHigh` now triggers near restart-budget exhaustion (source-specific).
  - `HealthArchiveCrawlRateSlowPHAC` now requires a lower threshold (`<1.0 ppm`) sustained longer (`90m`) with healthy output-dir/log probes.

## Rationale

Severity-aware routing preserves urgency for high-impact incidents while reducing notification churn for warning-level trends. Source-aware threshold tuning aligns alerts with real campaign behavior and restart budgets, improving signal quality for a solo-operator workflow.

## Alternatives considered

- Keep existing routing/thresholds and tolerate noise:
  - Rejected: creates sustained alert fatigue and weakens operator response quality over time.
- Disable warning-level crawl alerts entirely:
  - Rejected: loses early operational signals and trend visibility.
- Move all warning-level alerts to dashboards only:
  - Rejected: removes important asynchronous operator visibility during long runs.

## Consequences

### Positive

- Lower non-critical notification volume.
- Better alignment between warning notifications and likely operator action.
- Critical outage behavior remains explicit and high-urgency.

### Negative / risks

- Some warning-level transient recoveries are no longer explicitly notified via `resolved` events.
- CIHR and other source thresholds may still require iterative calibration from live telemetry.

## Verification / rollout

- Apply alerting config on VPS:
  - `./scripts/vps-install-observability-alerting.sh`
  - `sudo ./scripts/vps-install-observability-alerting.sh --apply`
- Verify routing and rules:
  - `curl -s http://127.0.0.1:9093/-/ready`
  - `sudo grep -nE 'healtharchive-webhook-(critical|noncritical)|send_resolved|repeat_interval' /etc/prometheus/alertmanager.yml`
  - `curl -s http://127.0.0.1:9090/api/v1/rules | rg 'HealthArchiveCrawlContainerRestartsHigh|HealthArchiveCrawlRateSlowPHAC'`
- Follow-through:
  - Review 7-day notification volume and tune CIHR slow-rate threshold if needed.

## References

- `docs/operations/monitoring-and-alerting.md`
- `docs/operations/playbooks/observability/observability-guide.md`
- `docs/operations/healtharchive-ops-roadmap.md`
- `ops/observability/alerting/healtharchive-alerts.yml`
- `scripts/vps-install-observability-alerting.sh`
