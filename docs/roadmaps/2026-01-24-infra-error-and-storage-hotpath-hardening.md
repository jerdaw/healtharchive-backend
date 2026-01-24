# Infra-error retry storms + Storage Box hot-path resilience (active plan) — 2026-01-24

Status: **active plan** (created 2026-01-24)

This plan targets a specific failure mode observed during the 2026 annual campaign on the
single-VPS deployment: a Storage Box / `sshfs`-backed mount (or bind mount onto it) became
stale and started raising:

- `OSError: [Errno 107] Transport endpoint is not connected`

The immediate impact was a **retry storm** (worker tight-looping on a `retryable` job that
always fails instantly) and an operator-visible “everything stopped” state when the worker
ended up stopped and stayed down.

This plan focuses on two outcomes:

1) **Prevention / mitigation** (make the system harder to wedge and reduce alert storms).
2) **Automated safe recovery** (repair stale hot paths and resume work without manual ops).

This plan is intentionally detailed so it can be implemented incrementally and verified
with high confidence.

---

## Background (what happened)

Observed behavior (condensed):

- A job output directory under `/srv/healtharchive/jobs/**` became unreadable (Errno 107).
- The worker repeatedly selected the same `retryable` job and immediately failed it as
  `crawler_status=infra_error` without consuming retry budget.
- This created high log volume and lots of notifications.
- Separately, the storage hot-path recovery automation ran but did not fully repair all
  relevant mountpoints, and the worker remained down until manually started.

Important nuance:

- `findmnt` can still show a mountpoint even when it is stale; always probe with a bounded
  `ls`/`stat` to detect “mounted but unreadable”.

Related historical context:

- Storage Box stale-mount recovery baseline plan (implemented): `roadmaps/implemented/2026-01-08-storagebox-sshfs-stale-mount-recovery-and-integrity.md`

This plan closes gaps revealed by the 2026-01-24 incident (notably: retry storms and stale
hot paths affecting *queued/retryable* jobs, not just `status=running` jobs).

---

## Goals

### G1) Stop alert storms from infra errors

If a job fails due to infrastructure (`crawler_status=infra_error`), do not let the worker
immediately re-run it in a tight loop.

### G2) Recover automatically when the hot path is stale

If a hot path is stale (Errno 107), automatically:

- quiesce only when necessary,
- unmount the exact stale mountpoint(s),
- restart the base Storage Box mount if required,
- re-apply tiering/bind mounts,
- and resume worker progress.

### G3) Preserve correctness (completeness + integrity)

- Do not “fix” infra issues by deleting data.
- Avoid actions that can corrupt in-flight outputs (prefer quiesce before remounting).
- Ensure recovery is bounded and idempotent.

---

## Non-goals

- Eliminating all Storage Box / network failures (we can only mitigate).
- Adding new infrastructure (multi-VPS, object storage, etc.) in this plan.
- Making crawls “fast” by adding page/depth caps (policy: completeness-first).

---

## Constraints and invariants (single VPS discipline)

- Worker is a scarce resource; avoid flapping it.
- Automation must be:
  - **idempotent**
  - **bounded / rate-limited**
  - **safe-by-default**
  - **gated by sentinel files**
  - **observable** (metrics + logs)
  - **instantly disable-able**

---

## Workstreams

### A) Worker resilience (prevent tight retry loops)

#### A1) Add a cooldown for recent infra_error jobs

Status: **implemented in code** and merged to `main`.

Change:

- The worker skips jobs whose latest `crawler_status == "infra_error"` for a cooldown window
  (currently 10 minutes).

Why:

- Prevents “pick → fail → pick → fail …” storms when the underlying issue is not job-specific.

Files:

- `src/ha_backend/worker/main.py`
- `tests/test_worker.py`

Deployment note:

- The cooldown only takes effect after `healtharchive-worker.service` restarts (safe boundary:
  no active `status=running` job).

Acceptance criteria:

- A single infra_error job cannot generate high-rate logs/alerts.

#### A2) (Optional) Persist next-attempt time in DB

Status: **not implemented** (candidate improvement).

Current implementation uses a query-time filter on `updated_at`. If we later want explicit
auditability, add a field in `ArchiveJob.config` (no schema change) like:

- `infra_error_next_attempt_utc`

Then the worker and/or watchdog can share the same semantics explicitly.

Decision point:

- Only implement if we need better forensic visibility than “updated_at cutoff”.

---

### B) Storage hot-path auto-recovery hardening

We already have `healtharchive-storage-hotpath-auto-recover.timer` + script:

- `scripts/vps-storage-hotpath-auto-recover.py`

The incident showed two gaps:

1) Recovery focused on **running job hot paths** + a manifest list, but a stale hot path for a
   **retryable** job can still wedge the worker.
2) Recovery could fail downstream (e.g. annual output tiering fails on stale mountpoints not in
   the initial probe set), leaving the system “stopped”.

#### B1) Detect stale mountpoints for “next jobs” (queued/retryable), not only running jobs

Status: **not implemented** (required).

Add to `vps-storage-hotpath-auto-recover.py`:

- Query the DB for the next N jobs by the same ordering the worker uses:
  - `status IN (queued, retryable)`
  - order by `queued_at` then `created_at`
  - limit (suggest: 5–10)
- Probe `output_dir` readability for those jobs and treat Errno 107 as an eligible stale target.

Safety rules:

- Only unmount if:
  - the path is under `jobs_root` (e.g. `/srv/healtharchive/jobs`)
  - it is an **exact mountpoint target** (do not unmount parent mounts)
  - the probe returns Errno 107

Acceptance criteria:

- A stale mountpoint for a retryable job is repaired *before* the worker repeatedly selects it.

Tests:

- Unit test covering that queued/retryable job output dirs are included in detection and that
  only exact mountpoint targets are eligible.

#### B2) Make annual output tiering repairable during recovery runs

Status: **not implemented** (required).

Problem:

- `scripts/vps-annual-output-tiering.py --apply` refuses to proceed when it finds a stale
  mountpoint unless `--repair-stale-mounts` is provided.

Fix:

- In `vps-storage-hotpath-auto-recover.py`, when running in apply mode and the worker is already
  quiesced (or there are no running jobs), call:
  - `vps-annual-output-tiering.py --apply --repair-stale-mounts`

Safety gate:

- Only run `--repair-stale-mounts` when we are certain it will not interrupt an in-flight crawl:
  - either worker is stopped by the script *because the running job hot path is stale*, or
  - there are no running jobs to begin with.

Acceptance criteria:

- A recovery run can repair stale annual job output mounts without failing early.

Tests:

- Unit test that the recovery plan selects the “repair” invocation only when the quiesce gate is met.

#### B3) Don’t stop the worker for “cold-only” issues when a crawl is running and healthy

Status: **not implemented** (required).

Refine quiesce logic:

- If there is a running job and its hot path is healthy, do not stop the worker just to repair
  a stale mountpoint for a different (non-running) job.

Rationale:

- Stopping the worker terminates the in-flight crawl container; that risks completeness and wastes time.

Acceptance criteria:

- If `hc` crawl is running and healthy, recovery can still fix a stale `phac` mountpoint without
  stopping the `hc` crawl.

#### B4) Post-failure safety: ensure “worker stopped” cannot become a long-lived state

Status: **not implemented** (required).

Two complementary changes:

1) Improve the storage hot-path script so it restarts the worker **when safe** even if some
   secondary steps fail (e.g., tiering metrics write), as long as hot paths are healthy.
2) Add a separate “worker resurrection” timer (Workstream C) so a single failed recovery run
   does not strand the system.

Acceptance criteria:

- A single transient failure cannot keep the worker down for hours.

---

### C) Worker “resurrection” watchdog (safe auto-start)

Status: **not implemented** (required).

Add a lightweight, production-only automation that ensures the worker is running when it should be.

Proposed implementation:

- New script: `scripts/vps-worker-auto-start.py`
- New systemd service + timer:
  - `healtharchive-worker-auto-start.service`
  - `healtharchive-worker-auto-start.timer` (suggest: every 2–5 minutes)
- Gated by sentinel file:
  - `/etc/healtharchive/worker-auto-start-enabled`

Refusal rules (must be conservative):

- If deploy lock exists and is not stale → refuse.
- If Storage Box mount is not readable → refuse.
- If there is any `status=running` job but worker is inactive → do **not** auto-start
  (prefer manual investigation; this indicates a possibly mid-flight/partial state).
- If there are no `queued`/`retryable` jobs → do nothing.

Action:

- If worker is inactive AND there are queued/retryable jobs AND storage probes pass → start worker.

Observability:

- Write a small textfile metric:
  - last auto-start attempt timestamp
  - last result (ok/fail/skip + reason)

Acceptance criteria:

- After a storage incident is repaired, the system resumes without a human starting the worker.

Tests:

- Unit tests for refusal rules and “start decision” logic (no integration tests).

---

### D) Alerting + metrics (reduce noisy notifications, increase signal)

Status: **partially implemented** (existing metrics), improvements required.

Add/adjust alerting so:

- We page on “worker down while jobs pending” (high-signal), not on “many identical infra_error logs”.
- We page on “storage hot-path unrecovered for > X minutes” (bounded).

Suggested metrics additions (textfile-based, DB-derived):

- `healtharchive_jobs_infra_error_recent_total{minutes="10"}` (count)
- `healtharchive_worker_should_be_running` (boolean: jobs pending + storage ok)

Acceptance criteria:

- One incident produces one actionable alert, not a flood.

---

### E) Documentation + operator UX

Status: **partially implemented**.

Required updates when workstreams B/C/D ship:

- Update playbook:
  - `docs/operations/playbooks/storagebox-sshfs-stale-mount-recovery.md`
  - include “cooldown semantics” and “worker-auto-start semantics”
- Update systemd docs:
  - `docs/deployment/systemd/README.md`
  - add the new worker-auto-start unit(s) and enablement guidance.

---

## Rollout plan (safe sequencing)

1) Land code changes behind safe defaults (no automation enablement changes yet).
2) Add tests for all new logic and keep them unit-level and fast.
3) Deploy backend code to the VPS with `vps-deploy.sh --skip-restart` (crawl-safe).
4) At a safe boundary (no `status=running` job), restart the worker to pick up:
   - worker infra_error cooldown (already merged)
   - any new worker/automation logic (once implemented)
5) Run automation verification rituals:
   - `scripts/verify_ops_automation.sh` (and optionally `--json`)
6) Enable new timers only after dry-runs look correct and caps are in place:
   - create sentinel files under `/etc/healtharchive/*enabled`

---

## Definition of done (this plan)

- [x] Worker has an infra_error cooldown (prevents tight retry loops).
- [ ] Storage hot-path recovery detects stale mountpoints for “next jobs” (queued/retryable).
- [ ] Storage hot-path recovery can repair annual output tiering stale mounts (`--repair-stale-mounts`) safely.
- [ ] Worker is not stopped to fix unrelated cold paths when a crawl is healthy.
- [ ] Worker can be auto-started safely when it is down and jobs are pending (sentinel-gated).
- [ ] High-signal alerts exist for “worker down while jobs pending” and “hot path stale too long”.
- [ ] Playbooks/systemd docs updated to reflect the new behavior.

---

## References

- Storage Box stale mount recovery baseline (implemented): `roadmaps/implemented/2026-01-08-storagebox-sshfs-stale-mount-recovery-and-integrity.md`
- Annual crawl resiliency hardening (implemented): `roadmaps/implemented/2026-01-19-annual-crawl-resiliency-hardening.md`
- Ops automation plan (active): `operations/automation-implementation-plan.md`
