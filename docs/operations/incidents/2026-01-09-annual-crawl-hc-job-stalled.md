# Incident: Annual crawl — HC job stalled (2026-01-09)

Status: draft (ongoing)

## Metadata

- Date (UTC): 2026-01-09
- Severity: sev1
- Environment: production
- Primary area: crawl
- Owner: (unassigned)
- Start (UTC): 2026-01-09T07:34:37Z (last observed crawl progress)
- End (UTC): ongoing

---

## Summary

The annual crawl job for `hc` (job 6) entered a stalled state: `crawlStatus` stopped advancing and the crawl metrics exporter flagged it as stalled. The stall correlated with repeated `Navigation timeout` warnings on canada.ca pages.

Manual recovery (stop worker + recover stale jobs) was intentionally deferred while `cihr` (job 8) was actively crawling, to avoid turning an in-progress crawl into a `failed` job at max retries.

## Impact

- User-facing impact: annual campaign remained `Ready for search: NO`.
- Internal impact: operator attention required; `hc` crawl not progressing.
- Data impact:
  - Data loss: unknown (WARCs exist in temp dirs, but crawl completeness is unknown until completion).
  - Data integrity risk: low/unknown (no specific corruption signals observed; primarily a progress/stall problem).
  - Recovery completeness: not recovered at time of write-up.

## Detection

- `./scripts/vps-crawl-status.sh --year 2026 --job-id 6`:
  - `healtharchive_crawl_running_job_stalled{job_id="6",source="hc"} 1`
  - `last_progress_age_seconds` climbed into multi-hour range.
  - `crawlStatus tail` stopped advancing.
  - `recent timeouts` showed repeated `Navigation timeout of 90000 ms exceeded`.

## Decision log

- 2026-01-09 — Deferred the “stop worker + recover stale jobs” procedure while job 8 (`cihr`) was actively crawling to reduce the risk of interrupting it at max retries.

## Timeline (UTC)

- 2026-01-09T06:05:14Z — Job 6 started (latest observed start time in status snapshot).
- 2026-01-09T07:34:37Z — Last observed `crawlStatus` progress for job 6 (`crawled=437`, `total=3209`, `pending=1`).
- 2026-01-09T12:57:17Z — Status snapshot shows multi-hour no-progress and `stalled=1`.
- 2026-01-09T13:33:23Z — Status snapshot still shows `stalled=1`.

## Root cause

Unknown at time of write-up. Strong signals point to crawl progress blocked by repeated page navigation timeouts and/or a crawler worker getting stuck on a specific URL.

## Contributing factors

- Many canada.ca pages timed out (90s navigation timeouts), increasing the chance of long “pending page” windows.
- `hc` and `cihr` were both running; the safest recovery approach (stopping the worker) would interrupt both.

## Resolution / Recovery

Not performed yet (deferred while `cihr` continues).

Planned recovery steps once it is safe to interrupt crawling:

- Follow `docs/operations/playbooks/crawl-stalls.md`:
  - `sudo systemctl stop healtharchive-worker.service`
  - `set -a; source /etc/healtharchive/backend.env; set +a`
  - `/opt/healtharchive-backend/.venv/bin/ha-backend recover-stale-jobs --older-than-minutes 60 --source hc --limit 5 --apply`
  - `sudo systemctl start healtharchive-worker.service`
  - Re-check `./scripts/vps-crawl-status.sh --year 2026 --job-id 6` for advancing `crawlStatus`.

## Post-incident verification

TBD (once recovered).

## Open questions (still unknown)

- What exact URL/work unit is the crawler stuck on (if any), and does it repeat across retries?
- Are timeouts driven by site performance, network issues, headless browser instability, or scope rules?
- Would changing timeouts/adaptive restart thresholds reduce repeat stalls without harming completeness?

## Action items (TODOs)

- [ ] After `cihr` completes (or during a maintenance window), perform the planned recovery steps and update this note with outcomes. (priority=high)
- [ ] If the stall repeats, capture the specific repeated URL(s) and assess whether scope/timeout tuning is warranted. (priority=medium)
- [ ] Consider tightening/clarifying automation boundaries: per-job recovery without stopping unrelated active crawls (if feasible). (priority=low)

## Automation opportunities

- Improve “stalled crawl” detection to include the most recent pending URL and age as part of operator output (snapshot script) and/or alert annotations.
- Investigate whether recovery can be scoped to a single crawl process/container without stopping the entire worker loop (risk: false positives and partial state).

## References / Artifacts

- Operator snapshot script: `scripts/vps-crawl-status.sh`
- Latest combined log (as of 2026-01-09 12:57Z snapshot): `/srv/healtharchive/jobs/hc/20260101T000502Z__hc-20260101/archive_new_crawl_phase_-_attempt_1_20260109_060517.combined.log`
- Playbook: `../playbooks/crawl-stalls.md`
- Playbook: `../playbooks/incident-response.md`
- Related: `2026-01-09-annual-crawl-phac-output-dir-permission-denied.md`
