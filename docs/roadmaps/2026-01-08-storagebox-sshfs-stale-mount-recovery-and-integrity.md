# Storage Box / `sshfs` stale mount incident — prevention, auto-recovery, and data integrity (2026-01-08) — implementation plan

Status: **planned** (created 2026-01-08)

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
   - `healtharchive-crawl-metrics.service` exited non-zero because `scripts/vps-crawl-metrics-textfile.py` crashed probing a job output dir.
   - This broke one of the core “is it healthy?” signals (node_exporter textfile crawl metrics).

4) **Worker experienced hard failures due to filesystem probes**
   - The worker attempted to pick jobs but hit Errno 107 while touching job output dirs and/or reading logs.
   - Jobs were left in broken lifecycle states (e.g., `running` but no crawl actually happening; or `failed` with retries consumed).

5) **`archive_tool` crashed due to Errno 107**
   - The stack trace showed a crash in `src/archive_tool/main.py` when checking for the existence of the stage combined log file (via `Path.is_file()`), which raised Errno 107.
   - This is a key bug class: “filesystem probes should not bring down the entire workflow when the failure mode is known and recoverable.”

6) **Manual recovery started**
   - We stopped the worker (`systemctl stop healtharchive-worker.service`) to prevent it from repeatedly tripping over broken mountpoints while we repaired storage.
   - We enumerated mounts and confirmed that the affected hot paths were `fuse.sshfs` mountpoints (FUSE / `sshfs`).

7) **We lazily unmounted stale hot paths**
   - We ran `umount -l` against each affected per-job directory mountpoint for:
     - the current annual campaign job dirs (`hc`, `phac`, `cihr` for 2026-01-01),
     - and legacy import mountpoints that were also stale.

8) **We re-applied tiering mounts**
   - We ran `scripts/vps-warc-tiering-bind-mounts.sh --apply` to restore expected mounts.
   - The first attempt failed because legacy import mountpoints were still stale; after unmounting those, rerunning succeeded.
   - We also (re)started annual output tiering (`healtharchive-annual-output-tiering.service`) to restore per-job tiering mounts.

9) **We recovered job states**
   - We ran `ha-backend recover-stale-jobs --older-than-minutes 5 --apply` to set stale `running` jobs to `retryable`.
   - We explicitly marked job IDs 6/7/8 as retryable using `ha-backend retry-job --id <N>` (to ensure the worker would pick them even after prior failures).

10) **We restarted the worker and validated recovery**
- After `systemctl restart healtharchive-worker.service`, the worker picked job 6.
- `archive_tool` resumed and began a new crawl stage.
- `scripts/vps-crawl-status.sh --year 2026 --job-id 6` showed:
  - `crawled` steadily increasing,
  - `stalled=0`,
  - `last_progress_age_seconds` small,
  - and a new `warc.gz` file being written.

### Appendix A — Operator commands executed (for completeness)

These are the key commands that were run during diagnosis and recovery, in roughly the order they occurred.
This is intended as a “full fidelity” operational record, not as a recommendation that every command is always required.

Diagnostics:

- `./scripts/vps-crawl-status.sh --year 2026`
- `sudo systemctl status healtharchive-worker.service --no-pager`
- `ps -ef | rg 'ha-backend|archive_tool|zimit'`
- `ls -la /srv/healtharchive/jobs/hc/`
- `sudo journalctl -u healtharchive-worker.service --since '<time>' --no-pager`
- `systemctl status healtharchive-crawl-metrics.timer healtharchive-crawl-metrics.service --no-pager`
- `sudo journalctl -u healtharchive-crawl-metrics.service --no-pager -l`
- `sudo journalctl -u healtharchive-worker.service --since '<time>' --until '<time>' --no-pager -l`

Recovery:

- `sudo systemctl stop healtharchive-worker.service`
- `mount | rg '/srv/healtharchive/jobs/(hc|phac|cihr)/20260101T000502Z__'`
- `systemctl status healtharchive-storagebox-sshfs.service --no-pager -l`
- `sudo umount -l /srv/healtharchive/jobs/hc/20260101T000502Z__hc-20260101`
- `sudo umount -l /srv/healtharchive/jobs/phac/20260101T000502Z__phac-20260101`
- `sudo umount -l /srv/healtharchive/jobs/cihr/20260101T000502Z__cihr-20260101`
- `sudo /opt/healtharchive-backend/scripts/vps-warc-tiering-bind-mounts.sh --apply`
  - (initially failed due to stale legacy import mounts)
- `mount | rg '/srv/healtharchive/jobs/imports'`
- `sudo umount -l /srv/healtharchive/jobs/imports/legacy-hc-2025-04-21`
- `sudo umount -l /srv/healtharchive/jobs/imports/legacy-cihr-2025-04`
- `sudo /opt/healtharchive-backend/scripts/vps-warc-tiering-bind-mounts.sh --apply`
- `sudo systemctl start healtharchive-annual-output-tiering.service`
- `set -a; source /etc/healtharchive/backend.env; set +a`
- `/opt/healtharchive-backend/.venv/bin/ha-backend recover-stale-jobs --older-than-minutes 5 --apply --limit 10`
- `/opt/healtharchive-backend/.venv/bin/ha-backend retry-job --id 6`
- `/opt/healtharchive-backend/.venv/bin/ha-backend retry-job --id 7`
- `/opt/healtharchive-backend/.venv/bin/ha-backend retry-job --id 8`
- `sudo systemctl start healtharchive-worker.service` / `sudo systemctl restart healtharchive-worker.service`

Validation:

- `./scripts/vps-crawl-status.sh --year 2026`
- `./scripts/vps-crawl-status.sh --year 2026 --job-id 6`
- `sudo journalctl -u healtharchive-worker.service -n 80 --no-pager -l`

### What was “actually wrong” vs. what was “just noisy”

There were two different classes of errors in the same window:

1) **Remote-site/network crawl errors** (expected-ish operational noise)
   - e.g., `net::ERR_HTTP2_PROTOCOL_ERROR` from Canada.ca during page loads.
   - These can contribute to crawl slowdowns/backoff but are not, by themselves, “the incident”.

2) **Local storage/mount errors** (the incident)
   - Errno 107 on job output paths prevented normal operation and caused crashes.
   - This is an infrastructure integrity problem: it breaks persistence, logs, WARC output, and monitoring.

This plan focuses primarily on (2), while ensuring (1) continues to be handled by existing crawl monitoring/backoff mechanisms.

## Root cause analysis (RCA)

### Proximate cause

One or more `sshfs`-backed mountpoints under `/srv/healtharchive/jobs/**` entered a stale/disconnected state where `stat()` calls failed with:

- `OSError: [Errno 107] Transport endpoint is not connected`

### Contributing factors

**C1) “Base mount healthy” did not imply “hot paths healthy”.**

- The Storage Box mount service could remain “active” while specific mounted subpaths used by the crawler were broken.
- Our monitoring primarily checked:
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

- Attempted reads under job dirs failed with Errno 107.
- `mount` output showed affected hot paths as `fuse.sshfs` mounts under `/srv/healtharchive/jobs/...`.

### 3) Remove stale mounts

- Lazily unmount broken hot paths (per-job and legacy-import paths):
  - `sudo umount -l /srv/healtharchive/jobs/...`

Lazy unmount is used here as an emergency measure to detach a dead FUSE mount; see “Risks” below.

### 4) Re-apply tiering and mounts

- Re-apply tiering mounts:
  - `sudo scripts/vps-warc-tiering-bind-mounts.sh --apply`
- Ensure annual output tiering is applied:
  - `sudo systemctl start healtharchive-annual-output-tiering.service`

### 5) Repair job lifecycle state

- Recover stale jobs stuck in `running`:
  - `ha-backend recover-stale-jobs --older-than-minutes 5 --apply`
- Ensure jobs are retryable:
  - `ha-backend retry-job --id 6`
  - `ha-backend retry-job --id 7`
  - `ha-backend retry-job --id 8`

### 6) Restart worker and validate crawl progress

- Restart worker:
  - `sudo systemctl restart healtharchive-worker.service`
- Validate with:
  - `scripts/vps-crawl-status.sh --year 2026 --job-id 6`
  - confirm crawlStatus counters rise and `stalled=0`
  - confirm a new `warc.gz` appears under the active temp crawl dir

## What this plan will deliver (scope)

### In-scope outcomes

**Prevention / fast detection**

- Hot-path mount health detection for every active job output dir and every configured tiering mount.
- Metrics and alerts that fail “loudly” when any hot path becomes unreadable (even if the base mount service is still “active”).

**Automated recovery**

- A safe, rate-limited recovery automation that:
  - detects the Errno 107 failure mode,
  - stops the worker (if needed),
  - repairs mounts (unmount stale hot paths, restart mount services as needed, re-apply tiering),
  - moves impacted jobs back to `retryable` without consuming retry budget unfairly,
  - restarts the worker.

**Data integrity + crawl completeness**

- A post-recovery verification path that:
  - validates existing `warc.gz` outputs (gzip + structural WARC checks),
  - flags and quarantines truncated/corrupt outputs,
  - guarantees indexing sees all valid WARCs and does not silently skip due to transient mount issues.

### Non-goals (explicitly out of scope for this plan)

- Replacing the Storage Box provider.
- Moving off `sshfs` entirely (we may *evaluate* alternatives, but that is a larger infrastructure project).
- Implementing a new search/index engine (unrelated).

## Current-state map (code and ops touchpoints)

### Status + metrics scripts (current behavior)

- Crawl status snapshot: `scripts/vps-crawl-status.sh`
- Crawl metrics writer: `scripts/vps-crawl-metrics-textfile.py`

### Tiering / mounts (current behavior)

- WARC tiering mounts: `scripts/vps-warc-tiering-bind-mounts.sh`
- Annual output tiering: `scripts/vps-annual-output-tiering.py`

### Existing auto-recovery machinery to build on

- Crawl auto-recover: `scripts/vps-crawl-auto-recover.py`

### Backend worker and job semantics

- Worker loop: `src/ha_backend/worker/main.py`
- Job runner + archive_tool invocation: `src/ha_backend/jobs.py`
- Crawl stats update from logs: `src/ha_backend/crawl_stats.py` (and friends; exact module name may vary)

### Systemd and alerts

- Systemd docs index: `docs/deployment/systemd/README.md`
- Alert rules (Prometheus): `ops/observability/alerting/healtharchive-alerts.yml`

## Definition of Done (DoD)

This plan is “done” when all of the following are true in production:

1) `healtharchive-crawl-metrics.service` **never** fails due to Errno 107; it emits metrics that indicate the underlying problem instead.
2) A hot-path mount disconnect results in:
   - an alert within 2 minutes, and
   - an automated recovery attempt within 5 minutes (if enabled).
3) Automated recovery is safe:
   - rate-limited,
   - logs every action it takes,
   - and does not loop destructively.
4) After recovery:
   - existing WARC outputs are verified (at least gzip integrity),
   - corrupt outputs are detected and flagged,
   - indexing continues and does not silently miss valid WARCs.
5) Job retry semantics are improved so infra failures do **not** burn crawl retry budgets (or at minimum, are clearly separated and visible).

## Roadmap / implementation plan (sequenced work)

This plan is intentionally **sequential** and biased toward “ship the safest detection first”.

Guiding principles (applies to every phase below):

- **Single-VPS reality:** Prefer small, incremental, reversible changes over big infra shifts.
- **Safety-first automation:** Anything that stops services or unmounts paths must be:
  - disabled-by-default (sentinel gated),
  - rate-limited (state file),
  - and observable (metrics + logs + alerts).
- **Fail loud, not fragile:** Monitoring code must not crash because the thing it is monitoring is broken.
- **Idempotent ops:** Every script should be safe to re-run; default to dry-run.
- **Protect provenance:** Never silently drop or rewrite WARC artifacts; integrity checks should detect, quarantine, and/or force explicit operator action.
- **Low-cardinality metrics:** Prefer `job_id` + `source` labels over full paths; include full paths in logs and playbooks instead of metric labels where possible.

### Phase 0 — Preconditions + documentation (no crawler behavior changes)

Objective: lock in a shared mental model, reduce future incident response time, and avoid “tribal knowledge”.

Deliverables:

- A new operator playbook describing detection + manual recovery steps for Errno 107.
- Production single-VPS runbook updated with the symptom signature and the safe recovery path.
- A short “mount topology + invariants” reference section so the next failure can be reasoned about quickly.

#### 0.0 Confirm and document current mount topology (source of truth)

Goal: answer “what is supposed to be mounted where?” and “what hot paths do we depend on?”.

Work items:

- Document the intended topology (expected steady-state):
  - Storage Box base mount:
    - `/srv/healtharchive/storagebox` (sshfs, systemd: `healtharchive-storagebox-sshfs.service`)
  - Canonical hot paths under `/srv/healtharchive/jobs/**` are expected to be:
    - local directories on the VPS filesystem, **or**
    - bind mounts onto the Storage Box tier (commonly showing `fstype=fuse.sshfs` because the bind source is on sshfs).
  - WARC tiering bind mount manifest:
    - `/etc/healtharchive/warc-tiering.binds` used by `scripts/vps-warc-tiering-bind-mounts.sh`.
  - Annual output tiering for current year:
    - `scripts/vps-annual-output-tiering.py` (systemd: `healtharchive-annual-output-tiering.service`) bind-mounts annual job output dirs onto `/srv/healtharchive/storagebox/jobs/**`.

- Explicitly list the “hot paths” that must be readable for the system to function:
  - For crawling:
    - `ArchiveJob.output_dir` for the running job (from DB)
    - `<output_dir>/.archive_state.json`
    - `<output_dir>/archive_*.combined.log` (for monitoring and stats)
    - `<output_dir>/.tmp*/collections/**.warc.gz` (while crawl is running)
  - For indexing:
    - `<output_dir>/warcs/**/*.warc.gz` (preferred stable path)
    - legacy `.tmp*` WARCs (fallback, pre-consolidation)
  - For replay:
    - all `Snapshot.warc_path` referenced by indexed snapshots

- Add a short “how to verify topology” checklist (for operators) that uses:
  - `mount`, `findmnt`, `mountpoint`
  - `ls`/`stat` probes of representative hot paths
  - unit status checks for:
    - `healtharchive-storagebox-sshfs.service`
    - `healtharchive-warc-tiering.service`
    - `healtharchive-annual-output-tiering.service`
    - `healtharchive-worker.service`
    - metrics timers (`healtharchive-crawl-metrics.timer`, `healtharchive-tiering-metrics.timer`)

Acceptance criteria:

- We can answer (from docs) “which script/unit is responsible for which mount”.
- The docs clearly distinguish:
  - “service is active” vs “mount is readable”.

#### 0.1 Write the incident-specific playbook (manual recovery, end-to-end)

Target file (canonical): `docs/operations/playbooks/storagebox-sshfs-stale-mount-recovery.md`

The playbook must include:

- Symptoms:
  - Errno 107 signature and common shell manifestations (`d?????????`, `Transport endpoint is not connected`)
  - how it appears in:
    - `healtharchive-crawl-metrics.service` failures
    - worker logs
    - `archive_tool` stack traces
- Decision tree:
  - “Is the base Storage Box mount readable?”
  - “Are the hot paths readable?”
  - “Is the worker currently running a crawl?”
- Safe recovery procedure (explicit ordering):
  1) stop worker
  2) (optional) stop any active crawl container(s) if needed
  3) unmount stale hot paths (targeted)
  4) restore tiering mounts (warc tiering + annual output tiering)
  5) recover job lifecycle state (`recover-stale-jobs`, `retry-job`)
  6) restart worker
  7) validate progress + metrics
- Post-recovery integrity checks (lightweight + deeper options)

Acceptance criteria:

- A fresh operator (no context) can follow the playbook and recover safely.

#### 0.2 Update the production runbook (reduce time-to-diagnosis)

Update: `docs/deployment/production-single-vps.md`

Add a concise section with:

- the Errno 107 signature,
- “first 3 commands” triage,
- pointers to the new playbook.

Acceptance criteria:

- Production runbook includes this incident class and links the playbook.

### Phase 1 — Observability hardening (ship first; must be safe to deploy mid-crawl)

Objective: ensure we **detect** hot-path storage failures quickly and that monitoring does not crash.

This phase should be deployable during an annual crawl because it does not change crawl behavior, only monitoring robustness.

Deliverables:

- `healtharchive-crawl-metrics.service` never fails because a path probe raises Errno 107.
- New metrics that explicitly report hot-path readability for:
  - running jobs’ output dirs, and
  - tiering manifest hot paths.
- Alerting rules that page on hot-path failures (not just base mount failure).

#### 1.0 Define a metrics + alerting contract (before changing code)

Why: metrics names and label choices become “API”; we should design them deliberately.

Work items:

- Define (in this plan) the exact metrics to add, with semantics and labels:

  **Crawl metrics (existing file: `healtharchive_crawl.prom`)**

  - `healtharchive_crawl_metrics_ok` (existing):
    - Redefine semantics: “script ran and wrote metrics file successfully”.
    - Must remain `1` even if some jobs are unreadable (that should be expressed by per-job metrics).
  - `healtharchive_crawl_running_job_output_dir_ok{job_id,source}` (new, gauge):
    - 1 if `job.output_dir` can be `stat()`’d and is a directory.
    - 0 if it raises `OSError` or is missing.
  - `healtharchive_crawl_running_job_output_dir_errno{job_id,source}` (new, gauge):
    - `-1` when OK or not applicable.
    - otherwise the errno from the last failure (e.g., `107`).
  - `healtharchive_crawl_running_job_log_probe_ok{job_id,source}` (new, gauge):
    - 1 if we could locate a combined log and attempt progress parsing.
    - 0 if log discovery/stat/read failed (includes Errno 107).
  - Keep existing stall/progress metrics unchanged in name, but ensure:
    - if log is unreadable, we emit `progress_known=0` and `last_progress_age_seconds=-1` without crashing.

  **Tiering metrics (existing file: `healtharchive_tiering.prom`)**

  - Add a new family for “tiering hot paths” sourced from `/etc/healtharchive/warc-tiering.binds`:
    - `healtharchive_tiering_hot_path_ok{hot="..."}`
    - `healtharchive_tiering_hot_path_errno{hot="..."}`
  - Keep cardinality low:
    - this manifest should remain small and stable; if it grows large, switch to a hashed label key.

- Define alert rules that map to operator actions:
  - “hot path down” should link to the new playbook, not generic incident response.

Acceptance criteria:

- Metric names/labels are agreed before implementation.

#### 1.1 Harden crawl metrics writer against Errno 107 (and other `OSError`)

Target: `scripts/vps-crawl-metrics-textfile.py`

Current failure mode:

- `Path.is_dir()` / `Path.is_file()` / `Path.stat()` can raise `OSError` (Errno 107) and crash the script.

Implementation plan:

- Make all filesystem probes “best effort”:
  - wrap calls to:
    - `_find_latest_combined_log` (including `output_dir.is_dir()`, glob, and mtime stat)
    - `_find_job_log` (including `p.is_file()`)
    - `parse_crawl_log_progress` (it can throw from `crawl_stats.py` on `is_file`/`stat`)
  - catch `OSError` explicitly and record errno for metrics.
  - catch broad exceptions per-job (not globally) so one broken job does not suppress metrics for others.

- Emit the new per-job output_dir/log-probe metrics described in 1.0.

- Preserve atomic write behavior:
  - keep the existing “write tmp + rename” flow.
  - ensure the script still exits `0` when it successfully writes a metrics file, even if some jobs are unreadable.

Testing plan:

- Add unit tests that monkeypatch `Path.stat()` / `Path.is_dir()` to raise `OSError(107, ...)` and assert:
  - the script returns `0` and writes output,
  - per-job `_ok` is `0` and `_errno` is `107`,
  - `healtharchive_crawl_metrics_ok` remains `1`.

Deployment plan:

- Update nothing else first; just deploy and confirm:
  - `systemctl start healtharchive-crawl-metrics.service` exits 0.
  - node_exporter shows the new metrics.

Acceptance criteria:

- The specific failure seen in the incident (`OSError: [Errno 107] ...`) cannot crash the crawl metrics writer.

#### 1.1.1 (Optional but recommended) Harden shared crawl log parsing helpers

Rationale:

- Both `scripts/vps-crawl-metrics-textfile.py` and other backend code paths rely on `ha_backend.crawl_stats`.
- If `crawl_stats` raises on `stat()`/`is_file()`/`is_dir()`, it forces every caller to remember to wrap it.

Targets:

- `src/ha_backend/crawl_stats.py`:
  - ensure `parse_crawl_status_events_from_log_tail` and `parse_crawl_log_progress` never raise on `OSError` from:
    - `Path.is_file()`
    - `Path.stat()`
    - file open/read
  - return “no progress” (`None`/empty list) instead.

Acceptance criteria:

- Any call site can safely call `parse_crawl_log_progress` without crashing on Errno 107.

#### 1.2 Add hot-path tiering metrics (manifest-driven)

Target (preferred): evolve `scripts/vps-tiering-metrics-textfile.sh` into a Python implementation for errno-aware probes.

Rationale:

- Shell can detect “ls failed” but cannot reliably surface the errno without brittle string parsing.
- Python can emit errno precisely and keep logic maintainable.

Implementation plan:

- Add `scripts/vps-tiering-metrics-textfile.py` (new) that:
  - reads `/etc/healtharchive/warc-tiering.binds` (path configurable via `--manifest`),
  - for each entry:
    - probes `hot` with `os.stat` and (optionally) a lightweight `os.listdir` to catch permission/IO issues,
    - emits `hot_path_ok` and `hot_path_errno`.
  - keeps existing metrics:
    - `healtharchive_storagebox_mount_ok`
    - `healtharchive_systemd_unit_ok` / `healtharchive_systemd_unit_failed`
  - never crashes due to a bad hot path; it emits “ok=0”.

- Update `docs/deployment/systemd/healtharchive-tiering-metrics.service` to call the Python script instead of the shell script.
  - Keep the old shell script for one deploy cycle if desired, then remove only after confidence.

Testing plan:

- Unit tests for manifest parsing and errno capture (using a temp manifest file).

Acceptance criteria:

- We have a direct Prometheus signal for “tiering hot path unreadable” even when the base mount is OK.

#### 1.3 Alerting changes (make failure visible fast)

Target: `ops/observability/alerting/healtharchive-alerts.yml`

Implementation plan:

- Add alerts (names are suggestions; confirm naming convention first):
  - `HealthArchiveCrawlMetricsMissing`:
    - `expr: healtharchive_crawl_metrics_ok == 0`
    - `for: 5m`
    - severity: warning
    - runbook: incident response playbook or crawl monitoring playbook
  - `HealthArchiveCrawlOutputDirUnreadable`:
    - `expr: healtharchive_crawl_running_job_output_dir_ok == 0`
    - `for: 2m`
    - severity: critical
    - runbook: new stale mount recovery playbook
  - `HealthArchiveTieringHotPathUnreadable`:
    - `expr: healtharchive_tiering_hot_path_ok == 0`
    - `for: 2m`
    - severity: critical
    - runbook: new stale mount recovery playbook

- Ensure alerts are actionable:
  - include the `hot` label only if cardinality remains small.
  - include `job_id` + `source` for crawl output dir alerts.

Acceptance criteria:

- If the incident repeats, we get a page quickly even if “Storage Box mount ok” remains 1.

### Phase 2 — Automated recovery (conservative, sentinel-gated, rate-limited)

Objective: make the system self-heal from the specific failure class (Errno 107 hot paths) without creating a restart loop.

Important: this phase introduces automation that can stop services and unmount paths, so it must be:

- disabled-by-default,
- heavily audited,
- and carefully tested in dry-run mode first.

Deliverables:

- A new watchdog that:
  - detects hot-path unreadability (Errno 107),
  - performs the minimal recovery actions needed,
  - records state and prevents thrashing,
  - and restores progress automatically.

#### 2.0 Define recovery policy + safety caps (before writing the watchdog)

Policy decisions (explicitly decide and record):

- Trigger conditions (any of these should qualify):
  - A running job has `output_dir_ok=0` with errno=107 for **>= 2 minutes**, confirmed by **2 consecutive watchdog runs**.
  - Tiering manifest contains hot paths with `hot_path_ok=0` and errno=107 for **>= 2 minutes**, confirmed by **2 consecutive watchdog runs**.
  - Optional: base Storage Box mount unreadable for **>= 2 minutes** (already alerted; watchdog can act sooner if it sees errno=107).

- Actions allowed (ordered from least to most invasive):
  1) stop worker (to prevent further filesystem interactions during repair)
  2) unmount stale hot paths (targeted)
  3) restart Storage Box mount service (only if base mount unreadable)
  4) re-apply tiering mounts (warc tiering + annual output tiering)
  5) recover job lifecycle state (running → retryable)
  6) start worker

- Safety caps:
  - Maximum recoveries per hour (global) and per job/day (per-job).
  - Hard stop if the same recovery has been attempted recently and hot paths remain broken (avoid loops).
  - Locking to prevent concurrent runs (systemd timer overlap or manual invocation).

Recommended default values (chosen for a single-VPS, single-operator system where “do no harm” > “thrash to stay alive”):

- Watchdog cadence: **every 60 seconds** (systemd timer)
- Failure confirmation: **2 consecutive watchdog runs**
- Minimum failure age before acting: **120 seconds**
- Global recovery cooldown (min time between apply-mode recoveries): **15 minutes**
- Max recoveries per hour (global): **2**
- Max recoveries per day (global): **6**
- Max recoveries per job per day: **3**
- Storage Box restart wait budget (if base mount unreadable): **60 seconds total**, probe every **5 seconds**

Rationale:

- Errno 107 is usually persistent once it appears; 2 minutes avoids acting on a single transient probe failure.
- A 15-minute cooldown + hourly/daily caps prevents “stop/start loops” that could corrupt outputs or hide a deeper connectivity problem.
- Caps are high enough to self-heal occasional blips while still forcing a human to investigate sustained instability.

#### 2.1 Implement a hot-path recovery watchdog (new script)

Target new script: `scripts/vps-storage-hotpath-auto-recover.py`

Why a new script (vs modifying `vps-crawl-auto-recover.py`):

- Existing watchdog is tuned for “crawl stalled” (progress age) and not for “filesystem is broken”.
- Keeping them separate reduces the chance of coupling two different recovery loops.

Implementation outline:

- Inputs:
  - `--apply` (default dry-run)
  - `--state-file` (default: `/srv/healtharchive/ops/watchdog/storage-hotpath-auto-recover.json`)
  - `--lock-file` (default: `/srv/healtharchive/ops/watchdog/storage-hotpath-auto-recover.lock`)
  - `--manifest` (default: `/etc/healtharchive/warc-tiering.binds`)
  - `--storagebox-mount` (default: `/srv/healtharchive/storagebox`)
  - `--recover-older-than-minutes` (default: `2`; used only for fallback mode via `ha-backend recover-stale-jobs`)
  - `--max-recoveries-per-day` (default: `6`) and `--max-recoveries-per-hour` (default: `2`)
  - `--max-recoveries-per-job-per-day` (default: `3`)
  - `--min-failure-age-seconds` (default: `120`; do not react to a single transient stat failure)
  - `--cooldown-seconds` (default: `900`; minimum time between apply-mode recoveries)
  - `--storagebox-restart-wait-seconds` (default: `60`) and `--storagebox-restart-probe-interval-seconds` (default: `5`)

- Detection logic (pure; easy to unit test):
  - Probe base mount readability:
    - `os.listdir(storagebox_mount)` in a try/except; record errno.
  - Probe tiering manifest hot paths:
    - for each hot path, `os.stat(hot)`; record errno.
  - Probe running job output dirs:
    - query DB for `ArchiveJob.status == "running"` (requires backend env, like crawl metrics writer),
    - for each `job.output_dir`, `os.stat(output_dir)` and `os.listdir(output_dir)` (stat alone may be enough; listdir catches a different class of failures).

- Decision engine:
  - If only base mount broken: restart storagebox service, then re-apply tiering, then recover jobs.
  - If base mount OK but hot paths broken: unmount those hot paths, then re-apply tiering, then recover jobs.
  - If there are running jobs whose output_dir is broken: include them in “recover jobs” step even if they are still marked running.

- Apply-mode actions (must be logged and state-recorded):
  1) acquire lock (fail fast if already locked)
  2) `systemctl stop healtharchive-worker.service`
  3) if base mount unreadable:
     - `systemctl restart healtharchive-storagebox-sshfs.service`
     - wait/retry mount readability (bounded)
  4) unmount stale hot paths (targeted):
     - try `umount <path>` first
     - if it fails and path is still unreadable, fallback to `umount -l <path>`
     - never unmount broad parents (only the specific mountpoint paths)
  5) re-apply tiering:
     - `systemctl restart healtharchive-warc-tiering.service`
     - `systemctl start healtharchive-annual-output-tiering.service` (or run the script) so annual job dirs are re-mounted
  6) recover jobs:
     - Prefer targeted recovery: set only impacted `status=running` jobs to `status=retryable` (based on output_dir unreadable), and annotate `crawler_stage`.
     - Fallback (if targeted DB update is not implemented yet):
       - `ha-backend recover-stale-jobs --older-than-minutes 2 --apply` (optionally filtered to impacted sources and capped by `--limit`)
     - optional: mark specific job IDs retryable if their status was forced to `failed` by infra error (handled in Phase 3)
  7) `systemctl start healtharchive-worker.service`
  8) write state:
     - timestamp, detected failures, actions taken, success/failure
     - and include a “recovery reason” string (e.g., `errno_107_hotpath`)

Observability for the watchdog:

- Write a small node_exporter textfile metric (new file, e.g. `healtharchive_storage_hotpath_auto_recover.prom`) including:
  - last run timestamp
  - last run result (ok/failed)
  - number of recoveries in last 24h
  - number of hot paths currently failing

Acceptance criteria:

- In dry-run, the watchdog prints exactly what it would do, without side effects.
- In apply mode (in a controlled test), the watchdog successfully restores:
  - mount readability,
  - crawl metrics health,
  - and the worker picking jobs again.

#### 2.2 Add systemd units (disabled-by-default)

Target templates under `docs/deployment/systemd/`:

- `healtharchive-storage-hotpath-auto-recover.service`
- `healtharchive-storage-hotpath-auto-recover.timer`

Unit requirements:

- `ConditionPathExists=/etc/healtharchive/storage-hotpath-auto-recover-enabled`
  - do not enable by default; operator must opt in.
- `ConditionPathExists=/etc/healtharchive/backend.env` (needs DB access and ha-backend CLI)
- Run as root (needs to `systemctl stop/start` and `umount`).
- Timer frequency: **every minute** (consistent with existing HealthArchive watchdog/metrics timers); the script enforces safety via `min_failure_age_seconds=120` and a cooldown window.

Acceptance criteria:

- The new watchdog is shipped but inert until the sentinel exists and the timer is enabled.

#### 2.3 Ensure tiering/mount scripts are “watchdog-friendly” (idempotent + clearer failure modes)

Even with a watchdog, the underlying scripts it calls must behave predictably under failure.

Targets:

- `scripts/vps-warc-tiering-bind-mounts.sh`
- `scripts/vps-annual-output-tiering.py`

Work items:

- Make both scripts detect “stale mountpoint” errors and fail with actionable output (not just a raw `install:` failure).
  - For example, when a `mkdir`/`install -d` hits Errno 107:
    - print the specific path and the recommended remediation (`umount -l <path>`).
- Where safe, add a “repair mode” (still safe-by-default):
  - for example, a flag like `--repair-stale-mounts` that:
    - detects Errno 107 at a hot path,
    - attempts `umount`/`umount -l`,
    - then retries the mount operation.
  - If this is too risky inside the scripts, keep repair logic exclusively in the watchdog and ensure scripts stay strict.

Acceptance criteria:

- In the presence of stale mountpoints, tiering scripts either:
  - succeed after targeted repair (if implemented), or
  - fail fast with an actionable error message that points to the correct playbook step.

### Phase 3 — Worker + job lifecycle robustness (prevent “stuck running”; don’t burn retries on infra)

Objective: even if mounts break, the backend should not:

- leave jobs stuck as `running`,
- or permanently mark jobs `failed` and exhaust retry budgets due to infrastructure errors.

Deliverables:

- `run_persistent_job` is exception-safe: it always finalizes the job state in the DB.
- Errno 107 (and related storage IO errors) are classified as infrastructure faults:
  - jobs become `retryable` without incrementing `retry_count`,
  - and status/metrics make the infra nature visible.

#### 3.0 Define an error taxonomy and how it maps to existing DB fields (no schema change first)

We should avoid new tables/columns unless necessary (single-VPS, minimal migration risk).

Proposed mapping using existing columns:

- `ArchiveJob.status`:
  - use existing lifecycle statuses (`queued`, `running`, `completed`, `failed`, `retryable`, `indexing`, `indexed`, `index_failed`)
- `ArchiveJob.crawler_status` (string):
  - extend with a small enum-like set:
    - `success`
    - `failed` (crawl failure)
    - `infra_error` (storage/mount failure; includes Errno 107)
    - `infra_error_config` (missing env/docker/etc; optional)
- `ArchiveJob.crawler_exit_code`:
  - keep the archive_tool exit code when we actually ran it.
  - for infra exceptions before/during the run:
    - set to a conventional non-zero (or leave as `None`) but ensure worker logic keys off `crawler_status` rather than rc alone.

Retry budget policy:

- Only true crawl failures should increment `retry_count`.
- `infra_error` should not increment retry_count; recovery should be handled by:
  - the Phase 2 watchdog, and/or
  - manual operator repair.

#### 3.1 Make `run_persistent_job` exception-safe and infra-aware

Target: `src/ha_backend/jobs.py` (`run_persistent_job`)

Current failure mode:

- If any exception occurs while interacting with the output dir (e.g., `Path.mkdir`, `archive_tool` filesystem probes), the job can remain `running` forever because the finalization session is never reached.

Implementation plan:

- Wrap the entire “execute archive_tool” portion in `try/except/finally` such that:
  - job is marked `running` and committed before execution (already true),
  - **and** job is always finalized even if execution throws.

- In the exception handler:
  - detect storage/mount infra errors:
    - `OSError` with `errno == 107` is the primary signature.
    - also consider other IO-ish errnos (`EIO`, `ETIMEDOUT`) as “infra”.
  - set:
    - `job.status = "retryable"` (not `failed`)
    - `job.crawler_status = "infra_error"`
    - `job.crawler_exit_code` to a conventional value or `None`
    - `job.finished_at = now`
  - do not attempt log parsing if output dir is unreadable (avoid compounding errors).

- Ensure attempt fields are not misleading:
  - when starting a new attempt (status→running), clear:
    - `finished_at`
    - `crawler_exit_code`
    - `crawler_status`
    - `crawler_stage`
    - optionally `combined_log_path` (or keep it if it is meant to point to “most recent log we successfully parsed”).

Testing plan:

- Unit test that simulates Errno 107 during the run and asserts:
  - job ends as `retryable`,
  - `crawler_status == "infra_error"`,
  - job is not left `running`.

Acceptance criteria:

- The worker can no longer create “stuck running forever” jobs due to exceptions.

#### 3.2 Update worker retry semantics to honor infra classification

Target: `src/ha_backend/worker/main.py`

Current behavior:

- Any non-zero rc or `status == failed` increments `retry_count` up to `MAX_CRAWL_RETRIES`.

Implementation plan:

- After `run_persistent_job(job_id)` returns, reload the job and decide based on `crawler_status`:
  - if `crawler_status == "infra_error"`:
    - ensure `job.status` is `retryable` (if not already)
    - do **not** increment `retry_count`
    - log a structured message (“infra error; not consuming retry budget”)
  - else if crawl failure (`crawler_status == "failed"` or rc != 0):
    - keep current retry behavior (increment up to cap)

- Optional (best practice, but may require schema):
  - Introduce a backoff delay for infra errors so the worker doesn’t thrash when storage is broken.
  - If schema changes are undesirable, rely on Phase 2 watchdog to fix storage quickly and keep worker from repeatedly retrying.

Acceptance criteria:

- Mount failures do not exhaust `MAX_CRAWL_RETRIES`.

#### 3.3 Harden indexing against unreadable output dirs (avoid “silent skip” / confusing failures)

Incident relevance:

- The same Errno 107 class can break indexing if it happens between crawl completion and indexing.

Targets:

- `src/ha_backend/indexing/pipeline.py`:
  - `output_dir.is_dir()` can raise `OSError` (Errno 107).

Implementation plan:

- Wrap `output_dir` existence checks in try/except:
  - treat Errno 107 as infra (similar semantics to crawl infra errors).
- Ensure indexing failures leave a clear status:
  - set `job.status = "index_failed"` with a clear error message surfaced via logs/admin.
- (Optional) add a pre-index “output dir readable” gate:
  - if output dir unreadable, do not even attempt indexing; let the watchdog recover storage first.

Acceptance criteria:

- Indexing does not crash the worker loop on Errno 107, and it leaves a clear, recoverable state.

#### 3.4 Harden `archive_tool` path probes so storage errors don’t crash the whole process

Incident relevance:

- The incident included an `archive_tool` crash due to `Path.is_file()` on a combined log path raising Errno 107.

Targets:

- `src/archive_tool/main.py`:
  - guard any filesystem probes (`is_file`, `glob`, `stat`) used for “best effort” behaviors (like “parse temp dir from logs”).
- `src/archive_tool/utils.py`:
  - any helpers that assume `stat()` is always safe.

Implementation plan:

- Treat storage probe failures as non-fatal where possible:
  - “could not parse temp dir from logs” should fall back (it already does) without crashing.
  - add try/except around `stage_combined_log_path.is_file()` and any mtime-based sorts.
- Ensure `archive_tool` returns a non-zero exit code when it cannot safely write output/state due to storage issues, but does so without throwing an unhandled exception.
  - This supports Phase 3.1/3.2 logic: the worker can classify the failure via stderr/log signatures or via a dedicated exit code.

Testing plan:

- Unit tests that monkeypatch `Path.is_file()` / `Path.stat()` in the relevant code paths to raise `OSError(107, ...)` and assert:
  - no unhandled exception escapes,
  - the tool degrades gracefully (fallback scan path used),
  - and it exits with a clear failure mode if required.

Acceptance criteria:

- `archive_tool` can no longer crash from “best-effort filesystem probes” on stale mountpoints.

### Phase 4 — Data integrity + crawl completeness (post-incident correctness guarantees)

Objective: ensure that:

- WARC artifacts remain valid and replayable after a storage incident,
- indexing does not silently miss WARCs,
- and crawl completeness is preserved (resume behavior remains correct).

Deliverables:

- A reproducible verification tool that can be run:
  - manually after an incident,
  - and optionally automatically before indexing.
- A policy for handling corrupt outputs that preserves provenance (no silent deletion).

#### 4.0 Define integrity levels and what we can afford on a single VPS

Integrity checks are a tradeoff: full gzip + WARC iteration can be expensive on large WARCs.

Define three levels:

- Level 0 (cheap; always-on):
  - file exists, is readable, size > 0
- Level 1 (moderate; default for “post-incident window”):
  - gzip stream integrity (detect truncation/corruption)
- Level 2 (heavier; opt-in or sampled):
  - WARC parseability (iterate records; no need to materialize bodies)

Policy:

- After a storage incident, run at least Level 1 for WARCs created/modified within the incident window.
- Before indexing, run Level 0 always; Level 1 optionally (configurable) to prevent indexing corrupted WARCs.

#### 4.1 Implement WARC verification tooling (CLI + report)

Preferred entrypoint: backend CLI (so it can query DB + reuse discovery logic)

- Add `ha-backend verify-warcs --job-id <ID> [--level 0|1|2] [--json-out ...] [--apply-quarantine]`

Implementation details:

- Use `ha_backend.indexing.warc_discovery.discover_warcs_for_job(job)` for discovery:
  - it already prefers stable `<output_dir>/warcs/` when present.
- For Level 1 gzip checks:
  - use Python `gzip` module to stream-decompress and detect CRC/truncation.
  - support `--max-bytes` or “newest-only” modes to keep CPU bounded.
- For Level 2 WARC checks:
  - use `warcio` to iterate records and ensure headers parse without raising.
  - do not read full bodies (avoid memory blowups).
- Output:
  - print a human-readable summary,
  - write a JSON report artifact (for later debugging and audit),
  - optionally write a node_exporter textfile metric with last verification status for that job/source.

Acceptance criteria:

- The verifier can detect a truncated `.warc.gz` created during a simulated interruption (where feasible).

#### 4.2 Define and implement “quarantine” semantics (provenance-preserving)

Key constraint:

- Once snapshots are indexed, their `warc_path` must remain valid for replay. Moving WARCs breaks replay.

Policy design:

- Quarantine is safe only **before indexing** (job not yet `indexed`).
- After indexing:
  - treat corruption as a critical incident (it implies replay integrity loss); handle via a separate playbook.

Implementation plan (pre-index jobs):

- If verifier finds corrupt WARCs:
  - move them under a quarantine directory inside the job output dir (example):
    - `<output_dir>/warcs_quarantine/<timestamp>/...`
  - write a marker file:
    - `<output_dir>/WARCS_QUARANTINED.txt` with details and checksums
  - mark job as `retryable` (or `index_failed`) with an explicit error message surfaced via admin APIs.

Acceptance criteria:

- We never silently delete or overwrite suspect WARC files.

#### 4.3 Ensure crawl completeness and resumption remain correct after mount recovery

The incident showed `archive_tool` starting a “new crawl phase” while consolidating prior WARCs, and it did not find a resume YAML.

Work items:

- Investigate and document archive_tool resume behavior:
  - Where should resume config live?
  - Under what conditions is it written?
  - Why might it be missing even though `.archive_state.json` exists?
- Define the completeness contract:
  - when a job is retried after infra recovery, it should:
    - resume queued pages when possible, or
    - otherwise continue safely without losing previously captured WARCs.
- Decide whether to improve archive_tool to persist a stable resume config and reuse it automatically.
  - This is likely part of `src/archive_tool/` changes and must be tested carefully.

Acceptance criteria:

- A job that is interrupted by a storage incident can be retried and still produce a complete crawl (no silent loss of coverage due to lost queue state).

### Phase 5 — Test strategy + recovery drills (prove it works)

Objective: demonstrate correctness with tests where possible and safe drills where not.

#### 5.0 Unit tests (CI-safe; no FUSE required)

Add tests that simulate Errno 107 by mocking filesystem calls:

- Metrics scripts:
  - `scripts/vps-crawl-metrics-textfile.py` does not crash and emits expected metrics.
- Job runner:
  - `run_persistent_job` finalizes jobs correctly on Errno 107.
- (Optional) archive_tool:
  - ensure “probe combined log path” does not crash the entire run when `stat()` fails; it should degrade gracefully and mark the stage appropriately.

#### 5.1 “Dry-run drills” on the VPS (safe even on production)

Design drills that do not require breaking mounts on production:

- Run watchdogs in dry-run mode and confirm they would take the correct actions when given a known-bad test path.
  - (This may require adding a `--simulate-broken-path` flag; keep it restricted and test-only.)
- Validate that metrics + alerts remain wired:
  - force metrics scripts to emit `*_ok=0` via a controlled test input and confirm alert firing is correct (use a non-production alert route if possible).

#### 5.2 Full recovery drill (only on non-production or explicitly scheduled maintenance)

If/when a staging VPS exists (or during a scheduled maintenance window):

- intentionally break an sshfs mount (or simulate an Errno 107 hot path),
- confirm:
  - alert triggers,
  - watchdog recovers,
  - worker resumes,
  - verifier passes or quarantines correctly.

## Rollout strategy (production-safe)

This section is intentionally “ops-grade” and explicit.

General deployment constraints (single-VPS):

- Keep deploys small and reversible.
- Prefer “ship inert code + observe” before enabling automation.
- Avoid changes that require large DB migrations during the annual crawl window.

### Phase-by-phase deploy order (recommended)

1) **Phase 0 docs** (no runtime impact)
   - Merge docs changes at any time.

2) **Phase 1 observability** (safe to ship mid-crawl)
   - Deploy metrics robustness first so we can see failures clearly before adding automation.

3) **Phase 2 watchdog automation** (ship disabled; enable later)
   - Deploy the watchdog code + systemd units, but do **not** create the sentinel file yet.
   - Observe dry-run behavior first.
   - Only enable in production after Phase 1 alerts/metrics are stable and you’ve verified the watchdog will not thrash.

4) **Phase 3 worker/job semantics** (ship after crawl stability)
   - These changes affect job state transitions; schedule deliberately.

5) **Phase 4 integrity tooling** (ship detection first, then quarantine)
   - Start with reporting-only verification.
   - Only enable “apply/quarantine” once you have confidence in the semantics and have an operator playbook for edge cases.

### VPS rollout checklist (for each phase that changes code/units)

Pre-deploy (local):

- Run the closest local checks:
  - `make check`
- Ensure docs remain canonical (no duplicated runbooks).

Deploy (VPS):

- Update the backend checkout under `/opt/healtharchive-backend` to the new revision.
- Update/refresh the venv if dependencies changed (ideally avoid dependency changes in these phases).
- If systemd units changed:
  - copy templates into `/etc/systemd/system/` (per `docs/deployment/systemd/README.md`)
  - run `sudo systemctl daemon-reload`

Post-deploy validation (always do):

- Metrics scripts:
  - `sudo systemctl start healtharchive-crawl-metrics.service`
  - `sudo systemctl start healtharchive-tiering-metrics.service`
  - confirm both exit with success and update node_exporter textfile outputs
- Alerts wiring:
  - confirm Prometheus picked up updated rules (reload/restart per observability playbook)
- Worker safety:
  - confirm `healtharchive-worker.service` is still active (do not restart it unless the phase requires it)

### Enabling Phase 2 automation (explicit opt-in procedure)

Do not enable automation until:

- Phase 1 alerts are working (you have a clear signal of hot-path unreadability).
- The watchdog has been run in dry-run mode and its printed plan is correct.

Enable steps (production):

1) Create the sentinel:
   - `sudo touch /etc/healtharchive/storage-hotpath-auto-recover-enabled`
2) Enable + start the timer:
   - `sudo systemctl enable --now healtharchive-storage-hotpath-auto-recover.timer`
3) Monitor for at least 30 minutes:
   - ensure it does not repeatedly stop/start the worker,
   - ensure its state file is being written and recovery caps behave.

Rollback steps (production):

1) Disable the timer:
   - `sudo systemctl disable --now healtharchive-storage-hotpath-auto-recover.timer`
2) Remove the sentinel:
   - `sudo rm -f /etc/healtharchive/storage-hotpath-auto-recover-enabled`
3) If the watchdog stopped the worker, restart it explicitly:
   - `sudo systemctl start healtharchive-worker.service`

### Healthchecks parity (when adding new timers)

If Phase 2 introduces a new timer:

- Update `docs/operations/playbooks/healthchecks-parity.md` and the Healthchecks UI (operator action) so failures are visible even if Prometheus is down.

## Risks and mitigations

**Risk:** `umount -l` can hide “still in use” mounts and has sharp edges.

- **Mitigation:** only use it for known-stale hot paths that are already failing `stat()`, log every action, rate-limit automation, and prefer targeted unmounts over broad restarts.

**Risk:** Monitoring and alerting becomes noisy (false positives from transient IO blips).

- **Mitigation:** use `for:` windows (hot-path unreadable: 2 minutes; metrics missing: 5 minutes) and enforce `min_failure_age_seconds=120` in watchdog detection.

**Risk:** Metric label cardinality grows over time (paths include timestamps).

- **Mitigation:** prefer `job_id` + `source` labels for per-job metrics; keep path-labeled metrics limited to small static manifests.

**Risk:** Aggressive auto-recovery could interrupt an otherwise-recovering crawl.

- **Mitigation:** require a clear Errno 107 signature; only act when the hot paths are currently unreadable; avoid stopping the worker unless necessary; rate-limit.

**Risk:** Data corruption may be subtle (partial warc writes that still gzip-validate).

- **Mitigation:** do more than gzip checks when feasible; implement basic WARC parse checks; compare discovered WARCs vs indexed coverage.

**Risk:** Quarantining WARCs could break replay if done after indexing.

- **Mitigation:** quarantine only for pre-index jobs; treat post-index corruption as a critical incident with a dedicated playbook.

## Open questions to resolve during implementation

1) Are per-job output directories mounted via `sshfs` directly, or via bind mounts from `/srv/healtharchive/storagebox`?
   - `mount` output during the incident showed `fuse.sshfs` at the hot paths; we should confirm the intended architecture in code/scripts.
2) Should we consolidate to a single `sshfs` mount (storagebox root) + local bind mounts for job outputs to reduce failure surface?
3) Should Errno 107 trigger an immediate “stop all crawling” posture, or can we safely isolate per-job repair while others continue?
4) What is the minimal integrity check that catches the majority of real corruption without being expensive on the VPS?
5) Should we tune `sshfs` options (e.g., `kernel_cache`, reconnect/keepalive) to reduce the chance of stale mountpoints, and what is the replay/crawl performance impact?
6) Do we want a dedicated “staging” VPS (even temporary) solely to run full recovery drills without risking the annual crawl?
