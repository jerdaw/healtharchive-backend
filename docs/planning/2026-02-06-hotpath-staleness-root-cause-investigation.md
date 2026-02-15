# 2026-02-06: Hot-Path Staleness Root-Cause Investigation

**Plan Version**: v1.4
**Status**: In Progress (Phases 0-2 implemented in repo; evidence capture + drills require operator execution on VPS)
**Scope**: Determine and mitigate underlying causes of recurring hot-path stale mount events (Errno 107).
**Batched items**: #6

## Current Operator Decision (as of 2026-02-07)

During the active 2026 annual crawl, we are **not** converting existing annual job output dirs from direct `sshfs` mounts into
bind mounts yet, even though this is likely a contributor to hot-path staleness risk.

**Why we are holding off**:

- Converting mount topology requires an unmount + re-mount of the job output dir.
- If a crawler container is actively writing to that output dir, this can interrupt the crawl and/or create confusing partial
  failure modes.
- The watchdog + playbooks already provide bounded recovery for Errno 107; the incremental benefit of a topology conversion is
  real, but not worth intentionally interrupting an in-progress annual crawl.

**What we will do instead (crawl-safe)**:

- Keep capturing pre/post evidence bundles on any Errno 107 event.
- Run Phase 2 dry-run drills (simulation only) to ensure planned recovery remains sensible.
- Schedule the mount-topology conversion for a maintenance window after the campaign is idle.

## Current observations (as of 2026-02-07)

- The active 2026 annual job output dirs are mounted directly as `sshfs` mountpoints (not bind mounts):
  - This was confirmed via `findmnt` and via `scripts/vps-annual-output-tiering.py --year 2026` warnings
    (`reason=unexpected_mount_type`).
  - Benefit of fixing: reduce Errno 107 blast radius and make hot-path recovery simpler/more deterministic.
  - Why deferred: fixing requires unmount/re-mount of job output dirs and risks interrupting active crawls.
- Deploy-lock suppression was observed and cleared:
  - A stale `/tmp/healtharchive-backend-deploy.lock` existed and caused apply-mode watchdogs to skip.
  - The lock file was removed; metrics now show deploy lock inactive.

## Implementation Progress

- **Phase 0**: Implemented in repository (hypothesis matrix + evidence criteria).
- **Phase 1**: Implemented in repository (operator evidence capture script + playbook integration).
  - Evidence capture helper:
    - `scripts/vps-capture-hotpath-staleness-evidence.sh`
  - Evidence diff helper:
    - `scripts/vps-diff-hotpath-staleness-evidence.sh`
  - Recovery playbook now recommends capturing a bundle before state changes:
    - `docs/operations/playbooks/storage/storagebox-sshfs-stale-mount-recovery.md`
  - The playbook also recommends a post-repair evidence bundle (`--tag post-repair`) so you can diff pre/post state.
- **Phase 2**: Implemented in repository (Phase 2 drill helper + investigation log format; still requires operator-run execution on the VPS).
  - Phase 2 drill helper:
    - `scripts/vps-hotpath-staleness-drill.sh`
  - Evidence bundles now include a crawl-status snapshot (`vps-crawl-status.txt`) to correlate mount issues with live jobs.
- **Phase 2.5**: Implemented in repository (tiering UX improvement: detect + optionally repair "unexpected mount type" for annual output dirs).
  - `scripts/vps-annual-output-tiering.py` now warns when an annual output dir is mounted but not as a bind mount, and can repair it
    during a maintenance window (`--apply --repair-unexpected-mounts`).
- **Phase 2.6**: Implemented in repository (auto-reconcile stale failed tiering-unit state).
  - `scripts/vps-storage-hotpath-auto-recover.py` now detects when
    `healtharchive-warc-tiering.service` is stuck in `failed` even though no
    stale targets are currently eligible, and performs a conservative
    reconcile (`systemctl reset-failed` + `systemctl start`) when the base
    Storage Box mount is readable.
  - This reduces persistent `HealthArchiveWarcTieringFailed` alert noise from
    historical oneshot unit failures.
- **Phase 3-5**: Pending.

## Current State Summary

HealthArchive has strong reactive handling for stale mount incidents:

- Detection and bounded recovery automation for stale hot paths:
  - `scripts/vps-storage-hotpath-auto-recover.py`
- Alerting for stale/unrecovered paths:
  - `ops/observability/alerting/healtharchive-alerts.yml`
- Operational playbooks and drills:
  - `docs/operations/playbooks/storage/storagebox-sshfs-stale-mount-recovery.md`
  - `docs/operations/playbooks/storage/storagebox-sshfs-stale-mount-drills.md`

However, incident follow-ups still list a root-cause gap: why hot-path mounts go stale while base mount health can appear normal (`docs/operations/incidents/2026-01-24-infra-error-107-hotpath-thrash-and-worker-stop.md`, `docs/operations/incidents/2026-01-08-storage-hotpath-sshfs-stale-mount.md`). Current automation reduces impact but does not yet explain or eliminate recurrence.

### Key Unknowns

- Primary failure domain for stale events:
  - SSH transport instability,
  - sshfs/FUSE behavior under load,
  - bind-mount propagation edge case,
  - kernel-level interaction,
  - Storage Box endpoint behavior.
- Whether stale events correlate with specific operations (tiering, replay, high crawl I/O, reconnect cycles).
- Which sshfs option changes reduce stale events without harming throughput or recovery.

### Assumptions

- Production VPS access remains operator-only; investigation execution requires operator-run commands.
- Stale events are infrequent enough that controlled drills and passive telemetry both are required.
- Crawl-safe posture remains mandatory during data collection (no disruptive experiments on active crawl unless scheduled).

## Goals

- Identify likely root cause(s) of hot-path staleness with evidence.
- Produce a bounded mitigation plan (config/process changes) with measurable success criteria.
- Update canonical docs and incident backlog to close long-standing unknowns.

## Non-Goals

- Replacing Storage Box architecture entirely in this plan.
- Building a full new storage subsystem.
- Turning all investigation logic into always-on heavy telemetry.

## Constraints

- Active crawl must not be disturbed by investigation work.
- Must operate within single-VPS resource and ops bandwidth constraints.
- Any production configuration changes require staged rollout and rollback path.

## Phased Implementation Plan

### Phase 0: Investigation Charter and Hypothesis Matrix

**Goal**: Define what evidence is needed to prove/disprove each hypothesis.

**Tasks**:

1. Build hypothesis matrix with observable signals:
   - network/transport instability,
   - sshfs reconnect behavior,
   - bind mount repair race,
   - service restart sequencing.
2. Define evidence requirements for closure:
   - minimum number of incidents/drills observed,
   - required signal correlation quality.
3. Define crawl-safe and maintenance-window boundaries for experiments.

**Deliverables**:

- Hypothesis matrix and evidence criteria (in this plan).

**Validation**:

- Maintainer agrees criteria are sufficient to close open incident action items.

#### Hypothesis matrix (initial)

This matrix is intentionally pragmatic: it lists what we can actually observe on a single VPS without adding heavy telemetry.

| Hypothesis | Observable signals | How to confirm / rule out |
|---|---|---|
| Transport instability (SSH/TCP) | storagebox sshfs logs show reconnects; kernel logs show TCP resets/timeouts; hot-path staleness coincides with network blips | Evidence bundle contains correlated `journal-storagebox.txt` + `dmesg-tail.txt` + timestamps from watchdog state/metrics |
| sshfs/FUSE stale state under load | base mount stays readable but specific hot paths become stale; `fuse.sshfs` mounts persist in `findmnt` while ops hang | Evidence bundle contains `tiering-hotpath-probes.txt` showing per-path staleness while `findmnt-storagebox.txt` remains healthy |
| Bind-mount propagation / tiering inconsistency | hot paths appear as direct `fuse.sshfs` mounts instead of bind mounts (unexpected layout); `findmnt -T <hot>` shows `SOURCE` like `user@host:/path` and mount `OPTIONS` missing `bind` | Evidence bundle captures `mount.txt` / `findmnt` outputs that show whether hot paths are bind mounts or direct sshfs |
| Recovery sequencing race with worker activity | staleness correlates with worker touching paths during tiering repair; repeated re-picks / infra-error thrash | Capture `journal-worker.txt` alongside `journal-hotpath-watchdog.txt`; look for tight loops at the same timestamps |
| Deploy overlap / lock suppression hides apply attempts | watchdog detects targets but apply is suppressed by deploy lock; issue persists beyond expected window | Evidence bundle captures watchdog metrics + deploy lock metrics from `watchdog-metrics.prom` and node_exporter metrics |

Evidence closure criteria:

- Capture at least 2 real-world staleness events with bundles (or 1 real + 1 maintenance-window reproduction).
- At least one event must clearly show whether the base mount was healthy while a hot path was stale.
- At least one event must clearly show the mount topology at the time (bind mount vs direct sshfs).

### Phase 1: Low-Risk Instrumentation and Evidence Capture

**Goal**: Improve event-level forensic context with minimal runtime risk.

**Tasks**:

1. Extend watchdog event logging/state capture (if needed) to include:
   - base mount readability,
   - mount metadata consistency,
   - operation context (running job, next job, tiering path),
   - command outcomes and durations.
2. Add a lightweight operator script for event snapshot capture (journal excerpts + mount/network state) to standardize incident evidence.
3. Document where evidence artifacts are stored and retention expectations.

**Deliverables**:

- Enhanced event capture in watchdog state/logs.
- Repeatable evidence collection script/workflow.

**Validation**:

- Synthetic drill produces complete evidence bundle.
- Evidence bundle is usable for post-event analysis without ad hoc shell history.

**Operator how-to (VPS)**:

When you see Errno 107 alerts or symptoms (before unmounting/repairing):

```bash
cd /opt/healtharchive-backend
./scripts/vps-capture-hotpath-staleness-evidence.sh --tag pre-repair
```

Then proceed with state-changing recovery steps in:

- `docs/operations/playbooks/storage/storagebox-sshfs-stale-mount-recovery.md`

### Phase 2: Controlled Drill and Correlation Runs

**Goal**: Reproduce stale-like conditions safely enough to test hypotheses.

**Tasks**:

1. Execute structured dry-run drills and limited maintenance-window drills.
2. Capture and compare:
   - pre-failure state,
   - failure detection state,
   - post-recovery state.
3. Correlate stale events with:
   - system logs,
   - sshfs service behavior,
   - crawl/tiering/replay activity.

**Deliverables**:

- Investigation log with timestamped drill/event records.
- Preliminary hypothesis ranking by evidence strength.

**Validation**:

- At least one complete event lifecycle captured with full telemetry.
- At least one hypothesis downgraded or eliminated by evidence.

**Operator how-to (VPS) (safe, dry-run)**:

Use this to capture pre/post bundles and optionally run the watchdog in **dry-run simulation mode** (no service changes, no unmounts).

```bash
cd /opt/healtharchive-backend
./scripts/vps-hotpath-staleness-drill.sh \
  --simulate-broken-path /srv/healtharchive/jobs/hc/<JOB_DIR> \
  --note "phase2 drill (dry-run)"
```

Outputs:

- `drill-pre` and `drill-post` evidence bundles under:
  - `/srv/healtharchive/ops/observability/hotpath-staleness/`
- A small correlation log line appended to:
  - `/srv/healtharchive/ops/observability/hotpath-staleness/investigation-log.tsv`

### Phase 3: Mitigation Candidate Definition and Risk Assessment

**Goal**: Turn evidence into actionable, bounded changes.

**Tasks**:

1. Define mitigation candidates (for example sshfs option changes, restart policy adjustments, sequencing refinements).
2. For each candidate, document:
   - expected effect,
   - failure modes,
   - rollout risk,
   - rollback command path.
3. Select one primary and one fallback mitigation for staged rollout.

**Deliverables**:

- Candidate mitigation matrix.
- Selected rollout candidates with rationale.

**Validation**:

- Candidate choice is evidence-backed and has explicit rollback.

### Phase 4: Staged Rollout and Measurement

**Goal**: Validate mitigation effectiveness under real workload.

**Tasks**:

1. Apply chosen mitigation in controlled maintenance window.
2. Monitor key indicators over defined observation period:
   - stale event frequency,
   - watchdog recovery count,
   - crawl interruption count,
   - replay/tiering related errors.
3. Compare against pre-change baseline.

**Deliverables**:

- Rollout report with before/after metrics.

**Validation**:

- Predefined success threshold met (for example sustained reduction in stale incidents over observation window).

### Phase 5: Documentation Closure and Decision Record

**Goal**: Institutionalize findings and close open backlog actions.

**Tasks**:

1. Update storage playbooks with validated root-cause findings and mitigations.
2. Create/update decision record if operational baseline changes materially.
3. Close incident follow-up TODOs with links to evidence and decisions.

**Deliverables**:

- Updated canonical docs.
- Decision record (if required).
- Closed follow-up action items.

**Validation**:

- Incident TODOs for root-cause investigation marked complete with references.

## Dependencies

- Operator access/time for drill execution on VPS.
- Existing observability stack for metric and log retrieval.
- Coordination with crawl schedule for maintenance-window experiments.

## Risks and Mitigations

- Risk: Rare events delay conclusive evidence.
  - Mitigation: combine passive event capture with controlled drills.
- Risk: Investigative changes increase operational complexity.
  - Mitigation: keep instrumentation lightweight and reversible.
- Risk: Candidate mitigation regresses throughput.
  - Mitigation: staged rollout with explicit rollback and performance checks.

## Progress Validation Framework

Progress is validated per phase by concrete artifacts:

- hypothesis matrix,
- evidence bundles,
- drill logs,
- mitigation matrix,
- rollout report,
- docs/decision updates.

No phase is complete without both artifact creation and verification evidence.

## Timeline and Milestones

Because event frequency is variable, timeline is milestone-based with target windows:

- Milestone A (Week 1): Phase 0 complete (charter and hypotheses).
- Milestone B (Weeks 1-2): Phase 1 complete (instrumentation and evidence workflow).
- Milestone C (Weeks 2-4): Phase 2 complete (drills/correlation evidence).
- Milestone D (Week 4): Phase 3 complete (mitigation selection).
- Milestone E (Weeks 5-6): Phase 4 complete (staged rollout + observation).
- Milestone F (Week 6): Phase 5 complete (docs closure and follow-up completion).

## Rollout Approach

- Investigation work starts with non-disruptive telemetry and drills.
- Configuration changes occur only in planned maintenance windows.
- One mitigation change at a time to preserve attribution.

## Rollback Approach

- For each mitigation change, predefine rollback commands before apply.
- If stale frequency or crawl stability worsens, immediately revert to prior known-good settings and continue evidence collection.
- Keep watchdog automation in place during rollback to preserve resilience.

## Exit Criteria

- Root-cause hypothesis is narrowed to a defensible primary explanation (or clearly bounded set of explanations).
- At least one mitigation has been validated in production without degrading crawl stability.
- Canonical storage playbooks and, if needed, a decision record reflect the new baseline.
- Open root-cause TODOs in related incidents are closed with evidence links.

## Related Sources

- `docs/operations/incidents/2026-01-24-infra-error-107-hotpath-thrash-and-worker-stop.md`
- `docs/operations/incidents/2026-01-08-storage-hotpath-sshfs-stale-mount.md`
- `scripts/vps-storage-hotpath-auto-recover.py`
- `docs/operations/playbooks/storage/storagebox-sshfs-stale-mount-recovery.md`
