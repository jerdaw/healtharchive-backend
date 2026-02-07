# 2026-02-06: Crawl Operability - Locks, Writability, and Retry Controls

**Plan Version**: v1.5
**Status**: Implemented in Repo (Phases 1-4 complete; Phase 4 requires operator execution on VPS during a safe window)
**Scope**: Improve crawl operability and safety around job locking, output-dir health visibility, and retry budget recovery UX.
**Batched items**: #7, #8, #9

## Implementation Progress

- **Phase 1**: Implemented in repository (migration-ready lock directory behavior).
  - Stop forcing `1777` permissions for non-`/tmp` lock dirs (preserves group/setgid semantics):
    - `src/ha_backend/jobs.py`
    - `tests/test_jobs_persistent.py`
  - Bootstrapped a dedicated ops lock directory path:
    - `/srv/healtharchive/ops/locks/jobs` via `scripts/vps-bootstrap-ops-dirs.sh`
  - Added operator guidance for production lock-dir cutover:
    - `docs/deployment/systemd/README.md`

- **Phase 2**: Implemented in repository (annual queued/retryable output-dir writability probes).
  - Added per-annual-job output-dir writability metrics (worker-user permission drift detection):
    - `scripts/vps-crawl-metrics-textfile.py`
    - `tests/test_ops_metrics_textfile_scripts.py`
  - Added an alert for sustained non-writable annual output dirs:
    - `ops/observability/alerting/healtharchive-alerts.yml` (`HealthArchiveAnnualOutputDirNotWritable`)
    - `tests/test_ops_alert_rules.py`
  - Documented the metric + alert:
    - `docs/operations/monitoring-and-alerting.md`
    - `docs/operations/thresholds-and-tuning.md`

- **Phase 3**: Implemented in repository (operator retry-budget reset CLI).
  - Added an operator-safe command to reset `retry_count` (dry-run by default, `--apply` required):
    - `src/ha_backend/cli.py` (`reset-retry-count`)
    - `tests/test_cli_reset_retry_count.py`
  - Documented command usage:
    - `docs/reference/cli-commands.md`

- **Phase 4**: Implemented in repository (operator cutover helper + checklist).
  - Added VPS helper to print an idempotent cutover plan and rollback steps:
    - `scripts/vps-job-lock-dir-cutover.sh`
  - Linked in systemd deployment docs:
    - `docs/deployment/systemd/README.md`

All phases (1-4) are implemented in-repo; production cutover remains an operator-run task.

## Current State Summary

The backend already has resilient job execution and infra-error classification (`src/ha_backend/jobs.py`), crawl metrics (`scripts/vps-crawl-metrics-textfile.py`), and stale-job recovery CLI (`ha-backend recover-stale-jobs`). However, three operability gaps remain:

1. Job locks currently live under `/tmp/healtharchive-job-locks` by default, which contributed to cross-user permission edge cases during incident response.
2. Output-dir readability is monitored for running jobs, but proactive visibility for queued/retryable annual jobs is limited.
3. Operators currently rely on ad hoc DB snippets for retry-count reset in edge cases; there is no dedicated audited CLI command.

Relevant current assets:

- Job lock implementation:
  - `src/ha_backend/jobs.py` (`_job_lock`, `DEFAULT_JOB_LOCK_DIR`)
  - `scripts/vps-crawl-auto-recover.py` (`DEFAULT_JOB_LOCK_DIR`, lock probes)
- Crawl metrics exporter:
  - `scripts/vps-crawl-metrics-textfile.py`
- Existing CLI flows:
  - `src/ha_backend/cli.py` (`retry-job`, `recover-stale-jobs`)
- Existing tests:
  - `tests/test_jobs_persistent.py`
  - `tests/test_cli_recover_stale_jobs.py`
  - `tests/test_ops_metrics_textfile_scripts.py`

### Key Unknowns

- Target lock directory choice for production (`/run`, `/srv/healtharchive/ops`, or another dedicated path).
- Whether dual-read lock probing is required during migration to avoid transient blind spots.
- Desired policy boundaries for resetting retry counts (allowed statuses, required reason field, audit detail level).

### Assumptions

- Service restarts to pick up lock-dir env updates can be scheduled during a safe window.
- Additional metrics and alerts can be added without overloading signal quality.
- A dedicated retry-reset CLI can remain operator-only and intentionally explicit (`--apply`).

## Goals

- Remove lock-file fragility caused by `/tmp` semantics and cross-user edge cases.
- Surface output-dir writability risks before jobs consume retries.
- Provide a safe, auditable retry-budget reset path that avoids manual DB mutation.

## Non-Goals

- Reworking entire job scheduler semantics.
- Changing archive_tool crawl logic.
- Fully automating all retry policy decisions.

## Constraints

- Must not interrupt active crawl during implementation.
- Migration path must avoid lock-state ambiguity while workers/watchdogs are live.
- New alerts must be low-noise and scoped to actionable cases.

## Phased Implementation Plan

### Phase 0: Design and Migration Strategy

**Goal**: Finalize lock-dir migration and CLI policy contract before code changes.

**Tasks**:

1. Select production lock-dir target and permissions model.
2. Define transition strategy:
   - dual-read lock detection vs single-cutover,
   - environment variable rollout sequence,
   - restart order for worker and watchdog services.
3. Define `reset-retry-count` command policy:
   - required flags,
   - supported statuses,
   - audit output format.

**Deliverables**:

- Approved lock migration design.
- Approved CLI contract for retry reset.

**Validation**:

- Maintainer sign-off on migration sequence and failure handling.

### Phase 1: Lock Directory Migration (Safe and Backward Compatible)

**Goal**: Move lock operations away from `/tmp` with minimal runtime risk.

**Tasks**:

1. Update lock-dir resolution in backend and watchdog scripts to support controlled migration.
2. Introduce backward-compatible probing during migration window (if selected in Phase 0).
3. Update deployment/systemd docs for lock-dir environment variable configuration.
4. Add/adjust tests covering:
   - lock acquisition in new directory,
   - lock detection by watchdog under mixed/legacy conditions.

**Deliverables**:

- Lock-dir migration-capable code.
- Updated tests and operator docs.

**Validation**:

- Existing lock-related tests pass.
- New migration tests pass in CI.
- Dry-run verification confirms runner detection still works.

### Phase 2: Output-Dir Writability Probes for Queued/Retryable Annual Jobs

**Goal**: Detect non-writable annual job output dirs before crawl attempts consume retries.

**Tasks**:

1. Extend crawl metrics exporter to probe queued/retryable annual job output dirs (bounded cardinality).
2. Emit metrics for probe status and errno with source/job labels.
3. Add alert rule(s) for sustained non-writable annual output dirs.
4. Add/extend tests in metrics and alert suites.

**Deliverables**:

- New proactive writability metrics.
- New alert rule and docs updates in:
  - `docs/operations/monitoring-and-alerting.md`
  - `docs/operations/thresholds-and-tuning.md`

**Validation**:

- Script tests pass with simulated permission and Errno 107 failures.
- Alert rule parses and is covered by test(s).
- Drill run shows expected metric output without touching running crawl.

### Phase 3: Add `reset-retry-count` Operator CLI

**Goal**: Replace ad hoc DB snippets with a safe, explicit admin command.

**Tasks**:

1. Add new CLI command in `src/ha_backend/cli.py` (dry-run default, explicit `--apply`).
2. Enforce policy guardrails:
   - disallow running jobs,
   - optional source/status filters,
   - optional reason note for auditability.
3. Add tests for happy path and refusal paths.
4. Document command usage in CLI reference and relevant playbooks/incidents.

**Deliverables**:

- `ha-backend reset-retry-count` command.
- Tests and documentation.

**Validation**:

- CLI tests pass for dry-run/apply and invalid-state behavior.
- Command output provides clear audit trail for before/after counts.

### Phase 4: Controlled Rollout and Cutover

**Goal**: Transition production safely with no crawl disruption.

**Operator checklist (VPS)**:

This phase has two parts:

1. **Non-disruptive staging** (safe even while crawls are running):
   - back up `/etc/healtharchive/backend.env`
   - set `HEALTHARCHIVE_JOB_LOCK_DIR`
   - ensure `/srv/healtharchive/ops/locks/jobs` exists with correct perms
2. **Disruptive cutover** (maintenance window only):
   - restart services that read `backend.env` (worker, API, and any watchdog units that use lock probes)

**Hard requirement**: do not restart the worker while a crawl you care about is running. Wait until:

- `/opt/healtharchive-backend/.venv/bin/ha-backend list-jobs --status running --limit 5` shows **no running jobs**, or
- you have explicitly decided it is OK to interrupt crawls.

Important note: if your VPS checkout at `/opt/healtharchive-backend` is behind the repo, it may not include the helper script
`scripts/vps-job-lock-dir-cutover.sh` or the latest `scripts/vps-bootstrap-ops-dirs.sh` that creates the lock dir. In that case,
either deploy/pull first, or follow the manual commands below.

**Non-disruptive staging (VPS)**:

```bash
sudo cp -av /etc/healtharchive/backend.env /etc/healtharchive/backend.env.bak.$(date -u +%Y%m%dT%H%M%SZ)

sudo rg -n '^export HEALTHARCHIVE_JOB_LOCK_DIR=' /etc/healtharchive/backend.env >/dev/null \
  && sudo sed -i 's|^export HEALTHARCHIVE_JOB_LOCK_DIR=.*$|export HEALTHARCHIVE_JOB_LOCK_DIR=/srv/healtharchive/ops/locks/jobs|g' /etc/healtharchive/backend.env \
  || echo 'export HEALTHARCHIVE_JOB_LOCK_DIR=/srv/healtharchive/ops/locks/jobs' | sudo tee -a /etc/healtharchive/backend.env >/dev/null

rg -n '^export HEALTHARCHIVE_JOB_LOCK_DIR=' /etc/healtharchive/backend.env | tail -n 2

# If the lock dir does not exist yet:
sudo install -d -m 2770 -o root -g healtharchive /srv/healtharchive/ops/locks
sudo install -d -m 2770 -o root -g healtharchive /srv/healtharchive/ops/locks/jobs
ls -ld /srv/healtharchive/ops/locks /srv/healtharchive/ops/locks/jobs
```

**Maintenance-window cutover (VPS)**:

```bash
set -a; source /etc/healtharchive/backend.env; set +a
/opt/healtharchive-backend/.venv/bin/ha-backend list-jobs --status running --limit 5

# Only proceed if you are OK restarting services (recommended: no running jobs).
sudo systemctl restart healtharchive-worker.service
sudo systemctl restart healtharchive-api.service
sudo systemctl is-active healtharchive-worker.service healtharchive-api.service
curl -fsS http://127.0.0.1:8001/api/health >/dev/null && echo OK
```

**Rollback (VPS)**:

```bash
sudo ls -1 /etc/healtharchive/backend.env.bak.* | tail -n 1
sudo cp -av "$(sudo ls -1 /etc/healtharchive/backend.env.bak.* | tail -n 1)" /etc/healtharchive/backend.env
sudo systemctl restart healtharchive-worker.service
sudo systemctl restart healtharchive-api.service
```

**Tasks**:

1. Deploy code with migration-safe lock handling.
2. During maintenance window:
   - set lock-dir env in service environment,
   - restart affected services in safe order,
   - verify lock probes and watchdog visibility.
3. Enable new writability alert after confirming metric quality.
4. Publish operator-facing migration note.

**Deliverables**:

- Production lock-dir cutover complete.
- Writability metrics and alerts active.
- Retry-reset CLI available for operators.

**Validation**:

- Post-cutover status checks show no false runner detection.
- New metrics appear and alert remains quiet under healthy conditions.
- Operator can execute retry reset without manual DB access.

## Dependencies

- Access to update systemd environment config on VPS.
- Alert rule deployment process.
- Maintainer/operator alignment on retry-reset governance.

## Risks and Mitigations

- Risk: Lock-dir cutover causes temporary lock visibility mismatch.
  - Mitigation: migration-safe dual-read/probe strategy and controlled restart order.
- Risk: Writability probes create noisy alerts.
  - Mitigation: scope to annual queued/retryable jobs and add sustained-duration thresholds.
- Risk: Retry-reset command is overused.
  - Mitigation: explicit `--apply`, state guardrails, and required audit output.

## Progress Validation Framework

- Phase complete only when code/docs are merged and verification artifacts exist (tests, dry-run outputs, or operator checklist completion).
- Production cutover complete only when lock detection and crawl safety checks pass in post-deploy verification.

## Timeline and Milestones

Expected timeline (single maintainer + one maintenance window):

- Milestone A (Days 1-2): Phase 0 complete (design and policy decisions finalized).
- Milestone B (Days 3-6): Phase 1 complete (lock migration code + tests).
- Milestone C (Days 7-9): Phase 2 complete (writability metrics/alerts + docs).
- Milestone D (Days 10-11): Phase 3 complete (retry-reset CLI + docs/tests).
- Milestone E (Day 12+maintenance window): Phase 4 rollout complete.

## Rollout Approach

1. Merge and validate code in CI.
2. Deploy migration-safe lock handling first.
3. Perform lock-dir env cutover in maintenance window.
4. Enable/observe new metrics and alerts.
5. Announce and document new retry-reset operational flow.

## Rollback Approach

- Lock migration rollback:
  - revert env var to legacy lock dir,
  - restart services,
  - keep migration code while investigating.
- Metrics/alert rollback:
  - disable new alert rule and keep metrics for diagnostics.
- CLI rollback:
  - remove command if policy misuse is observed, fallback to existing controlled runbook.

Rollback steps are operational and do not require schema changes.

## Exit Criteria

- Incident follow-up for lock-directory migration is closed.
- Proactive output-dir writability monitoring exists for annual queued/retryable jobs.
- Operators no longer require manual DB snippets to reset retry budget.
- No observed regression in lock-based stale-job safety checks across one campaign cycle.

## Related Sources

- `docs/operations/incidents/2026-02-06-auto-recover-stall-detection-bugs.md`
- `docs/operations/incidents/2026-01-09-annual-crawl-phac-output-dir-permission-denied.md`
- `src/ha_backend/jobs.py`
- `scripts/vps-crawl-auto-recover.py`
- `scripts/vps-crawl-metrics-textfile.py`
- `src/ha_backend/cli.py`
