# Incident: Annual crawl — Errno 107 hot-path issue triggered infra-error thrash and worker stopped (2026-01-24)

Status: draft

## Metadata

- Date (UTC): 2026-01-24
- Severity (see `severity.md`): sev1
- Environment: production (single VPS)
- Primary area: storage
- Owner: (unassigned)
- Start (UTC): 2026-01-24T06:17:10Z (approx; storage hot-path auto-recover began running repeatedly)
- End (UTC): 2026-01-24T12:31:03Z (approx; worker restarted and annual crawl resumed)

---

## Summary

During the annual 2026 campaign, Storage Box “hot path” mountpoints under `/srv/healtharchive/jobs/**` intermittently became stale and returned `OSError: [Errno 107] Transport endpoint is not connected`. The worker then repeatedly picked the PHAC annual job (job 7), immediately failed with an infra error, and re-picked it in a tight loop. Shortly after, the worker service became inactive, leaving the campaign with no running jobs until manual intervention restarted the worker and re-launched the HC crawl (job 6).

## Impact

- User-facing impact: none directly observed, but annual campaign remained `Ready for search: NO` and made no progress while the worker was down.
- Internal impact (ops burden, automation failures, etc):
  - Alert noise (repeated infra errors and hot-path auto-recover runs).
  - Worker stopped; no jobs ran until manual restart.
  - Increased risk of “new crawl phase” restarts (loss of frontier continuity) after recovery.
- Data impact:
  - Data loss: unknown (no evidence of WARC deletion in this incident record).
  - Data integrity risk: medium (stale mounts can interrupt writes; “new crawl phase” can reduce completeness by losing crawl frontier).
  - Recovery completeness: partial (worker restarted and crawl resumed; underlying Errno 107 trigger not fully understood).
- Duration: ~6h 14m (approx; 06:17Z → 12:31Z).

## Detection

- Operator reported overnight warning/error notifications (approx 5 hours before 12:22Z).
- `./scripts/vps-crawl-status.sh --year 2026` at ~12:22Z showed:
  - `FAIL worker service is not active`
  - `healtharchive_crawl_running_jobs 0`
  - `phac` job marked `crawl_status=infra_error`
  - storage hot-path watchdog recorded tiering failures mentioning `Errno 107` for PHAC/CIHR hot paths.
- Worker journal around 06:28Z showed rapid repetition of the same infra error for job 7.
- `healtharchive-crawl-auto-recover.service` was running every ~5 minutes throughout the window and consistently completed successfully (“Deactivated successfully”), which suggests it was likely not the component that stopped the worker. (See timeline and artifacts.)

## Timeline (UTC)

- 2026-01-24T05:45:10Z — `healtharchive-crawl-auto-recover.service` runs (timer-driven), completes successfully.
- 2026-01-24T05:50:10Z — `healtharchive-crawl-auto-recover.service` runs, completes successfully.
- 2026-01-24T05:55:10Z — `healtharchive-crawl-auto-recover.service` runs, completes successfully.
- 2026-01-24T06:00:10Z — `healtharchive-crawl-auto-recover.service` runs, completes successfully.
- 2026-01-24T06:05:01Z — `healtharchive-crawl-auto-recover.service` runs, completes successfully.
- 2026-01-24T06:10:10Z — `healtharchive-crawl-auto-recover.service` runs, completes successfully.
- 2026-01-24T06:15:07Z — `healtharchive-crawl-auto-recover.service` runs, completes successfully.
- 2026-01-24T06:17:10Z — `healtharchive-storage-hotpath-auto-recover.service` begins running repeatedly (timer-driven).
- 2026-01-24T06:28:01Z — Worker picks job 7 (PHAC) and immediately raises `Errno 107` for the job output dir; the same job is re-picked repeatedly within the same second (infra-error thrash).
- 2026-01-24T06:28:02Z — `healtharchive-worker.service` becomes inactive (stopped); no running jobs remain.
- 2026-01-24T06:30:10Z — `healtharchive-crawl-auto-recover.service` runs, completes successfully.
- 2026-01-24T06:35:10Z — `healtharchive-crawl-auto-recover.service` runs, completes successfully.
- 2026-01-24T06:40:10Z — `healtharchive-crawl-auto-recover.service` runs, completes successfully.
- 2026-01-24T06:45:05Z — `healtharchive-crawl-auto-recover.service` runs, completes successfully.
- 2026-01-24T06:50:01Z — `healtharchive-crawl-auto-recover.service` runs, completes successfully.
- 2026-01-24T06:55:10Z — `healtharchive-crawl-auto-recover.service` runs, completes successfully.
- 2026-01-24T07:00:05Z — `healtharchive-crawl-auto-recover.service` runs, completes successfully.
- 2026-01-24T07:05:01Z — `healtharchive-crawl-auto-recover.service` runs, completes successfully.
- 2026-01-24T07:10:01Z — `healtharchive-crawl-auto-recover.service` runs, completes successfully.
- 2026-01-24T12:22:11Z — Operator status snapshot confirms worker inactive and no running jobs.
- 2026-01-24T12:31:03Z — Manual recovery: worker started; job 6 (HC) restarts a crawl container and begins a “new crawl phase”.

## Root cause

- Immediate trigger:
  - One or more hot-path mountpoints returned `Errno 107` during job execution (observed for PHAC output dir; watchdog also reported PHAC/CIHR paths).
- Underlying cause(s):
  - Likely intermittent `sshfs`/FUSE hot-path instability (similar failure mode to `2026-01-08-storage-hotpath-sshfs-stale-mount.md`), but root trigger remains unconfirmed.
  - Worker behavior on infra errors allowed immediate re-pick of the same job with no effective cooldown, causing log spam and increased operational risk.
  - The worker stop event at `2026-01-24T06:28:02Z` was a `systemd` stop; based on available logs, `healtharchive-crawl-auto-recover.service` does not appear to be the direct cause (it continued running successfully before/after).

## Contributing factors

- Infra error handling did not appear to enforce a cooldown/backoff for fast-failing jobs, enabling a tight loop.
- Hot-path auto-recovery ran frequently but did not resolve the condition before the worker stopped.
- Operator CLI confusion: running `ha-backend show-job --id 6` without exporting `/etc/healtharchive/backend.env` defaulted to SQLite and failed with `no such table: archive_jobs` (not causal, but slowed diagnosis).

## Resolution / Recovery

Manual recovery steps performed (state-changing):

1) Confirm mounts were readable again (spot-check):

```bash
timeout 5 ls -la /srv/healtharchive/storagebox >/dev/null
timeout 5 ls -la /srv/healtharchive/jobs/phac/20260101T000502Z__phac-20260101 >/dev/null
timeout 5 ls -la /srv/healtharchive/jobs/cihr/20260101T000502Z__cihr-20260101 >/dev/null
```

2) Restart the worker:

```bash
sudo systemctl start healtharchive-worker.service
```

3) Verify the crawl restarted and a zimit container is running:

```bash
./scripts/vps-crawl-status.sh --year 2026
sudo systemctl --no-pager --full status healtharchive-worker.service
```

Observed outcome: job 6 (HC) restarted at ~12:31Z and began writing new crawl temp dirs/WARCs under the existing job output directory.

### Additional recovery work (2026-01-25)

- Reset the retry budget for jobs 7 (PHAC) and 8 (CIHR) by writing `retry_count=0` directly via the backend ORM so the re-runs behaved like fresh attempts.
- Fixed the output-directory permissions (`chown -R haadmin:haadmin`, `chmod 755`) and confirmed writability by touching `.writable_test_manual` inside each job dir as `haadmin`.
- Launched the jobs through the transient `systemd-run`-based helper (`scripts/vps-run-db-job-detached.py / systemd-run … run-db-job --id …`) so the crawls kept running while our SSH sessions closed.
- Relaxed permissions on the existing `.tmpt*` directories (`docker run --rm -v "…:/output" alpine sh -c 'chmod -R a+rX /output/.tmp*'`) so the hot-path watchdog/ops scripts could read the WARCs without manual chmods.

## Post-incident verification

- Worker/job health checks:
  - `sudo systemctl status healtharchive-worker.service --no-pager -l` (worker active)
  - `./scripts/vps-crawl-status.sh --year 2026` (running job detected; metrics OK; crawlStatus advancing)
- Storage/mount checks:
  - `findmnt -T /srv/healtharchive/jobs/phac/20260101T000502Z__phac-20260101`
  - `findmnt -T /srv/healtharchive/jobs/cihr/20260101T000502Z__cihr-20260101`
- Integrity checks:
  - Not performed as part of this initial incident note; consider WARC sampling on job 6 after stabilization.

## Open questions (still unknown)

- What is the underlying trigger for hot-path mount staleness (network blip, Storage Box behavior, sshfs option mismatch, local FUSE behavior)?
- Why did the worker service stop shortly after the infra-error thrash (explicit stop by automation vs worker exit)?
- Does the current infra-error cooldown/backoff logic actually prevent tight re-pick loops in practice, and is it observable via metrics/logs?

## Action items (TODOs)

- [x] Implement mitigations and recovery improvements in `docs/planning/implemented/2026-01-24-infra-error-and-storage-hotpath-hardening.md`. (owner=eng, priority=high, due=2026-02-01)
- [ ] Investigate underlying cause of the hot-path staleness (Storage Box/sshfs/network/FUSE). (owner=eng, priority=high, due=2026-02-01)
- [x] Add a worker-side guardrail to prevent tight “pick same job instantly” loops on infra errors (cooldown + metric). (owner=eng, priority=high, due=2026-02-01)
- [x] Add a playbook section for “CLI shows sqlite/no such table” (env export reminder) to reduce operator confusion. (owner=ops, priority=low, due=2026-02-01)

## Follow-up implementation details

- Added `scripts/vps-run-db-job-detached.py` and updated `docs/deployment/systemd/README.md` to point operators at this helper so specific jobs can be re-run within transient `systemd-run` units without keeping a shell session attached.
- Extended `scripts/vps-storage-hotpath-auto-recover.py` to probe the output directories of queued/retryable jobs (not just the currently running ones) so stale hot paths can be detected before the worker picks them; detection still strictly unmounts only the specific stale targets.
- Extended the archive tool to discover `.tmp*` temp directories immediately after the container starts and periodically throughout the crawl. When `--relax-perms` is enabled the helper now runs during the crawl (configurable interval) so host commands can read WARCs without manual `chmod`, not just after the job finishes.
- Added a `last_healthy_*` timestamp to the storage hot-path watchdog state and Prometheus textfile metrics, while continuing to gate actual recovery attempts via the existing `last_apply_*` cooldown/cap fields. This gives dashboards a clearer signal when the watchdog has seen no stale targets.

## Automation opportunities

- Safe automation:
  - Detect `Errno 107` at the job output-dir boundary and pause/park affected jobs with a cooldown rather than thrashing the worker loop.
  - If `Errno 107` is detected with high confidence, automate a conservative recovery sequence (stop worker → unmount stale hot paths only → re-apply tiering/bind mounts → restart worker), but only when crawl integrity risk is acceptable.
- What should stay manual:
  - Any automation that unmounts paths used by an actively running crawl container should be guarded heavily (risk of interrupting writes / frontier continuity).

## References / Artifacts

- Related incident: `docs/operations/incidents/2026-01-08-storage-hotpath-sshfs-stale-mount.md`
- Follow-up roadmap: `docs/planning/implemented/2026-01-24-infra-error-and-storage-hotpath-hardening.md`
- `healtharchive-crawl-auto-recover.service` journal window:
  - `sudo journalctl -u healtharchive-crawl-auto-recover.service --since '2026-01-24 05:45:00' --until '2026-01-24 07:15:00' --no-pager | tail -n 200`
- Most relevant worker log excerpt (redacted):

```text
Jan 24 06:28:01 <vps> ha-backend[...]: 2026-01-24 06:28:01,050 [INFO] healtharchive.worker: Worker picked job 7 for source phac (...) with status retryable and retry_count 0
Jan 24 06:28:01 <vps> ha-backend[...]: 2026-01-24 06:28:01,056 [WARNING] healtharchive.jobs: Job 7 raised during archive_tool execution: [Errno 107] Transport endpoint is not connected: '/srv/healtharchive/jobs/phac/20260101T000502Z__phac-20260101'
Jan 24 06:28:01 <vps> ha-backend[...]: 2026-01-24 06:28:01,063 [WARNING] healtharchive.worker: Crawl for job 7 failed due to infra error (RC=1). Not consuming retry budget (retry_count=0).
Jan 24 06:28:01 <vps> ha-backend[...]: 2026-01-24 06:28:01,068 [INFO] healtharchive.worker: Worker picked job 7 for source phac (...) with status retryable and retry_count 0
Jan 24 06:28:02 <vps> systemd[1]: Stopping healtharchive-worker.service - HealthArchive Worker...
Jan 24 06:28:02 <vps> systemd[1]: healtharchive-worker.service: Deactivated successfully.
```
