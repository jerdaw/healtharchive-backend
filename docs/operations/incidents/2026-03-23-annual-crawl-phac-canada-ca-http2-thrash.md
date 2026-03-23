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

Follow-up log review later showed that compatibility change was itself invalid for the deployed zimit image: each restart failed during zimit's `warc2zim` preflight with `unrecognized arguments: --extraChromeArgs --disable-http2`. PHAC was then paused pending a rollback of that incompatible passthrough. The longer-term PHAC diagnosis still points to HTML/runtime churn on specific canada.ca families, but the immediate resume-stage `RC=2` failures were self-inflicted by the passthrough flag.

The immediate repo-side follow-up was to harden `archive_tool` monitoring so a
stage that emits no `crawlStatus` for a full stall window is treated as an
explicit monitored stall (`reason=no_stats`) instead of remaining silently
`running`. That improves the control plane, but it does not yet explain or fix
the underlying PHAC no-progress behavior.

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
- 2026-03-23T18:57:26Z — Direct inspection of the newest PHAC resume log showed `zimit: error: unrecognized arguments: --extraChromeArgs --disable-http2` during the `warc2zim` preflight check.
- 2026-03-23T19:xx:xxZ — Repo-side rollback prepared to remove the incompatible HC/PHAC Browsertrix passthrough from canonical annual source config.
- 2026-03-23Txx:xx:xxZ — Repo-side monitor hardening was implemented so stages
  that emit no `crawlStatus` for a full stall window now trigger
  `{"status": "stalled", "reason": "no_stats"}` instead of remaining silently
  `running`. Pending: deploy and observe on the VPS.

## Root cause

- Immediate trigger: repeated document-level HTTP/2 protocol failures on canada.ca pages prevented PHAC from making useful crawl progress.
- Secondary trigger introduced during mitigation: the deployed zimit image rejected `--extraChromeArgs --disable-http2` during its `warc2zim` preflight step, causing immediate `RC=2` failures before crawl startup.
- Underlying cause(s): current Browsertrix/chromium transport behavior appears incompatible with some canada.ca annual PHAC pages under the existing source profile; the single `public-health-notices` exclusion was not sufficient to restore progress.
- Control-plane gap discovered during follow-up: the monitor only treated
  "known progress went stale" as a stall, so stages that emitted no
  `crawlStatus` at all could avoid intervention indefinitely until the
  repo-side `no_stats` stall fallback was added.

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
- Identified that the compatibility change itself was invalid for the deployed zimit image because `warc2zim` preflight rejected those flags.
- Paused PHAC again rather than allowing repeated blind restarts.
- Implemented repo-side monitor hardening so stages that emit no `crawlStatus`
  for an entire stall window now trigger a `no_stats` intervention path.

## Post-incident verification

Completed so far:

- Public surface verification passed after the backend deploy.
- PHAC live process verification showed the reconciled `scopeExcludeRx` was active after restart.
- CIHR remained healthy and was not interrupted during PHAC-specific recovery.

Still required:

- Roll back the incompatible HC/PHAC `--disable-http2` passthrough in production and reconcile the live annual jobs.
- Re-test PHAC once with the narrowed PHAC HTML-family exclusions but without the broken Browsertrix passthrough.
- Decide whether the temporary `public-health-notices` exclusion remains justified once the deeper crawler/runtime issue is understood.
- Design the next repo-side mitigation only after that corrected rerun.

## Open questions (still unknown)

- Why does PHAC keep touching `.archive_state.json` and advancing resume-attempt logs without surfacing any `crawlStatus` or new WARC output?
- Once the broken compatibility flag is removed, is the temporary `public-health-notices` exclusion still necessary?
- After rollback, does PHAC return to the original timeout families or resume successfully with the narrowed exclusions?

## Action items (TODOs)

- [x] Deploy the HC/PHAC Browsertrix compatibility change with a pinned ref and verify the VPS checkout contains `--disable-http2`. (priority=high)
- [x] Reconcile annual HC/PHAC job configs in production and confirm `show-job --id 6/7` reflect the canonical passthrough args. (priority=high)
- [x] Perform one controlled PHAC restart with the new compatibility config and record the outcome in this note. (priority=high)
- [ ] Roll back the incompatible HC/PHAC `--disable-http2` passthrough with a pinned deploy and annual reconciliation. (priority=high)
- [x] Improve ops visibility for repeated `Resume Crawl` churn without `crawlStatus` so this state is obvious in VPS snapshots and metrics. (priority=medium)
- [x] Add a repo-side `archive_tool` monitor fallback so a stage with no `crawlStatus` for the full stall window triggers an explicit `no_stats` intervention instead of silently hanging. (priority=medium)
- [ ] Decide whether the temporary PHAC `public-health-notices` exclusion can be removed after live verification. (priority=medium)
- [ ] If PHAC still flatlines after the rollback, capture the current no-progress failure mode and design a follow-up mitigation. (priority=medium)

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
