# Incident: Annual crawl — PHAC canada.ca HTTP/2 thrash (2026-03-23)

Status: draft (ongoing)

## Metadata

- Date (UTC): 2026-03-23
- Severity: sev1
- Environment: production
- Primary area: crawl
- Owner: (unassigned)
- Start (UTC): 2026-03-23T10:39:22Z
- End (UTC): ongoing

---

## Summary

The annual PHAC crawl (`job_id=7`) entered a sustained failure loop on `www.canada.ca` with repeated document-level `net::ERR_HTTP2_PROTOCOL_ERROR` errors. The observed failures were broader than the previously excluded `public-health-notices` subtree and covered many in-scope PHAC URLs under both English and French paths.

We first confirmed that the deployed backend was missing the repo-side PHAC scope reconciliation fix, then deployed that fix and reconciled the live PHAC job config. A controlled PHAC-only restart picked up the corrected scope exclusion, but the crawl still flatlined, so a broader source-profile compatibility fix (`--extraChromeArgs --disable-http2`) was prepared, deployed, and verified in the live PHAC process.

That compatibility change appears to have removed the visible HTTP/2 thrash, but it still did not restore measurable crawl progress. PHAC remained in a misleading `status=running` state with `.archive_state.json` updating and repeated resume-stage attempts, yet no parseable `crawlStatus`, no new WARC mtimes, and no non-zero crawl rate. The job was parked as `retryable` pending repo-side investigation.

## Impact

- User-facing impact: annual campaign remained `Ready for search: NO`.
- Internal impact: repeated operator intervention was required to inspect logs, reconcile config drift, and restart PHAC without interrupting CIHR.
- Data impact:
  - Data loss: unknown.
  - Data integrity risk: low/unknown (the issue is crawl completeness/progress, not known corruption).
  - Recovery completeness: partial at time of write-up.
- Duration: ongoing.

## Detection

- `rg` on the PHAC combined log showed repeated `Page Load Failed: retry limit reached` entries with `net::ERR_HTTP2_PROTOCOL_ERROR`.
- `./scripts/vps-crawl-status.sh --year 2026 --job-id 7 --recent-lines 5000` showed:
  - `crawled` flat at `267`
  - `crawl_rate_ppm=0`
  - `last_progress_age_seconds` continuing to climb
  - no useful forward progress after restart
- `show-job --id 7` and process inspection confirmed the live runner was initially using stale PHAC passthrough args and later picked up the corrected scope exclusion after deploy/restart.

## Decision log

- 2026-03-23T11:3x:00Z — Deferred PHAC recovery until the repo-side reconciliation fix was committed, pushed, and deployed. Recovery against undeployed code was explicitly rejected.
- 2026-03-23T11:4x:00Z — Chose a PHAC-only stop/recover/restart path to avoid interrupting the healthy CIHR crawl.
- 2026-03-23T12:00:49Z — After confirming the `public-health-notices` exclusion was active in the live PHAC process, concluded the remaining failure pattern was broader than that subtree and required a source-profile compatibility change rather than repeated blind restarts.

## Timeline (UTC)

- 2026-03-23T10:39:22Z — Earliest operator-captured PHAC log entry in the current incident window shows `net::ERR_HTTP2_PROTOCOL_ERROR`.
- 2026-03-23T11:37:55Z — Backend deploy completed on the VPS with the annual scope reconciliation fix active.
- 2026-03-23T11:39:xxZ — `show-job --id 7` confirmed PHAC config now included the canonical `public-health-notices` exclusion.
- 2026-03-23T11:49:48Z — Existing PHAC runner stopped cleanly via its transient systemd unit.
- 2026-03-23T11:49:53Z — `recover-stale-jobs --apply --source phac --limit 1` marked job 7 `retryable`.
- 2026-03-23T11:50:02Z — PHAC job 7 relaunched.
- 2026-03-23T12:00:49Z — Status snapshot showed PHAC still flatlined at `crawled=267`, `crawl_rate_ppm=0`, and `container_restarts_done=30`.
- 2026-03-23T12:37:46Z — Backend deploy completed on the VPS with the Browsertrix compatibility change active (`b863ec0`).
- 2026-03-23T12:43:34Z — PHAC was relaunched via a new transient systemd unit after `recover-stale-jobs` marked job 7 `retryable`.
- 2026-03-23T12:43:35Z — New live PHAC process started with `--extraChromeArgs --disable-http2` confirmed in the command line.
- 2026-03-23T12:55:29Z — Status snapshot showed no recent HTTP/2/timeouts, but also no parseable `crawlStatus` and no measurable progress (`progress_known=0`, `crawl_rate_ppm=-1`).
- 2026-03-23T13:12:30Z — Follow-up snapshot still showed no progress and no new WARC mtimes while the state file kept updating and the latest log had advanced to `archive_resume_crawl_-_attempt_8_...`.
- 2026-03-23T13:18:16Z — PHAC job 7 was parked as `retryable` again pending repo-side investigation.

## Root cause

- Immediate trigger: repeated document-level HTTP/2 protocol failures on canada.ca pages prevented PHAC from making useful crawl progress.
- Underlying cause(s): current Browsertrix/chromium transport behavior appears incompatible with some canada.ca annual PHAC pages under the existing source profile; the single `public-health-notices` exclusion was not sufficient to restore progress.
- Follow-up hypothesis after the `--disable-http2` deploy: the crawler may now be falling into repeated resume-stage churn without emitting parseable `crawlStatus` or producing new WARC output, leaving ops metrics with only a weak "running but unknown" signal.

## Contributing factors

- The first PHAC recovery attempt happened after discovering that the VPS checkout did not yet contain the repo-side scope reconciliation change.
- PHAC and HC share the canada.ca host, which makes broad exclusions risky for completeness.
- PHAC had already exhausted its adaptive container restart budget (`30`), so the live run was no longer self-healing.

## Resolution / Recovery

Performed so far:

- Verified the missing PHAC scope reconciliation code on the VPS checkout.
- Committed, pushed, and deployed the PHAC scope reconciliation fix.
- Reconciled the live PHAC annual job config in place.
- Verified `show-job --id 7` included the canonical `public-health-notices` exclusion.
- Performed a PHAC-only stop/recover/restart without interrupting CIHR.
- Confirmed the restarted PHAC process picked up the corrected `scopeExcludeRx`.

Completed after the initial draft:

- Deployed the repo-side HC/PHAC source-profile compatibility change adding Browsertrix `--extraChromeArgs --disable-http2`.
- Reconciled the live HC/PHAC annual job configs in production.
- Relaunched PHAC and verified the live process included `--extraChromeArgs --disable-http2`.
- Observed that the HTTP/2 error storm stopped, but the crawler still failed to produce measurable progress.
- Parked PHAC as `retryable` again rather than allowing repeated blind restarts.

## Post-incident verification

Completed so far:

- Public surface verification passed after the backend deploy.
- PHAC live process verification showed the reconciled `scopeExcludeRx` was active after restart.
- CIHR remained healthy and was not interrupted during PHAC-specific recovery.

Still required:

- Determine why PHAC can cycle through resume attempts without parseable `crawlStatus` or new WARC mtimes.
- Decide whether the temporary `public-health-notices` exclusion remains justified once the deeper crawler/runtime issue is understood.
- Design the next repo-side mitigation before any further VPS recovery attempts.

## Open questions (still unknown)

- Why does PHAC keep touching `.archive_state.json` and advancing resume-attempt logs without surfacing any `crawlStatus` or new WARC output?
- Once the compatibility flag is live, is the temporary `public-health-notices` exclusion still necessary?
- Should HC pick up the same compatibility flag immediately through annual reconciliation, even if HC is not currently failing on the same pattern?

## Action items (TODOs)

- [x] Deploy the HC/PHAC Browsertrix compatibility change with a pinned ref and verify the VPS checkout contains `--disable-http2`. (priority=high)
- [x] Reconcile annual HC/PHAC job configs in production and confirm `show-job --id 6/7` reflect the canonical passthrough args. (priority=high)
- [x] Perform one controlled PHAC restart with the new compatibility config and record the outcome in this note. (priority=high)
- [ ] Decide whether the temporary PHAC `public-health-notices` exclusion can be removed after live verification. (priority=medium)
- [ ] If PHAC still flatlines after the compatibility change, capture the current no-progress failure mode and design a follow-up mitigation. (priority=medium)
- [ ] Improve ops visibility for repeated `Resume Crawl` churn without `crawlStatus` so this state is obvious in VPS snapshots and metrics. (priority=medium)

## Automation opportunities

- Extend operator snapshots so they surface the live Browsertrix compatibility flags alongside scope filters for running annual jobs.
- Consider a dedicated “config drift before recovery” operator check in crawl-stall tooling so stale VPS checkouts are caught immediately.
- Surface repeated `Resume Crawl` stage churn as a first-class ops signal; counting only `New Crawl Phase` churn hid this incident's actual behavior.

## References / Artifacts

- Operator snapshot script: `scripts/vps-crawl-status.sh`
- Playbook: `../playbooks/crawl/crawl-stalls.md`
- Playbook: `../playbooks/core/deploy-and-verify.md`
- Runbook: `../runbooks/crawl-restart-budget-low.md`
- Annual scope/source policy: `../annual-campaign.md`
