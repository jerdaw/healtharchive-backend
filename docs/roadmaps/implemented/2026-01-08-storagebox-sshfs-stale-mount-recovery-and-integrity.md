# Storage Box / `sshfs` stale mount incident — prevention, auto-recovery, and data integrity (2026-01-08) — implementation plan

Status: **implemented** (completed 2026-01-16; created 2026-01-08)

This plan documents a real production incident and turns it into a concrete, sequenced set of repo changes to:

1) prevent recurrence (or at least detect it quickly),
2) automate safe recovery, and
3) preserve data integrity and crawl completeness when it happens anyway.

It is intentionally **very detailed** so future operators can use it as:

- an incident postmortem reference,
- a runbook for manual recovery,
- and the canonical implementation plan for the code changes.

## Executive summary

**What happened:** Several job output directories under `/srv/healtharchive/jobs/**` became unreadable with:

- `OSError: [Errno 107] Transport endpoint is not connected`

This is a classic FUSE failure mode (commonly seen with `sshfs`) where a mountpoint stays present but the underlying connection is gone, so basic filesystem operations (`stat`, `is_dir`, `ls`, etc.) fail.

**Impact:** The crawler/worker and related monitoring scripts attempted to `stat()` files under these mountpoints, threw exceptions, and the system degraded into a “looks alive but isn’t making progress” state:

- `archive_tool` crashed on `Path.is_file()` against a combined log path.
- The worker loop hit unexpected exceptions while preparing or updating jobs, leaving jobs in confusing states.
- Crawl metrics timer failed repeatedly because the metrics writer script crashed while probing a job output dir.
- The annual campaign was blocked: jobs appeared `running` or ended up `failed`, with `indexed_pages=0`.

**Recovery:** We stopped the worker, identified the stale mountpoints, lazily unmounted them, re-applied the tiering mounts, recovered stale jobs to `retryable`, re-queued jobs for retry, restarted the worker, and confirmed crawl progress resumed (crawl counters increased; new `warc.gz` produced; stalled metric stayed 0).

**Root cause (proximate):** job output paths were backed by `sshfs`/FUSE mounts that became disconnected. “Storage Box mount service active” did not imply these per-job hot paths were healthy. Monitoring and error handling were not robust to this failure mode.

## Incident details (high-fidelity narrative)

### Environment and components involved

This incident concerns the “production single VPS” deployment described in:

- `docs/deployment/production-single-vps.md`

Key components in play:

- `healtharchive-worker.service` (runs the backend worker loop and launches crawls via Docker).
- `archive_tool` (in-tree crawler CLI under `src/archive_tool/`).
- `healtharchive-crawl-metrics.timer` / `healtharchive-crawl-metrics.service` (writes node_exporter textfile metrics via `scripts/vps-crawl-metrics-textfile.py`).
- Storage tiering / mounts (scripts under `scripts/` + systemd units documented under `docs/deployment/systemd/`).
- `scripts/vps-crawl-status.sh` (operator snapshot script used throughout this incident).

The job output directory pattern affected:

- `/srv/healtharchive/jobs/<source>/<job_id_timestamp>__<job_name>`

### Observable symptoms (what we saw)

At the filesystem layer:

- `ls` against a job output dir failed with: `Transport endpoint is not connected`
- directories showed as `d?????????` when listed (unstat’able)

At the service/script layer:

- The crawl metrics writer failed on a path probe:
  - `scripts/vps-crawl-metrics-textfile.py` crashed when calling `Path.is_dir()` because `Path.stat()` raised `OSError: [Errno 107] Transport endpoint is not connected`.
- `archive_tool` crashed on a combined log probe:
  - stack trace showed `src/archive_tool/main.py` calling `Path.is_file()` on the stage combined log path, which raised the same Errno 107.
- The worker loop logged “Unexpected error in worker iteration” with Errno 107 against per-job output paths.

At the job/state layer (as surfaced by `scripts/vps-crawl-status.sh` and the worker logs):

- campaign jobs became blocked in `running`/`queued`/`failed` states that did not reflect real crawl progress.
- retries were consumed by infrastructure errors (mount failures), not by true crawl failures.
- indexing remained at 0 because crawls were not completing successfully.

### Timeline (derived from the operator session and systemd journal)

This timeline focuses on the causal chain; it is deliberately explicit about each observed step.

1) **Crawl had been progressing previously**
   - Earlier snapshots showed job 6 (hc) `running` with steadily increasing `crawled` counts and new `warc.gz` files appearing over time.

2) **Mountpoints became stale/unreadable**
   - `ls -la` under `/srv/healtharchive/jobs/hc/...` failed with `Transport endpoint is not connected`.
   - The failing directories showed “unknown” metadata (`d?????????`) indicating `stat()` failed.

3) **Metrics writer began failing repeatedly**
   - `healtharchive-crawl-metrics.service` exited non-zero because it could not probe a job output dir without crashing.

4) **Crawl runs began failing in ways that looked like “stalls”**
   - `archive_tool` and the worker loop hit hard exceptions, leaving jobs stuck in `status=running` or `status=failed` without meaningful progress.

5) **Operator intervention recovered state**
   - Worker stopped, stale mountpoints unmounted, tiering re-applied, stale jobs marked retryable, worker restarted.

## Root cause analysis (RCA)

### Proximate cause

One or more `sshfs`-backed mountpoints under `/srv/healtharchive/jobs/**` entered a stale/disconnected state where `stat()` calls failed with:

- `OSError: [Errno 107] Transport endpoint is not connected`

### Contributing factors

**C1) “Base mount healthy” did not imply “hot paths healthy”.**

- The Storage Box mount service could remain “active” while specific mounted subpaths used by the crawler were broken.
- Monitoring primarily checked:
  - `/srv/healtharchive/storagebox` reachability, and
  - certain systemd unit health,
  but did not validate *every hot path we depend on*.

**C2) Scripts and critical paths were not robust to Errno 107.**

- `scripts/vps-crawl-metrics-textfile.py` crashed instead of emitting “unhealthy” metrics.
- `archive_tool` crashed instead of classifying the error as “storage unavailable” and making it recoverable by automation.

**C3) Job lifecycle semantics did not separate infra failures from crawl failures.**

- Infra errors consumed retry budgets and produced confusing states (`running` + `finished_at` inconsistencies; `crawl_rc`/`crawl_status` not clearly tied to a specific attempt).

**C4) Recovery steps existed but were not packaged as a single safe operation.**

- Operators could fix it (stop worker → unmount stale → reapply mounts → recover jobs), but the system did not do this automatically and safely.

### Impact assessment

Primary impact:

- Annual campaign blocked (no jobs completed or indexed during the failure window).

Secondary impact:

- Monitoring degraded (metrics writer failing), increasing time-to-detection.

Data risk:

- Partial writes or truncated `warc.gz` files are plausible if a mount disconnect occurred mid-write.
- “Completeness risk”: if resume state/config is lost and the crawler restarts “fresh”, crawl may recrawl already-covered pages and still miss some queued pages unless we persist resumption state reliably.

## Resolution (what we did, step-by-step)

This section is both a record and a proto-runbook.

### 1) Stabilize services

- Stop worker to prevent repeated failures while repairing storage:
  - `sudo systemctl stop healtharchive-worker.service`

### 2) Identify stale mountpoints

Use the playbook:

- `docs/operations/playbooks/storagebox-sshfs-stale-mount-recovery.md`

### 3) Repair mountpoints + re-apply tiering

Preferred:

- `sudo systemctl restart healtharchive-storagebox-sshfs.service`
- `sudo systemctl reset-failed healtharchive-warc-tiering.service`
- `sudo systemctl start healtharchive-warc-tiering.service`

### 4) Recover job state

Safest recovery for “stale running” jobs:

- `ha-backend recover-stale-jobs --older-than-minutes 10 --require-no-progress-seconds 3600 --apply`

Then restart the worker.

## Implemented outputs (what exists now)

As of 2026-01-16, this plan is considered implemented; the operational “surface area” is:

- Storage hot-path watchdog:
  - `scripts/vps-storage-hotpath-auto-recover.py`
  - `docs/deployment/systemd/healtharchive-storage-hotpath-auto-recover.timer`
  - sentinel: `/etc/healtharchive/storage-hotpath-auto-recover-enabled`
- Tiering bind-mount helper:
  - `scripts/vps-warc-tiering-bind-mounts.sh` (supports `--repair-stale-mounts`)
  - `docs/deployment/systemd/healtharchive-warc-tiering.service` uses `--repair-stale-mounts`
- Crawl stall recovery:
  - `scripts/vps-crawl-auto-recover.py` (safe-by-default; caps recoveries)
  - `ha-backend recover-stale-jobs` supports `--require-no-progress-seconds`
- Replay resilience:
  - replay systemd/runbook recommends `-v /srv/healtharchive/jobs:/warcs:ro,rshared`
  - replay smoke tests: `healtharchive-replay-smoke.timer`

Remaining follow-up (not in this plan) is alerting/visibility on the new metrics.

