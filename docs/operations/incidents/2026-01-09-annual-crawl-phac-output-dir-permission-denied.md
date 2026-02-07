# Incident: Annual crawl — PHAC output dir not writable (2026-01-09)

Status: draft

## Metadata

- Date (UTC): 2026-01-09
- Severity: sev2
- Environment: production
- Primary area: crawl + storage
- Owner: (unassigned)
- Start (UTC): 2026-01-08T20:22:04Z
- End (UTC): 2026-01-09T13:39:52Z (mitigated; awaiting successful retry)

---

## Summary

The annual crawl job for `phac` (job 7) repeatedly failed immediately because its job `output_dir` was not writable (`PermissionError` while creating a `.writable_test_*` file). The job produced no WARCs and consumed its retry budget.

Recovery restored a writable output directory and reset the job’s retry budget (`retry_count=0`) so the worker can safely reattempt it when capacity is available.

## Impact

- User-facing impact: none directly, but annual campaign remained `Ready for search: NO` while jobs were incomplete.
- Internal impact: operator intervention required; `phac` job blocked; retry budget consumed.
- Data impact:
  - Data loss: no (no WARCs were produced).
  - Data integrity risk: low (failure-to-start; no partial WARC writes).
  - Recovery completeness: partial (job left `retryable`; not yet re-run at time of write-up).
- Duration: ~17 hours (first failure → operator repair + retry reset).

## Detection

- `./scripts/vps-crawl-status.sh --year 2026` showed:
  - `phac` job 7: `status=retryable`, `crawl_rc=1`, `crawl_status=failed`, `WARC files=0`
- Worker journal showed the root symptom during job startup:
  - `CRITICAL ... Output directory ... is invalid or not writable: [Errno 13] Permission denied: .../.writable_test_<pid>`

## Decision log

- 2026-01-09 — Avoided interventions that stop `healtharchive-worker.service` while `cihr` was actively crawling (to reduce the risk of turning an in-progress crawl into a `failed` job at max retries).

## Timeline (UTC)

- 2026-01-08T20:22:04Z — Worker picked job 7 (`phac`); job failed immediately due to `output_dir` not writable (Errno 13).
- 2026-01-09T05:21:15Z — Status snapshot: job 7 still `retryable`/failed with `0` WARCs.
- 2026-01-09T13:10Z — Confirmed the job output dir is an `sshfs` hot path mountpoint (`findmnt -T <output_dir>` shows `fstype=fuse.sshfs`).
- 2026-01-09T13:10Z — Attempted `chown` of the output dir failed (`Permission denied`) because the path is on `sshfs`.
- 2026-01-09T13:26:21Z — `ha-backend validate-job-config --id 7` confirmed crawler command construction and output dir resolution.
- 2026-01-09T13:39:52Z — Reset `retry_count` to `0` via Python + SQLAlchemy session so job can be retried safely.

## Root cause

- Immediate trigger: `archive_tool` refused to start because `output_dir` was not writable.
- Underlying cause(s): job output directory mount/permissions were not compatible with the worker/crawler runtime user (details TBD).

## Contributing factors

- Direct `psql` access from the operator account failed due to missing local DB role mapping (e.g., `role "haadmin" does not exist`).
- The `output_dir` is on `sshfs`, so ownership fixes via `chown` are not available on the VPS; recovery requires “make the mount writable” rather than “change owner”.

## Resolution / Recovery

- Diagnosed job output dir mount + permissions:
  - Confirmed job config and path:
    - `ha-backend show-job --id 7` → `Output dir: /srv/healtharchive/jobs/phac/20260101T000502Z__phac-20260101`
  - Confirmed it is an `sshfs` hot path mountpoint:
    - `findmnt -T /srv/healtharchive/jobs/phac/20260101T000502Z__phac-20260101 -o TARGET,SOURCE,FSTYPE,OPTIONS`
  - Confirmed the worker user:
    - `systemctl show -p User -p Group healtharchive-worker.service` → `User=haadmin`, `Group=haadmin`
  - Attempted to fix ownership failed (`Permission denied`) because the output dir is on `sshfs`:
    - `sudo chown <worker_user>:<worker_group> <output_dir>`
- Ensured a writable output dir:
  - Verified writability with a host-level probe:
    - `touch /srv/healtharchive/jobs/phac/20260101T000502Z__phac-20260101/.writable_test && rm /srv/healtharchive/jobs/phac/20260101T000502Z__phac-20260101/.writable_test`
  - Validated annual tiering state for `phac`:
    - `sudo /opt/healtharchive-backend/.venv/bin/python3 /opt/healtharchive-backend/scripts/vps-annual-output-tiering.py --year 2026 --sources phac --apply`
- Validated job configuration:
  - `ha-backend validate-job-config --id 7`
- Reset the job retry budget:
  - Direct `psql` access failed due to missing DB roles for the operator account (`role "haadmin" does not exist`, `role "root" does not exist`).
  - Used a small Python snippet with `ha_backend.db.get_session()` to set `retry_count=0` for `job_id=7`:
    - ```bash
      /opt/healtharchive-backend/.venv/bin/python3 - <<'PY'
      from ha_backend.db import get_session
      from ha_backend.models import ArchiveJob

      job_id = 7
      with get_session() as session:
          job = session.get(ArchiveJob, job_id)
          if job is None:
              raise SystemExit(f"job {job_id} not found")
          old = job.retry_count
          job.retry_count = 0
          session.commit()
          print(f"OK job_id={job_id} retry_count {old} -> {job.retry_count}")
      PY
      ```

## Post-incident verification

- Confirmed output dir is writable on the host.
- Confirmed job config dry-run passes.
- Confirmed job shows `Status: retryable`, `Retry count: 0`.

## Open questions (still unknown)

- Why did this `sshfs` hot path mount become non-writable for the worker user?
- Is there any automation that can proactively detect “output dir not writable” before a crawl attempt consumes retries?
- Should the worker/crawler user be changed to a dedicated service account (instead of an operator user) to reduce permission drift?

## Action items (TODOs)

- [ ] Identify why this job’s `output_dir` was not writable (mount type + UID/GID expectations) and document the invariant we rely on. (priority=high)
- [x] Add an operator-safe command to reset a crawl job’s retry budget: `ha-backend reset-retry-count` (dry-run by default; `--apply` required; skips running/lock-held jobs). (implemented 2026-02-06)
- [ ] Consider treating “output dir not writable” as an `infra_error` class so it does not consume retry budget. (priority=medium)
- [ ] Add a short ops note: when `psql` roles are missing, use the DB session method (Python snippet) rather than forcing `psql` as root. (priority=low)

## Automation opportunities

- Add a periodic “job output dir writability probe” (metrics + alert) for queued/running annual jobs.
- Expand tiering/repair automation to ensure hot-path output dirs are consistently mounted/writable before a crawl starts.

## References / Artifacts

- Operator snapshot script: `scripts/vps-crawl-status.sh`
- Incident response playbook: `../playbooks/core/incident-response.md`
- Crawl stalls playbook: `../playbooks/crawl/crawl-stalls.md`
- Storage hot-path incidents: `../playbooks/storage/storagebox-sshfs-stale-mount-recovery.md`
- Note: job 7 produced no combined crawl logs because it failed before `archive_tool` started.
- Related: `2026-01-09-annual-crawl-hc-job-stalled.md`
