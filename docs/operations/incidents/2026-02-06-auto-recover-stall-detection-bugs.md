# Incident: Auto-recover stall detection bugs (2026-02-06)

Status: closed

## Metadata

- Date (UTC): 2026-02-06
- Severity: sev2
- Environment: production
- Primary area: crawl
- Owner: jerdaw
- Start (UTC): 2026-02-05T06:07:24Z (hc job 6 last progress)
- End (UTC): 2026-02-06T02:40:07Z (job 6 recovered and restarted)

---

## Summary

The crawl auto-recover watchdog failed to detect and recover a stalled hc job (job 6) for ~20 hours due to a chain of four bugs. The watchdog ran every 5 minutes but incorrectly reported "no_stalled_jobs" despite the metrics exporter correctly flagging the stall. Manual investigation revealed bugs in stale log detection, runner detection, and cross-user lock file access. After fixing all four bugs, the watchdog successfully recovered the job automatically.

## Impact

- User-facing impact: No 2026 annual search data available during the ~20h stall period.
- Internal impact: Auto-recovery automation broken; required manual debugging.
- Data impact:
  - Data loss: no (WARCs preserved on disk)
  - Data integrity risk: no
  - Recovery completeness: complete (job resumed from last checkpoint)
- Duration: ~20 hours of stalled crawl; auto-recovery restored in ~2 hours of debugging/fixes.

## Detection

- Detected via manual crawl status check (`./scripts/vps-crawl-status.sh --year 2026`)
- Metrics showed disconnect: `healtharchive_crawl_running_job_stalled{job_id="6"} = 1` but auto-recover reported `reason="no_stalled_jobs"`
- Most useful signals:
  - Metrics exporter vs auto-recover discrepancy
  - Auto-recover service logs showing PermissionError crashes
  - `sysctl fs.protected_regular = 2` kernel setting

## Timeline (UTC)

- 2026-02-05T06:07:24Z — hc job 6 last progress (crawled 299 pages)
- 2026-02-06T00:00:00Z — Operator checks crawl status, notices 18+ hour stall
- 2026-02-06T00:15:00Z — Investigation begins: metrics show stall, auto-recover doesn't
- 2026-02-06T00:30:00Z — Root cause 1 found: `_find_job_log` using stale DB path
- 2026-02-06T00:45:00Z — Fix 1 deployed (4bddb7c)
- 2026-02-06T01:00:00Z — Auto-recover now detects stall but crashes on PermissionError
- 2026-02-06T01:15:00Z — Root cause 2 found: `_detect_job_runner` not checking job locks
- 2026-02-06T01:30:00Z — Fix 2 deployed (1648f74)
- 2026-02-06T01:45:00Z — Auto-recover still crashes: PermissionError on lock file
- 2026-02-06T02:00:00Z — Root cause 3 found: OSError not caught in recovery CLI
- 2026-02-06T02:10:00Z — Fix 3 deployed (e77212b)
- 2026-02-06T02:25:00Z — Still fails: `fs.protected_regular=2` blocks O_CREAT
- 2026-02-06T02:30:00Z — Root cause 4 found: O_CREAT on existing file in /tmp
- 2026-02-06T02:35:00Z — Fix 4 deployed (e073749), recovery succeeds
- 2026-02-06T02:40:07Z — Job 6 automatically restarted, crawling resumed

## Root cause

Four cascading bugs in the auto-recovery chain:

1. **Stale log detection**: Auto-recover's `_find_job_log` returned the DB `combined_log_path` immediately without checking for newer logs on disk. If the DB path pointed to an old log (from a previous attempt), the watchdog parsed stale data and skipped the job.

2. **Runner detection gap**: `_detect_job_runner` didn't check held job locks. A dead crawl subprocess + live worker lock = incorrectly classified as runner="none", triggering soft-recovery instead of full recovery.

3. **Permission error handling**: Lock probes only caught `JobAlreadyRunningError`, not `OSError`. When cross-user permission issues occurred (root auto-recover vs haadmin worker), the command crashed instead of skipping gracefully.

4. **fs.protected_regular kernel protection**: The kernel sysctl `fs.protected_regular=2` blocks `O_CREAT` on existing files in world-writable sticky directories (`/tmp`) when the caller doesn't own the file. The `_job_lock` function used `O_CREAT` unconditionally, causing EACCES for cross-user probes.

## Contributing factors

- Metrics exporter had already been fixed for bug #1 (stale log detection), but auto-recover was not updated in sync
- No integration tests covering cross-user lock file scenarios (root vs non-root)
- `fs.protected_regular=2` is a modern security feature not documented in the job lock implementation

## Resolution / Recovery

1. Fixed stale log detection (commit 4bddb7c):
   - Updated `_find_job_log` in `scripts/vps-crawl-auto-recover.py` to match metrics exporter logic
   - Added tests for newest-by-mtime selection

2. Fixed runner detection (commit 1648f74):
   - Added job lock probe as final fallback in `_detect_job_runner`
   - Classify as "worker" when lock is held and worker is running

3. Fixed permission error handling (commit e77212b):
   - Catch `OSError` in `cmd_recover_stale_jobs` lock probe
   - Treat `PermissionError` as "potentially held" in auto-recover

4. Fixed O_CREAT issue (commit e073749):
   - Try `O_RDWR` first in `_job_lock`, fall back to `O_CREAT | O_RDWR` only for new files
   - Bypasses `fs.protected_regular` restrictions on existing files

5. Fixed `/tmp/healtharchive-job-locks/` permissions:
   - One-time: `chmod 1777 /tmp/healtharchive-job-locks/`
   - One-time: `chmod 666 /tmp/healtharchive-job-locks/job-*.lock`

## Post-incident verification

- Auto-recover successfully detected and recovered job 6 at 02:35 UTC
- Worker automatically picked up retryable job 6 at 02:40 UTC
- New crawl log created, crawlStatus advancing normally (5 pages/3 min)
- All 238 tests pass, CI green
- Auto-recover timer running every 5 min without errors

## Action items

- [x] Fix stale log detection in auto-recover (completed: 4bddb7c)
- [x] Fix runner detection to check job locks (completed: 1648f74)
- [x] Handle PermissionError in lock probes (completed: e77212b)
- [x] Avoid O_CREAT on existing lock files (completed: e073749)
- [x] Document fs.protected_regular interaction in MEMORY.md (completed)
- [x] Create incident record (this file)
- [ ] Consider moving job locks out of /tmp to dedicated directory (priority=low, future improvement)

## Automation opportunities

- The fixes enable fully automatic recovery for future stalls
- No additional automation needed — the bugs prevented existing automation from working
- Keep metrics exporter and auto-recover `_find_job_log` logic in sync (ongoing maintenance)

## References / Artifacts

- Playbook: `docs/operations/playbooks/crawl/crawl-stalls.md`
- Auto-recover script: `scripts/vps-crawl-auto-recover.py`
- Metrics exporter: `scripts/vps-crawl-metrics-textfile.py`
- Test suite: `tests/test_ops_crawl_auto_recover_find_job_log.py`
- Operator scratch notes: (local, not in repo)
- Commits: 4bddb7c, 1648f74, e77212b, e073749
