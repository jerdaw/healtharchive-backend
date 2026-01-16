# Incident: Annual crawl — Storage hot-path sshfs mounts went stale (Errno 107) (2026-01-08)

Status: closed

## Metadata

- Date (UTC): 2026-01-08
- Severity (see `severity.md`): sev1
- Environment: production (single VPS)
- Primary area: storage
- Owner: (unassigned)
- Start (UTC): 2026-01-08T06:31:43Z (approx; first observed Errno 107 in worker logs)
- End (UTC): 2026-01-08T20:38:39Z (approx; crawler restarted and hot paths readable again)

---

## Summary

Several Storage Box “hot path” `sshfs` mountpoints under `/srv/healtharchive/jobs/**` became stale and started returning `OSError: [Errno 107] Transport endpoint is not connected`. This caused the worker to throw exceptions when reading/writing job output dirs, the crawl metrics textfile writer to fail repeatedly, and annual crawl jobs (HC/PHAC/CIHR) to fail/retry without making forward progress.

Recovery required stopping the worker, lazily unmounting the stale hot-path mountpoints, re-applying tiering bind mounts, and marking affected jobs as `retryable` so they could safely restart. After recovery, the worker successfully restarted the HC crawl and resumed writing WARCs to the output directory.

## Impact

- User-facing impact: none directly observed, but annual campaign remained `Ready for search: NO` while jobs were blocked/failing.
- Internal impact (ops burden, automation failures, etc):
  - Manual operator intervention required (mount recovery + job recovery).
  - `healtharchive-crawl-metrics.service` failed repeatedly (reduced visibility during the incident).
  - Worker loop repeatedly hit `Errno 107` and could not safely proceed with affected jobs.
- Data impact:
  - Data loss: unknown (no evidence of WARC deletion; risk was primarily loss of crawl continuity and partial/aborted crawl attempts).
  - Data integrity risk: medium (stale mounts can interrupt writes and break assumptions about output dir readability; risk reduced after later WARC verification).
  - Recovery completeness: complete for mount recovery; annual campaign completion remained in-progress.
- Duration: ~14 hours (approx; first Errno 107 observed in morning logs → successful crawl restart in the evening).

## Detection

- Operator status snapshot:
  - `./scripts/vps-crawl-status.sh --year 2026` showed `WARN job output dir not found/readable` and missing running-job log tails.
- Direct filesystem symptom:
  - `ls -la /srv/healtharchive/jobs/hc/` returned `Transport endpoint is not connected` and showed `d?????????` for the affected job dir.
- Monitoring symptom:
  - `systemctl status healtharchive-crawl-metrics.timer healtharchive-crawl-metrics.service` showed the metrics writer exiting non-zero.
  - `journalctl -u healtharchive-crawl-metrics.service` showed a traceback ending in `OSError: [Errno 107] Transport endpoint is not connected: '<job output dir>'`.
- Worker symptom:
  - `journalctl -u healtharchive-worker.service` showed `Unexpected error in worker iteration: [Errno 107] ...` while picking jobs 6/7/8.

### Most relevant excerpts (redacted)

Worker journal (error propagation into the worker loop):

```text
Jan 08 06:31:43 <vps> ha-backend[302894]: 2026-01-08 06:31:43,663 [WARNING] healtharchive.worker: Crawl for job 6 failed (RC=1). Marking as retryable (retry_count=1).
Jan 08 06:31:43 <vps> ha-backend[302894]: 2026-01-08 06:31:43,675 [INFO] healtharchive.worker: Worker picked job 6 for source hc (Health Canada) with status retryable and retry_count 1
Jan 08 06:31:43 <vps> ha-backend[302894]: 2026-01-08 06:31:43,684 [ERROR] healtharchive.worker: Unexpected error in worker iteration: [Errno 107] Transport endpoint is not connected: '/srv/healtharchive/jobs/hc/20260101T000502Z__hc-20260101'
Jan 08 06:32:13 <vps> ha-backend[302894]: 2026-01-08 06:32:13,694 [INFO] healtharchive.worker: Worker picked job 7 for source phac (Public Health Agency of Canada) with status queued and retry_count 0
Jan 08 06:32:13 <vps> ha-backend[302894]: 2026-01-08 06:32:13,702 [ERROR] healtharchive.worker: Unexpected error in worker iteration: [Errno 107] Transport endpoint is not connected: '/srv/healtharchive/jobs/phac/20260101T000502Z__phac-20260101'
Jan 08 06:32:43 <vps> ha-backend[302894]: 2026-01-08 06:32:43,711 [INFO] healtharchive.worker: Worker picked job 8 for source cihr (Canadian Institutes of Health Research) with status queued and retry_count 0
Jan 08 06:32:43 <vps> ha-backend[302894]: 2026-01-08 06:32:43,718 [ERROR] healtharchive.worker: Unexpected error in worker iteration: [Errno 107] Transport endpoint is not connected: '/srv/healtharchive/jobs/cihr/20260101T000502Z__cihr-20260101'
```

Crawl metrics writer failure (systemd service repeatedly failing due to `Errno 107` during output-dir probing):

```text
Traceback (most recent call last):
  File "/opt/healtharchive-backend/scripts/vps-crawl-metrics-textfile.py", line 174, in main
    log_path = _find_job_log(job)
  File "/opt/healtharchive-backend/scripts/vps-crawl-metrics-textfile.py", line 33, in _find_latest_combined_log
    if not output_dir.is_dir():
  File "/usr/lib/python3.12/pathlib.py", line 842, in stat
    return os.stat(self, follow_symlinks=follow_symlinks)
OSError: [Errno 107] Transport endpoint is not connected: '/srv/healtharchive/jobs/hc/20260101T000502Z__hc-20260101'
```

Filesystem symptom (stale FUSE mountpoint):

```text
$ ls -la /srv/healtharchive/jobs/hc/
ls: cannot access '/srv/healtharchive/jobs/hc/20260101T000502Z__hc-20260101': Transport endpoint is not connected
d????????? ? ? ? ? ? 20260101T000502Z__hc-20260101
```

## Decision log (optional but recommended for sev0/sev1)

- 2026-01-08T20:17Z (approx) — Decision: stop `healtharchive-worker.service` before unmounting hot paths (why: avoid concurrent reads/writes against a stale FUSE mount; risks: temporarily halts all crawl work).
- 2026-01-08T20:18Z (approx) — Decision: use `umount -l` (lazy) for stale mountpoints (why: avoid blocking on FUSE teardown; risks: processes holding FDs continue referencing the old mount until released).
- 2026-01-08T20:22Z (approx) — Decision: mark jobs as `retryable` (and later `retry-job`) after storage recovery (why: allow the worker to restart crawls cleanly; risks: consumes retry budget if repeated).

## Timeline (UTC)

- 2026-01-08T06:20:00Z — Worker monitoring logged repeated HTTP/Network errors (many `net::ERR_HTTP2_PROTOCOL_ERROR`) during the HC crawl (context for the long-running crawl).
- 2026-01-08T06:25:24Z — CrawlMonitor thread logged: “Docker logs stream ended” (crawl stage ended).
- 2026-01-08T06:31:43Z — `archive_tool` and the worker encountered `OSError: [Errno 107] Transport endpoint is not connected` on the HC job output dir; worker then hit the same error when attempting PHAC and CIHR output dirs.
- 2026-01-08T19:52:58Z — Operator ran `./scripts/vps-crawl-status.sh --year 2026` and observed job output dir unreadable and crawl jobs failing/retrying.
- 2026-01-08T20:09:02Z — `healtharchive-crawl-metrics.service` repeatedly failed with `Errno 107` while probing output dirs/logs.
- 2026-01-08T20:17Z (approx) — Operator stopped worker, unmounted stale hot-path mountpoints for job output dirs.
- 2026-01-08T20:18Z (approx) — First attempt to re-apply tiering bind mounts failed due to additional stale mounts under `/srv/healtharchive/jobs/imports/**`.
- 2026-01-08T20:21Z (approx) — Operator unmounted stale imports mountpoints and re-applied tiering bind mounts successfully.
- 2026-01-08T20:22Z (approx) — Operator ran `ha-backend recover-stale-jobs --apply` and restarted the worker; crawl metrics writer started succeeding again.
- 2026-01-08T20:34:34Z — Status snapshot showed annual jobs in `failed` (no running jobs); operator re-marked jobs `retryable` via `ha-backend retry-job`.
- 2026-01-08T20:38:39Z — Worker picked job 6 and successfully launched a new `zimit` container; crawl resumed and began producing new WARCs.

## Root cause

- Immediate trigger: one or more `sshfs` “hot path” mountpoints under `/srv/healtharchive/jobs/**` became stale, causing `stat(2)` and directory reads to fail with `Errno 107` (“Transport endpoint is not connected”).
- Underlying cause(s): unknown.
  - Hypothesis: transient network disruption between the VPS and Storage Box left multiple nested `sshfs` mounts in a stale-but-mounted state; the base Storage Box mount remained active, but hot-path submounts did not recover automatically.

## Contributing factors

- The system had multiple per-job/per-path `sshfs` mountpoints (“hot paths”), multiplying the surface area for FUSE staleness.
- Several code paths treated output-dir probes as infallible:
  - `archive_tool` attempted to `stat()` combined logs and raised an unhandled exception when the mount was stale.
  - The crawl metrics writer crashed rather than emitting a “probe failed” metric.
- No hot-path auto-recovery timer/sentinel was enabled at the time, so stale mountpoints persisted until manual intervention.
- The crawl was long-running and noisy (frequent HTTP2 protocol errors/timeouts), increasing the chance of being mid-operation when storage became unavailable.

## Resolution / Recovery

### 1) Confirm the symptom and scope

- Confirmed filesystem error:
  - `ls -la /srv/healtharchive/jobs/hc/` → `Transport endpoint is not connected`
- Confirmed the affected paths were `sshfs` mountpoints:
  - `mount | rg '/srv/healtharchive/jobs/(hc|phac|cihr)/20260101T000502Z__'`

### 2) Stop the worker to prevent concurrent I/O against stale mounts

```bash
sudo systemctl stop healtharchive-worker.service
```

### 3) Lazily unmount stale job output-dir hot paths

```bash
sudo umount -l /srv/healtharchive/jobs/hc/20260101T000502Z__hc-20260101
sudo umount -l /srv/healtharchive/jobs/phac/20260101T000502Z__phac-20260101
sudo umount -l /srv/healtharchive/jobs/cihr/20260101T000502Z__cihr-20260101
```

What this changed:

- Removed stale FUSE mountpoints so the tiering scripts could remount cleanly.

### 4) Re-apply tiering bind mounts (and clear any additional stale mounts)

First attempt surfaced additional stale mounts under legacy imports (same symptom):

```bash
sudo /opt/healtharchive-backend/scripts/vps-warc-tiering-bind-mounts.sh --apply
```

Then unmounted the stale imports mountpoints and re-ran the bind-mount script:

```bash
mount | rg '/srv/healtharchive/jobs/imports'
sudo umount -l /srv/healtharchive/jobs/imports/legacy-hc-2025-04-21
sudo umount -l /srv/healtharchive/jobs/imports/legacy-cihr-2025-04
sudo /opt/healtharchive-backend/scripts/vps-warc-tiering-bind-mounts.sh --apply
```

What this changed:

- Restored canonical tiered WARC paths and removed stale “imports” hot paths blocking the bind-mount installer.

### 5) Requeue stale jobs in the DB

```bash
set -a; source /etc/healtharchive/backend.env; set +a
/opt/healtharchive-backend/.venv/bin/ha-backend recover-stale-jobs --older-than-minutes 5 --apply --limit 10
```

What this changed:

- Marked jobs 6/7/8 as `retryable` so the worker could safely restart them after storage recovery.

### 6) Restart the worker and confirm metrics writer success

```bash
sudo systemctl start healtharchive-worker.service
systemctl status healtharchive-crawl-metrics.service --no-pager -l
```

### 7) Explicitly retry annual jobs and restart worker loop

```bash
set -a; source /etc/healtharchive/backend.env; set +a
/opt/healtharchive-backend/.venv/bin/ha-backend retry-job --id 6
/opt/healtharchive-backend/.venv/bin/ha-backend retry-job --id 7
/opt/healtharchive-backend/.venv/bin/ha-backend retry-job --id 8
sudo systemctl restart healtharchive-worker.service
```

What this changed:

- Ensured the jobs were eligible for immediate pickup and restarted the worker to pick a retryable job promptly.

## Post-incident verification

- Public surface checks:
  - Not performed as part of the storage recovery; incident scope was internal pipeline health.
- Worker/job health checks:
  - `sudo systemctl status healtharchive-worker.service --no-pager`
  - `./scripts/vps-crawl-status.sh --year 2026` (confirm jobs are running/retryable and output dirs readable)
  - `docker ps | rg 'ghcr.io/openzim/zimit'` (confirm active crawl container)
- Storage/mount checks (if relevant):
  - `mount | rg '/srv/healtharchive/jobs/(hc|phac|cihr)/20260101T000502Z__'`
  - `ls -la /srv/healtharchive/jobs/hc/20260101T000502Z__hc-20260101 | head`
- Integrity checks (if relevant):
  - After recovery, ran WARC verification (sampling) to reduce integrity uncertainty:
    - `/opt/healtharchive-backend/.venv/bin/ha-backend verify-warcs --job-id 6 --level 0 --limit-warcs 20`

## Open questions (still unknown)

- What was the underlying trigger for the `sshfs` hot-path staleness (network instability, server-side disconnect, local FUSE behavior, or sshfs option mismatch)?
- Why did multiple independent hot paths go stale at once (shared failure mode), while the base mount remained active?
- Should we treat `Errno 107` as a first-class “infra error” everywhere (worker, archive_tool, metrics) so it never consumes retry budget and never crashes the worker loop?

## Action items (TODOs)

- [x] Create a focused roadmap and implement guardrails/automation: `docs/roadmaps/implemented/2026-01-08-storagebox-sshfs-stale-mount-recovery-and-integrity.md` (owner=eng, priority=high, due=2026-01-15)
- [x] Add “hot path unreadable” metrics + alerting rules (owner=eng, priority=high, due=2026-01-15)
- [x] Add operator drill tooling for alert pipeline and stale-mount recovery (owner=eng, priority=medium, due=2026-01-20)
- [x] Enable `healtharchive-storage-hotpath-auto-recover.timer` + sentinel on production after a maintenance window (ensure it will not interrupt active crawls unexpectedly). (owner=ops, priority=high, due=2026-01-20, done=2026-01-16)
- [x] Add an operator runbook step to clear “failed” systemd unit state after recovery (`systemctl reset-failed ...`) so warning alerts don’t linger. (owner=ops, priority=medium, due=2026-01-20, done=2026-01-16)
- [ ] Investigate (and document) why hot-path mounts can become stale while the base mount remains OK; adjust sshfs options if needed. (owner=ops, priority=medium, due=unknown)

## Automation opportunities

- Safe automation implemented post-incident:
  - `scripts/vps-storage-hotpath-auto-recover.py` can detect `Errno 107` and perform a conservative recovery sequence (stop worker → unmount stale hot paths → re-apply tiering → requeue stale jobs → start worker), with safeguards (cooldowns, caps, “confirm runs”).
- Risk/false positives to consider:
  - Stopping the worker while a crawl is legitimately progressing can cause unnecessary job restarts and reduce annual coverage.
  - Unmount/remount operations are destructive if targeted at the wrong mountpoint; the detector must be confident (Errno 107) and scoped.
  - Automation should remain opt-in via a sentinel file and should be enabled only once its posture matches the desired operational risk tolerance.

## References / Artifacts

- Status snapshots:
  - `scripts/vps-crawl-status.sh` (see operator runs around 2026-01-08 19:52Z and 20:34Z)
- Relevant logs / error excerpts:
  - `sudo journalctl -u healtharchive-worker.service --since '2026-01-08 06:20' --until '2026-01-08 06:45' --no-pager -l`
  - `sudo journalctl -u healtharchive-crawl-metrics.service --since '2026-01-08 20:00' --no-pager -l`
- Job output dirs impacted:
  - `/srv/healtharchive/jobs/hc/20260101T000502Z__hc-20260101`
  - `/srv/healtharchive/jobs/phac/20260101T000502Z__phac-20260101`
  - `/srv/healtharchive/jobs/cihr/20260101T000502Z__cihr-20260101`
- Tiering / mounts:
  - `scripts/vps-warc-tiering-bind-mounts.sh`
  - `scripts/vps-annual-output-tiering.py`
  - Playbook: `../playbooks/storagebox-sshfs-stale-mount-recovery.md`
  - Drill playbook: `../playbooks/storagebox-sshfs-stale-mount-drills.md`
- Follow-up implementation plan:
  - `docs/roadmaps/implemented/2026-01-08-storagebox-sshfs-stale-mount-recovery-and-integrity.md`
