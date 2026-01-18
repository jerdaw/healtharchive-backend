# Operational hardening: tiering alerting + incident follow-ups (v1) — implementation plan

Status: **planned** (created 2026-01-17)

## Goal

Close out open operational gaps from recent incidents and enable proactive alerting
infrastructure that already exists but is not yet wired:

- **Enable tiering health metrics + alerting** to detect storage/tiering failures
  before they cascade into user-visible outages (ref: 2026-01-16 incident).
- **Complete incident follow-ups** from 2026-01-09 and 2026-01-16 incidents.
- **Document replay smoke target independence** decision (canary replay proposal).

This plan is intentionally **small and focused**: complete it in a single session
before returning to larger feature work.

## Why this is "next" (roadmap selection)

This is the highest-leverage operational work because:

- **Implementation already exists** — systemd timers and metrics exporters are written;
  we just need to enable and wire alerting.
- **Direct incident prevention** — the 2026-01-16 replay smoke + tiering failure could
  have been caught earlier with proper alerting.
- **Low risk, high reward** — no code changes to the backend; purely operational.
- **Unblocks other work** — incident follow-ups should be closed before starting new
  feature development (good hygiene).

## Docs setup (do first)

1) **Create this plan doc**
   - File: `docs/roadmaps/2026-01-17-ops-tiering-alerting-and-incident-followups.md` (this document)

2) **Backlog linkage**
   - Update `docs/roadmaps/roadmap.md`:
     - Add link to this plan under "Ops surface / environments" section.

3) **Roadmaps index**
   - Update `docs/roadmaps/README.md` to list this plan under "Implementation plans (active)".

4) **Canonical docs to update during/after implementation**
   - `docs/operations/healtharchive-ops-roadmap.md` — mark tiering alerting as done
   - `docs/operations/observability-alerting.md` — add tiering alert rules
   - `docs/operations/incidents/2026-01-09-annual-crawl-hc-job-stalled.md` — close action items
   - `docs/operations/incidents/2026-01-16-replay-smoke-503-and-warctieringfailed.md` — close action items

---

## Scope, goals, constraints

### In-scope outcomes (what we will deliver)

- **Tiering health alerting enabled** on the production VPS:
  - `healtharchive-tiering-metrics.timer` enabled and running.
  - Metrics visible at `http://127.0.0.1:9100/metrics` (node exporter textfile).
  - Prometheus alert rule wired for `healtharchive_tiering_metrics_ok == 0`.
  - Alertmanager notification configured (email or webhook).
- **Incident 2026-01-16 follow-ups closed**:
  - Tiering alerting enabled (this plan).
  - Decision documented on replay smoke target independence.
- **Incident 2026-01-09 follow-ups closed**:
  - Captured repeated URLs from stalled crawl (if still recoverable).
  - Document per-job recovery decision (implement or explicitly defer).

### Non-goals (explicitly out of scope)

- No new monitoring infrastructure (use existing Prometheus/Alertmanager stack).
- No changes to backend code.
- No new systemd timers (all already exist).
- No replay service architecture changes (canary is a future decision).

### Constraints to respect (project resources + policy)

- **Single-VPS reality** — all changes are local to the production VPS.
- **Minimal operational complexity** — enable existing infrastructure, don't add new.
- **Reversible** — disabling a timer or alert rule is trivial.

---

## Current-state map (what exists today)

### Tiering metrics infrastructure

- Timer: `healtharchive-tiering-metrics.timer` (installed, not enabled)
- Service: `healtharchive-tiering-metrics.service`
- Textfile collector output: `/var/lib/prometheus/node-exporter/healtharchive_tiering.prom`
- Node exporter: reads textfile collector directory
- Prometheus: scrapes node exporter at `:9100`
- Alertmanager: configured for HealthArchive alerts

### Incident notes (open action items)

**2026-01-16 (replay smoke 503 + tiering failed)**:
- [x] Re-applied WARC tiering
- [x] Restarted replay service
- [x] Updated systemd unit for `rshared` bind propagation
- [x] Updated tiering service for stale mount auto-repair
- [x] Enabled storage hot-path auto-recovery timer
- [ ] Enable tiering health metrics + alerting (this plan)
- [ ] Consider "canary replay" job for smoke independence (decision needed)

**2026-01-09 (annual crawl job 6 stalled)**:
- [ ] Capture repeated URLs after recovery
- [ ] Assess timeout tuning
- [ ] Document per-job recovery without stopping worker

---

## Definition of Done (DoD) + acceptance criteria

### Tiering alerting

- `healtharchive-tiering-metrics.timer` is enabled and active on VPS.
- `curl -s http://127.0.0.1:9100/metrics | grep healtharchive_tiering` returns metrics.
- Prometheus has a firing-capable alert rule for tiering unhealthy.
- Alert fires correctly in a manual test (simulate failure).

### Incident follow-ups

- 2026-01-16 incident note: all action items marked complete or explicitly deferred with rationale.
- 2026-01-09 incident note: all action items marked complete or explicitly deferred with rationale.
- Canonical ops roadmap reflects current state.

---

## Phase 1 — Enable tiering health metrics (VPS configuration)

**Objective:** Enable the existing tiering metrics timer and verify metrics are visible.

### 1.1 Enable the timer

On the production VPS:

```bash
sudo systemctl enable --now healtharchive-tiering-metrics.timer
sudo systemctl status healtharchive-tiering-metrics.timer
```

Verify the service runs successfully:

```bash
sudo systemctl start healtharchive-tiering-metrics.service
journalctl -u healtharchive-tiering-metrics.service --no-pager -n 20
```

### 1.2 Verify metrics are exported

```bash
curl -s http://127.0.0.1:9100/metrics | grep healtharchive_tiering
```

Expected output includes:
- `healtharchive_tiering_metrics_ok` (1 = healthy, 0 = unhealthy)
- `healtharchive_tiering_last_success_timestamp_seconds`

### 1.3 Document the enabled state

Update `docs/operations/healtharchive-ops-roadmap.md`:
- Move "Enable tiering health metrics + alerting" from "Current ops tasks" to a completed section or remove.

**Deliverables:**
- Timer enabled and running
- Metrics visible in node exporter output

**Exit criteria:** Metrics export verified; timer survives reboot.

---

## Phase 2 — Wire Prometheus alerting rules

**Objective:** Create and deploy alert rules that fire when tiering is unhealthy.

### 2.1 Create alert rule

Add to Prometheus alert rules (typically `/etc/prometheus/rules/healtharchive.yml`):

```yaml
groups:
  - name: healtharchive-tiering
    rules:
      - alert: HealthArchiveTieringUnhealthy
        expr: healtharchive_tiering_metrics_ok == 0
        for: 5m
        labels:
          severity: warning
        annotations:
          summary: "HealthArchive WARC tiering is unhealthy"
          description: "The tiering metrics service reports unhealthy state. Check sshfs mounts and tiering service logs."

      - alert: HealthArchiveTieringStale
        expr: (time() - healtharchive_tiering_last_success_timestamp_seconds) > 7200
        for: 10m
        labels:
          severity: warning
        annotations:
          summary: "HealthArchive tiering metrics are stale"
          description: "No successful tiering metrics update in over 2 hours. Timer may be failing."
```

### 2.2 Reload Prometheus

```bash
sudo systemctl reload prometheus
```

Verify rules are loaded:

```bash
curl -s http://127.0.0.1:9090/api/v1/rules | jq '.data.groups[] | select(.name == "healtharchive-tiering")'
```

### 2.3 Test alert firing (manual)

Temporarily break the tiering metrics to verify alerting works:

```bash
# Create a failure condition (e.g., write unhealthy metric manually)
echo 'healtharchive_tiering_metrics_ok 0' | sudo tee /var/lib/prometheus/node-exporter/healtharchive_tiering_test.prom

# Wait 5+ minutes for alert to fire
# Check Prometheus alerts UI or Alertmanager

# Clean up test file
sudo rm /var/lib/prometheus/node-exporter/healtharchive_tiering_test.prom
```

### 2.4 Document alert rules

Update `docs/operations/observability-alerting.md`:
- Add tiering alerts to the documented rules list
- Include the alert names, thresholds, and what they mean

**Deliverables:**
- Alert rules deployed to Prometheus
- Alert firing verified in test
- Documentation updated

**Exit criteria:** Alert fires correctly when tiering is unhealthy.

---

## Phase 3 — Close incident 2026-01-16 follow-ups

**Objective:** Mark all action items complete or explicitly defer with rationale.

### 3.1 Tiering alerting (complete via Phase 1-2)

Update the incident note to mark this complete.

### 3.2 Replay smoke target independence decision

**Decision point:** Should we implement a "canary replay" job that uses local-only WARCs
to distinguish pywb failures from storage mount failures?

Options:
- **A) Implement canary replay now** — adds complexity but improves incident triage.
- **B) Defer canary replay** — current tiering alerting + auto-recovery is sufficient for now.
- **C) Document as backlog** — add to roadmap.md for later consideration.

**Recommendation:** Option C (document as backlog). The tiering alerting and auto-recovery
mechanisms are now in place, which reduces the urgency. Canary replay is a nice-to-have
but not critical.

### 3.3 Update incident note

Edit `docs/operations/incidents/2026-01-16-replay-smoke-503-and-warctieringfailed.md`:
- Mark tiering alerting action item complete
- Document the canary replay decision (deferred to backlog with rationale)

**Deliverables:**
- Incident note updated with all action items addressed
- Canary replay added to roadmap.md if deferred

**Exit criteria:** Incident note shows all items complete or explicitly deferred.

---

## Phase 4 — Close incident 2026-01-09 follow-ups

**Objective:** Complete or defer the annual crawl stall follow-ups.

### 4.1 Capture repeated URLs (if recoverable)

Check if the stalled job's state is still available:

```bash
# Check job state
ha-backend show-job --id 6

# If archive_tool state exists, examine for repeated URLs
ls -la /path/to/job6/.archive_state.json
```

If state is available, extract and document the repeated URLs.
If state was cleaned up, document that the data is no longer recoverable.

### 4.2 Assess timeout tuning

Review the crawl configuration for job 6:

```bash
ha-backend show-job --id 6 | jq '.config'
```

Document whether timeout tuning would have helped:
- If yes: add specific tuning recommendation to `docs/operations/playbooks/crawl-timeout-tuning.md`
- If no: document why current settings are appropriate

### 4.3 Per-job recovery decision

**Decision point:** Should we implement per-job stall recovery without stopping the entire worker?

Options:
- **A) Implement per-job recovery** — complex; risk of partial state corruption.
- **B) Keep current behavior** — stopping worker is safe and simple; acceptable blast radius.
- **C) Document manual procedure** — operator can manually recover specific jobs without code changes.

**Recommendation:** Option C. Document a manual procedure for recovering individual jobs
while minimizing worker disruption. Defer code changes until there's a clear pattern
of need.

### 4.4 Update incident note

Edit `docs/operations/incidents/2026-01-09-annual-crawl-hc-job-stalled.md`:
- Document findings from URL capture (or note data unavailable)
- Document timeout tuning assessment
- Document per-job recovery decision

**Deliverables:**
- Incident note updated with all action items addressed
- Any new playbooks or procedures documented

**Exit criteria:** Incident note shows all items complete or explicitly deferred.

---

## Phase 5 — Final documentation updates

**Objective:** Ensure all canonical docs reflect the new state.

### 5.1 Update ops roadmap

Edit `docs/operations/healtharchive-ops-roadmap.md`:
- Remove tiering alerting from "Current ops tasks"
- Add any new tasks identified during incident review

### 5.2 Update future roadmap (if needed)

If canary replay or per-job recovery were deferred, add them to `docs/roadmaps/roadmap.md`
under appropriate sections.

### 5.3 Archive this plan

Move this plan to `docs/roadmaps/implemented/2026-01-17-ops-tiering-alerting-and-incident-followups.md`
when complete.

**Deliverables:**
- Ops roadmap current
- Future roadmap updated with deferred items
- This plan archived

**Exit criteria:** All docs reflect reality; no stale TODOs.

---

## Risk register (pre-mortem)

- **Risk:** Alert fatigue from false positives.
  - **Mitigation:** Use `for: 5m` duration before firing; tune thresholds if needed.
- **Risk:** Tiering metrics timer fails silently.
  - **Mitigation:** Added staleness alert (no update in 2+ hours).
- **Risk:** Incident follow-up data is no longer recoverable.
  - **Mitigation:** Document what was available; focus on future prevention.

---

## Appendix: Commands reference

### Enable tiering metrics

```bash
sudo systemctl enable --now healtharchive-tiering-metrics.timer
sudo systemctl status healtharchive-tiering-metrics.timer
curl -s http://127.0.0.1:9100/metrics | grep healtharchive_tiering
```

### Reload Prometheus

```bash
sudo systemctl reload prometheus
curl -s http://127.0.0.1:9090/api/v1/rules | jq '.data.groups[].name'
```

### Check incident notes

```bash
cat docs/operations/incidents/2026-01-16-replay-smoke-503-and-warctieringfailed.md
cat docs/operations/incidents/2026-01-09-annual-crawl-hc-job-stalled.md
```
