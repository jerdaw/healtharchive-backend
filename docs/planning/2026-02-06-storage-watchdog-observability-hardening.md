# 2026-02-06: Storage Watchdog Observability Hardening

**Plan Version**: v1.6
**Status**: Implemented in Repo (Phases 0-4 implemented; burn-in observation requires operator execution on VPS)
**Scope**: Close remaining reliability and observability gaps in storage hot-path auto-recover.
**Batched items**: #3, #4, #5

## Implementation Progress

- **Phase 0**: Implemented in-plan (failure-mode and alert semantics frozen).
  - Defined watchdog terminal-state taxonomy (below).
  - Finalized startup-safe alert expression proposal for persistent failed apply state:
    - `healtharchive_storage_hotpath_auto_recover_enabled == 1`
    - `healtharchive_storage_hotpath_auto_recover_apply_total > 0`
    - `healtharchive_storage_hotpath_auto_recover_last_apply_ok == 0`
    - `(time() - healtharchive_storage_hotpath_auto_recover_last_apply_timestamp_seconds) > 86400`
  - Initial severity set to `warning` with `for: 30m` (escalate to `critical` only after burn-in if signal quality is clean).
- **Phase 1**: Implemented in repository (integration-like stale mount scenarios).
  - Added explicit regression tests for missing mount-info Errno 107 recovery behavior in running-job and next-job paths:
    - `tests/test_ops_storage_hotpath_auto_recover.py`
  - Added parity test ensuring dry-run planned actions match apply intent for same stale target:
    - `tests/test_ops_storage_hotpath_auto_recover.py`
  - Added post-check failure test ensuring unreadable mounts force `last_apply_ok=0` and non-zero apply return:
    - `tests/test_ops_storage_hotpath_auto_recover.py`
- **Phase 2**: Implemented in repository (persistent failed-apply alerting).
  - Added alert rule for long-lived failed apply state:
    - `ops/observability/alerting/healtharchive-alerts.yml` (`HealthArchiveStorageHotpathApplyFailedPersistent`)
  - Added alert semantics test coverage:
    - `tests/test_ops_alert_rules.py`
  - Updated monitoring and threshold guidance:
    - `docs/operations/monitoring-and-alerting.md`
    - `docs/operations/thresholds-and-tuning.md`
- **Phase 3**: Implemented in repository docs (failure-mode matrix + drills + maintenance cadence).
  - Added watchdog failure-mode matrix and persistent failed-apply operator path:
    - `docs/operations/playbooks/storage/storagebox-sshfs-stale-mount-recovery.md`
  - Added explicit safe drill for persistent failed-apply alert condition:
    - `docs/operations/playbooks/storage/storagebox-sshfs-stale-mount-drills.md`
  - Added periodic watchdog verification cadence linkage:
    - `docs/operations/playbooks/validation/automation-maintenance.md`
- **Phase 4**: Implemented in repository (rollout/burn-in tooling + procedure).
  - Added VPS burn-in summary helper:
    - `scripts/vps-storage-watchdog-burnin-report.py`
    - `tests/test_ops_storage_watchdog_burnin_report.py`
  - Added explicit rollout-week evidence capture procedure:
    - `docs/operations/playbooks/storage/storagebox-sshfs-stale-mount-drills.md` (Section 5)
  - Linked burn-in helper in automation maintenance cadence:
    - `docs/operations/playbooks/validation/automation-maintenance.md`
  - Note: recent recoveries in the burn-in window are informational (do not fail the clean gate).
  - Optional scheduling: systemd timer for daily burn-in snapshots:
    - `docs/deployment/systemd/healtharchive-storage-watchdog-burnin-snapshot.service`
    - `docs/deployment/systemd/healtharchive-storage-watchdog-burnin-snapshot.timer`
    - `scripts/vps-storage-watchdog-burnin-snapshot.sh`
  - Remaining operator step: run one-week VPS burn-in capture and confirm clean end-of-week gate.

## Phase 0 Decisions (Frozen)

### Watchdog terminal-state taxonomy

1. **Apply success**:
   - `--apply` path executes and post-check confirms stale targets were restored/readable.
   - Expected state/metrics: `last_apply_ok=1`, apply counter increments.
2. **Apply attempted, post-check failed**:
   - `--apply` path runs but stale target remains unreadable or mountpoint not restored.
   - Expected state/metrics: `last_apply_ok=0`, non-zero script exit.
3. **Deploy-lock suppressed apply (safe dry-run)**:
   - `--apply` requested but deploy lock is active; script downgrades to dry-run planning only.
   - Expected state/metrics: detections still visible; no apply-side mutation.
4. **Detection not yet eligible**:
   - Stale target detected but confirm-runs/min-age/rate-limit gates prevent action.
   - Expected state/metrics: detections visible, no apply attempt.

### Alert semantics for persistent failed apply

Implemented PromQL expression (Phase 2):

```promql
healtharchive_storage_hotpath_auto_recover_enabled == 1
and healtharchive_storage_hotpath_auto_recover_apply_total > 0
and healtharchive_storage_hotpath_auto_recover_last_apply_ok == 0
and (time() - healtharchive_storage_hotpath_auto_recover_last_apply_timestamp_seconds) > 86400
```

Rationale:

- Avoid startup/first-run noise by requiring at least one apply attempt.
- Detect prolonged unresolved failure state rather than transient apply failure.
- Keep initial severity `warning` (with `for: 30m`) to tune signal before any escalation.

## Current State Summary

HealthArchive already has a production watchdog for stale storage hot paths (`scripts/vps-storage-hotpath-auto-recover.py`), a substantial test suite (`tests/test_ops_storage_hotpath_auto_recover.py`), and alert coverage for stale targets (`HealthArchiveStorageHotpathStaleUnrecovered`). The 2026-02-02 incident follow-ups identified three remaining gaps: explicit stale-mount integration scenario coverage, alerting for persistent `last_apply_ok=0`, and clearer failure-mode documentation.

Relevant current assets:

- Watchdog implementation:
  - `scripts/vps-storage-hotpath-auto-recover.py`
- Existing watchdog tests:
  - `tests/test_ops_storage_hotpath_auto_recover.py`
- Alert rules:
  - `ops/observability/alerting/healtharchive-alerts.yml`
- Existing storage recovery docs:
  - `docs/operations/playbooks/storage/storagebox-sshfs-stale-mount-recovery.md`
  - `docs/operations/playbooks/storage/storagebox-sshfs-stale-mount-drills.md`

### Key Unknowns

- Whether current tests should be extended in-place or split into an "integration-like" watchdog suite for readability/maintenance.
- Desired alert severity after rollout (warning vs critical).

### Assumptions

- Existing `last_apply_ok`, `last_apply_timestamp`, and `apply_total` metrics remain stable.
- Alerting stack supports deploying new rules without impacting crawl execution.
- Documentation updates can be deployed independently of runtime code.

## Goals

- Prevent silent watchdog degradation.
- Improve confidence in stale-mount recovery behavior through realistic tests.
- Give operators a clear, fast failure-mode triage path.

## Non-Goals

- Re-architecting storage tiering system.
- Changing core recovery sequencing (stop worker, repair, resume).
- Introducing risky auto-remediation beyond current bounded actions.

## Constraints

- Must remain crawl-safe: no forced worker/service interruptions during implementation.
- Must avoid high-cardinality or noisy alert rules.
- Must keep watchdog dry-run/apply behavior understandable and auditable.

## Phased Implementation Plan

### Phase 0: Failure-Mode Baseline and Alert Semantics

**Goal**: Freeze concrete failure-mode definitions and alert logic before code changes.

**Tasks**:

1. Enumerate watchdog terminal states from current script:
   - Successful apply.
   - Apply attempted but post-check failed.
   - Dry-run only due deploy lock.
   - Detection with no eligible action.
2. Define exact alert expression for "apply failing for >24h" with startup-safe guards.
3. Decide initial alert severity and escalation policy.

**Deliverables**:

- Alert expression and rationale documented in this plan.
- Failure-mode taxonomy for docs.

**Validation**:

- Maintainer sign-off that expression avoids obvious false positives.

### Phase 1: Integration-Like Stale Mount Scenario Coverage

**Goal**: Add explicit high-signal tests for stale mount recovery pathways and parity behavior.

**Tasks**:

1. Add/extend tests to validate end-to-end script decisions under scenarios such as:
   - Errno 107 with missing mount info still triggers eligible recovery path.
   - Dry-run planned actions align with apply path intent for same inputs.
   - Post-check marks `last_apply_ok=0` when repair path fails to restore readability.
2. Keep tests deterministic via monkeypatched probes and scripted command captures.
3. Ensure coverage includes both running-job and next-job stale path cases.

**Deliverables**:

- Expanded watchdog test coverage for follow-up incident scenarios.
- Regression tests tied to documented incident failure modes.

**Validation**:

- `tests/test_ops_storage_hotpath_auto_recover.py` (or split successor) passes.
- Synthetic failure scenario reliably reproduces expected non-zero apply outcome and metric state.

### Phase 2: Add Alert for Persistent Failed Apply State

**Goal**: Detect prolonged watchdog apply failure even when stale-target detection itself is intermittent.

**Tasks**:

1. Add a new alert rule in `ops/observability/alerting/healtharchive-alerts.yml`, for example:
   - watchdog enabled,
   - at least one apply attempt recorded,
   - `last_apply_ok == 0`,
   - and last apply older than 24h.
2. Add/extend alert-rule tests in `tests/test_ops_alert_rules.py` (or a dedicated alert semantics test).
3. Update `docs/operations/monitoring-and-alerting.md` and `docs/operations/thresholds-and-tuning.md` with the new rule and operator response.

**Deliverables**:

- New alert rule for stuck failed-apply state.
- Documentation and test updates.

**Validation**:

- Alert rules parse and pass tests.
- Expression behavior verified against sample metrics (manual dry run on dev metric fixture).

### Phase 3: Playbook Hardening with Failure-Mode Matrix

**Goal**: Make operator response deterministic for watchdog failure classes.

**Tasks**:

1. Update `docs/operations/playbooks/storage/storagebox-sshfs-stale-mount-recovery.md` with a dedicated "watchdog failure modes" section covering:
   - stale targets detected but no apply,
   - apply attempted and failed (`last_apply_ok=0`),
   - deploy-lock suppression behavior,
   - stale metrics writer behavior.
2. Update drill playbook to explicitly validate the new alert condition safely.
3. Cross-link from automation-maintenance playbook for periodic verification cadence.

**Deliverables**:

- Failure-mode matrix in storage playbook.
- Drill procedure for alert-path verification.

**Validation**:

- Docs reference checks pass.
- On-call/operator can follow playbook with no missing step in tabletop walkthrough.

### Phase 4: Rollout and Burn-In

**Goal**: Deploy with low-noise monitoring and validate operational signal quality.

**Tasks**:

1. Ship test and alert/doc changes.
2. Observe one-week burn-in for false positives/negatives.
3. Tune alert severity/duration if needed based on real data.

**Deliverables**:

- Stable alert signal and documented operator confidence.

**Validation**:

- No unresolved false-positive pattern in first week.
- Alert triggers in synthetic drill as expected.

## Dependencies

- Prometheus rule deployment workflow in place.
- Existing watchdog metrics emitted to node_exporter textfile collector.
- Maintainer bandwidth for short burn-in review.

## Risks and Mitigations

- Risk: New alert triggers unnecessarily when no apply attempts were expected.
  - Mitigation: gate on `apply_total > 0` and explicit time condition.
- Risk: Tests become too coupled to implementation details.
  - Mitigation: assert behavior/outcomes rather than exact internal structure.
- Risk: Playbook complexity increases.
  - Mitigation: failure-mode matrix with concise "if/then" operator actions.

## Progress Validation Framework

Each phase is complete when:

1. Code/doc artifacts are merged.
2. At least one explicit verification artifact exists:
   - passing tests,
   - validated alert expression,
   - or completed drill output.

## Timeline and Milestones

Expected timeline (single maintainer, no blockers):

- Milestone A (Days 1-2): Phase 0 complete (alert semantics finalized).
- Milestone B (Days 3-4): Phase 1 complete (integration-like tests merged).
- Milestone C (Days 5-6): Phase 2 complete (alert + docs updates merged).
- Milestone D (Days 7-8): Phase 3 complete (playbook hardening merged).
- Milestone E (Week 2): Phase 4 burn-in review and tuning complete.

## Rollout Approach

1. Merge tests first.
2. Merge alert rule with initial conservative severity.
3. Merge playbook updates immediately after alert rollout.
4. Run a safe drill to verify end-to-end detection and operator instructions.

No crawl interruption is required for any rollout step.

## Rollback Approach

- Alert rollback: revert new alert rule only if noisy.
- Test rollback: keep tests unless they block unrelated work; if needed, mark flaky case and fix forward.
- Doc rollback: revert sections if inaccurate and patch quickly.

Rollback does not require worker restart or crawl interruption.

## Exit Criteria

- Incident follow-up items #3, #4, #5 are closed in canonical incident docs.
- Watchdog failure persistence (`last_apply_ok=0`) is alert-visible.
- Storage playbook includes a complete failure-mode response matrix.
- Regression tests cover stale mount scenarios identified in 2026 incidents.

## Related Sources

- `docs/operations/incidents/2026-02-02-storage-watchdog-unmount-filter-bug.md`
- `scripts/vps-storage-hotpath-auto-recover.py`
- `ops/observability/alerting/healtharchive-alerts.yml`
- `tests/test_ops_storage_hotpath_auto_recover.py`
- `docs/operations/playbooks/storage/storagebox-sshfs-stale-mount-recovery.md`
