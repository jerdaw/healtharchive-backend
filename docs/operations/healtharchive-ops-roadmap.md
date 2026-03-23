# HealthArchive ops roadmap (internal)

This file tracks the current ops roadmap/todo items only. Keep it short and current.

For historical roadmaps and upgrade context, see:

- `docs/planning/README.md` (backend repo)

Keep the two synced copies of this file aligned:

- Backend repo: `docs/operations/healtharchive-ops-roadmap.md`
- Optional local working copy (non-git): if you keep a separate ops checklist outside the repo, keep it in sync with this canonical file.

## Recurring ops (non-IRL, ongoing)

- **Quarterly:** run a restore test and record a public-safe log entry in `/srv/healtharchive/ops/restore-tests/`.
- **Quarterly:** add an adoption signals entry in `/srv/healtharchive/ops/adoption/` (links + aggregates only).
- **Quarterly:** confirm dataset release exists and passes checksum verification (`sha256sum -c SHA256SUMS`).
- **Quarterly:** confirm core timers are enabled and succeeding (recommended: on the VPS run `cd /opt/healtharchive-backend && ./scripts/verify_ops_automation.sh`; then spot-check `journalctl -u <service>`).
- **Quarterly:** docs drift skim: re-read the production runbook + incident response and fix any drift you notice (keep docs matching reality).

## Current status (as of 2026-03-23)

- 2026 annual campaign is partially active on the VPS:
  - `cihr` remains running.
  - `hc` is still in a failed state from earlier annual-campaign churn.
  - `phac` is parked as `retryable` after the 2026-03-23 investigation and controlled restart attempt.
- Deploy-lock suppression is cleared (the stale `/tmp/healtharchive-backend-deploy.lock` was removed; auto-recover apply actions are no longer skipped due to deploy lock).
- Job lock-dir cutover is **staged** (non-disruptive) but not fully complete:
  - `/etc/healtharchive/backend.env` now sets `HEALTHARCHIVE_JOB_LOCK_DIR=/srv/healtharchive/ops/locks/jobs`
  - `/srv/healtharchive/ops/locks/jobs` exists with intended perms
  - Maintenance-window restart of services is still required to pick up the env change.
- Annual output-dir mount topology is currently **unexpected** (direct `sshfs` mounts instead of bind mounts) for the active 2026 jobs.
  - We are intentionally deferring conversion to bind mounts until a maintenance window to avoid interrupting in-progress crawls.
- PHAC annual crawl job 7 is no longer blocked on deploy/config drift.
  - The scope reconciliation fix and `--extraChromeArgs --disable-http2` compatibility flag were both deployed and verified in the live PHAC process on 2026-03-23.
  - The visible HTTP/2 error storm stopped, but PHAC still made no measurable progress and was parked as `retryable`.
  - Repo-side monitor hardening now exists for one part of the symptom: stages that emit no `crawlStatus` for a full stall window now trigger an explicit `no_stats` intervention instead of silently hanging.
- Alerting noise-reduction tuning is deployed and verified:
  - Alertmanager routing is severity-aware (`critical` keeps resolved notifications, non-critical suppresses resolved and repeats less often).
  - Crawl alerting is now automation-first and dashboard-driven:
    - Crawl-rate/churn notifications were removed (tracked in Grafana instead).
    - `Errno 107` job-level unreadable/writability symptom alerts are split out so storage watchdog alerts are the primary stale-mount signal.
    - Worker-down alerting waits for the worker auto-start watchdog window and suppresses during active deploy locks.
    - Watchdog freshness alerts were added for worker auto-start and crawl auto-recover timers.

## Current priority order

Treat the following as the current ops execution order:

1. PHAC repo-side mitigation and verification.
2. Job lock-dir cutover during a safe maintenance window.
3. Annual output-dir bind-mount conversion after the 2026 annual crawl is idle.
4. Routine quarterly ops and evidence collection.

## Current ops tasks (implementation already exists; enable/verify)

- PHAC follow-up is now repo-side investigation, not another live restart.
  - Current state: job `7` (`phac-20260101`) is parked `retryable` after a controlled restart with `--disable-http2` removed visible HTTP/2 thrash but did not restore crawl progress.
  - Current evidence: repeated resume-stage attempts, `.archive_state.json` updates, no parseable `crawlStatus`, and no new WARC mtimes.
  - Diagnostic update (2026-03-23): the new content-cost report plus direct log review point to PHAC HTML/runtime churn, not broad binary/media frontier waste.
    - Across the newest 120 combined logs, PHAC showed `3619` timeout signals concentrated under `en/public-health/services` and `fr/sante-publique/services`.
    - Concrete repeated pathological targets include the travel-health artesunate page pair, the English NACI subtree, the English CCDR subtree, and the English Canadian Immunization Guide subtree.
    - Current sampled WARC bytes remain dominated by normal pages/render assets rather than `.mp4`/dataset/document classes.
  - Next steps:
    - deploy the temporary PHAC HTML-family exclusions backed by that diagnosis
    - deploy the `no_stats` stall fallback with a pinned ref
    - reconcile annual PHAC tool options on the VPS so job `7` picks up the new canonical scope exclude regex
    - verify the next PHAC retry either makes measurable progress or surfaces an explicit monitored condition instead of silent `running`
    - continue the PHAC root-cause mitigation work in the repo before any further controlled restart
  - Do not do further blind PHAC recover/restart attempts from the VPS until that repo-side mitigation exists.
- Maintenance window: complete the job lock-dir cutover by restarting services that read `/etc/healtharchive/backend.env`.
  - This must wait until crawls are idle unless you explicitly accept interrupting them.
  - Plan + commands: `../planning/2026-02-06-crawl-operability-locks-and-retry-controls.md` (Phase 4)
- Maintenance window (after 2026 annual crawl is idle): convert annual output dirs from direct `sshfs` mounts to bind mounts.
  - Why defer: unmount/re-mount of a live job output dir can interrupt in-progress crawls; benefit is reduced Errno 107 blast radius,
    but not worth forced interruption mid-campaign.
  - Detection (crawl-safe): `python3 /opt/healtharchive-backend/scripts/vps-annual-output-tiering.py --year 2026`
  - Repair (maintenance only): stop the worker and ensure crawl containers are stopped, then:
    - `sudo python3 /opt/healtharchive-backend/scripts/vps-annual-output-tiering.py --year 2026 --apply --repair-unexpected-mounts --allow-repair-running-jobs`
- After any reboot/rescue/maintenance where mounts may drift:
  - Verify Storage Box mount is active (`healtharchive-storagebox-sshfs.service`).
  - Re-apply annual output tiering for the active campaign year and confirm job output dirs are on Storage Box (see incident: `incidents/2026-02-04-annual-crawl-output-dirs-on-root-disk.md`).
- After deploying new crawl tuning defaults (or if an annual campaign was started before the change):
  - Reconcile already-created annual job configs so retries/restarts adopt the new per-source profiles:
    - Dry-run: `ha-backend reconcile-annual-tool-options --year <YEAR>`
    - Apply: `ha-backend reconcile-annual-tool-options --year <YEAR> --apply`
- Verify the new Docker resource limit environment variables are set appropriately on VPS if defaults need adjustment:
  - `HEALTHARCHIVE_DOCKER_MEMORY_LIMIT` (default: 4g)
  - `HEALTHARCHIVE_DOCKER_CPU_LIMIT` (default: 1.5)
  - Post-deploy follow-through (alerting):
  - Review notification volume and alert outcomes after 7 days (firing + resolved counts by alertname/severity).
  - Confirm crawl throughput/churn investigations are being done via Grafana (`HealthArchive - Pipeline Health`) and not missed due to notification removal.
  - Consider a future composite crawl-degradation alert only if dashboard review repeatedly reveals actionable issues that are not otherwise alerted.

## IRL / external validation (pending)

After the immediate PHAC + maintenance-window items above, shift the main project emphasis back to the active admissions-strengthening work.

Track external validation/outreach work (partner, verifier, methods paper, dataset DOI, mentions/citations log) in:

- `../planning/roadmap.md`
