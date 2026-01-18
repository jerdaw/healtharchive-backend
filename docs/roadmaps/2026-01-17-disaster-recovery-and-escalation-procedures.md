# Disaster recovery and escalation procedures (v1) — implementation plan

Status: **planned** (created 2026-01-17)

## Goal

Create comprehensive documentation for disaster recovery and escalation procedures
that are currently missing from the operational documentation:

- **Disaster Recovery Runbook** — RTO/RPO targets, complete VPS restoration procedures,
  storage failure recovery, and DR drill schedule.
- **Escalation Procedures** — on-call responsibilities, escalation contacts, DRI
  assignments, and break-glass procedures.

This plan produces **documentation only** — no code changes. The deliverables are
operational runbooks and policy documents.

## Why this is "next" (roadmap selection)

These documents are high priority because:

- **Single point of failure** — the project runs on a single VPS; operators need
  clear procedures for total failure scenarios.
- **Incident response gap** — recent incidents have postmortems but no escalation
  path documentation.
- **Bus factor** — with a single operator, explicit documentation ensures continuity.
- **Best practice** — any production service should have DR procedures documented.

## Docs setup (do first)

1) **Create this plan doc**
   - File: `docs/roadmaps/2026-01-17-disaster-recovery-and-escalation-procedures.md` (this document)

2) **Roadmaps index**
   - Update `docs/roadmaps/README.md` to list this plan under "Implementation plans (active)".

3) **New canonical docs to create**
   - `docs/deployment/disaster-recovery.md` — DR runbook
   - `docs/operations/escalation-procedures.md` — escalation policy and contacts

4) **Existing docs to update**
   - `docs/operations/incident-response.md` — link to escalation procedures
   - `docs/deployment/production-single-vps.md` — link to DR runbook
   - `mkdocs.yml` — add new docs to navigation

---

## Scope, goals, constraints

### In-scope outcomes (what we will deliver)

**Disaster Recovery Runbook:**
- Defined RTO (Recovery Time Objective) and RPO (Recovery Point Objective)
- Complete VPS restoration procedure from NAS backup
- Database restoration procedure
- Storage/WARC recovery procedure
- Archive root reconstruction
- Service startup sequence after recovery
- Verification checklist post-recovery
- DR drill schedule and procedure

**Escalation Procedures:**
- On-call responsibilities (even if single operator)
- Escalation path by incident severity
- Contact information storage (secure, not in git)
- DRI (Directly Responsible Individual) assignments
- Break-glass procedures for critical failures
- Handoff procedures for operator changes

### Non-goals (explicitly out of scope)

- Multi-VPS failover architecture (future consideration)
- Automated DR (manual procedures are acceptable for current scale)
- Formal SLA documentation (separate plan)
- Third-party backup verification (out of scope)

### Constraints to respect

- **Single operator reality** — procedures should be executable by one person.
- **Budget constraints** — no additional infrastructure required.
- **Existing backup infrastructure** — use existing NAS + nightly backup.
- **Security** — no secrets or contact info in git-tracked documents.

---

## Current-state map (what exists today)

### Backup infrastructure

- **Nightly backups:** `pg_dump -Fc` to `/srv/healtharchive/backups/` (14-day retention)
- **Offsite copy:** Synology NAS pull over Tailscale
- **Quarterly restore tests:** documented in `docs/operations/playbooks/restore-test.md`

### Recovery documentation (gaps)

| Document | Status | Gap |
|----------|--------|-----|
| Backup procedures | Exists | None |
| Restore test procedure | Exists | None |
| Complete VPS restoration | Missing | Full DR scenario |
| RTO/RPO targets | Missing | No defined targets |
| Escalation procedures | Missing | No escalation path |
| Contact information | Missing | No secure storage |

### Related existing docs

- `docs/deployment/production-single-vps.md` — provisioning (not recovery)
- `docs/operations/incident-response.md` — incident handling (no escalation)
- `docs/operations/playbooks/restore-test.md` — restore verification (not full DR)
- `docs/operations/risk-register.md` — identifies VPS as single point of failure

---

## Definition of Done (DoD) + acceptance criteria

### Disaster Recovery Runbook

- RTO and RPO are explicitly documented with rationale
- Step-by-step VPS restoration procedure is complete and testable
- Database restoration procedure includes verification steps
- Archive root reconstruction addresses WARC integrity
- Service startup sequence is documented with health checks
- DR drill procedure exists with schedule
- Linked from production runbook and incident response

### Escalation Procedures

- On-call responsibilities are clearly defined
- Escalation path covers sev0, sev1, sev2, sev3 incidents
- Break-glass procedures exist for common critical failures
- Contact information storage approach is documented (not the contacts themselves)
- Linked from incident response documentation

---

## Phase 1 — Define RTO/RPO targets

**Objective:** Establish and document recovery objectives with rationale.

### 1.1 Analyze service criticality

Consider:
- User impact of downtime (research access, no real-time critical users)
- Data loss tolerance (backups are nightly; up to 24h data loss is acceptable)
- Complexity of recovery (single VPS with documented setup)

### 1.2 Define targets

**Recommended targets for HealthArchive:**

| Metric | Target | Rationale |
|--------|--------|-----------|
| **RPO** | 24 hours | Nightly backups; crawl data can be re-crawled |
| **RTO** | 8 hours | Manual VPS provisioning + restore is feasible in a work day |
| **MTTR** | 4 hours | For partial failures (service restart, DB recovery) |

### 1.3 Document targets

Create section in DR runbook explaining:
- What RPO/RTO mean in HealthArchive context
- Why these targets are appropriate
- When to revisit (scale changes, criticality changes)

**Deliverables:**
- RTO/RPO targets documented with rationale

**Exit criteria:** Targets are explicit and justified.

---

## Phase 2 — Document complete VPS restoration procedure

**Objective:** Step-by-step procedure to restore HealthArchive from total VPS loss.

### 2.1 Identify restoration scenarios

**Scenario A: VPS total loss, NAS backup available**
- Most likely DR scenario
- NAS has DB dump + configuration backups

**Scenario B: VPS total loss, NAS unavailable**
- Worst case; recover from any available backup
- May need to accept data loss

**Scenario C: VPS corrupted but recoverable**
- Partial recovery; may not need full restoration

### 2.2 Document Scenario A procedure

**Prerequisites:**
- New VPS provisioned (Hetzner or equivalent)
- Tailscale access configured
- NAS backup accessible

**Procedure outline:**
1. Provision new VPS (reference existing provisioning docs)
2. Install base dependencies
3. Configure Tailscale access
4. Mount NAS or transfer backup files
5. Restore database from `pg_dump`
6. Restore application configuration
7. Restore archive root (WARCs)
8. Start services in correct order
9. Verify restoration

### 2.3 Document service startup sequence

Order matters for HealthArchive:
1. Database (PostgreSQL)
2. Backend API (uvicorn)
3. Worker (optional, can delay)
4. Replay service (pywb)
5. Reverse proxy (Caddy)

For each service:
- Start command
- Health check command
- Expected behavior
- What to check if it fails

### 2.4 Document verification checklist

Post-recovery verification:
- [ ] Database responds to queries
- [ ] `/api/health` returns 200
- [ ] `/api/sources` returns expected sources
- [ ] Search returns results
- [ ] Replay service serves archived pages
- [ ] Public URLs resolve correctly
- [ ] HTTPS/TLS working
- [ ] Monitoring reconnected

**Deliverables:**
- Complete VPS restoration procedure
- Service startup sequence
- Verification checklist

**Exit criteria:** Procedure is detailed enough to follow without prior knowledge.

---

## Phase 3 — Document database restoration procedure

**Objective:** Detailed procedure for PostgreSQL database restoration.

### 3.1 Document backup file location and format

- Backup location: `/srv/healtharchive/backups/` (local) + NAS
- Format: `pg_dump -Fc` (custom format, compressed)
- Naming: `healtharchive-YYYY-MM-DD.dump`
- Retention: 14 days local, longer on NAS

### 3.2 Document restoration commands

```bash
# Create fresh database
sudo -u postgres createdb healtharchive_restored

# Restore from dump
pg_restore -d healtharchive_restored /path/to/backup.dump

# Verify restoration
psql healtharchive_restored -c "SELECT COUNT(*) FROM snapshots;"

# If verification passes, swap databases
sudo -u postgres psql -c "ALTER DATABASE healtharchive RENAME TO healtharchive_old;"
sudo -u postgres psql -c "ALTER DATABASE healtharchive_restored RENAME TO healtharchive;"
```

### 3.3 Document integrity verification

After restoration:
- Row counts match expected ranges
- Recent snapshots exist (within RPO)
- Foreign key constraints pass
- No orphaned records

### 3.4 Document partial restoration scenarios

- Restore specific tables only
- Point-in-time recovery (if WAL archiving is enabled)
- Restore to a different server for verification

**Deliverables:**
- Database restoration procedure
- Integrity verification steps
- Partial restoration options

**Exit criteria:** DBA-level detail for restoration.

---

## Phase 4 — Document archive root reconstruction

**Objective:** Procedure for recovering WARC files and archive storage.

### 4.1 Document archive root structure

```
/srv/healtharchive/archive/
├── jobs/
│   ├── hc-2026-01/
│   │   ├── *.warc.gz
│   │   └── .archive_state.json
│   └── phac-2026-01/
├── tiered/
│   └── (storage box mount)
└── manifests/
```

### 4.2 Document WARC recovery scenarios

**Scenario: Local WARCs lost, tiered storage intact**
- Re-import from tiered storage
- Verify against manifests

**Scenario: Tiered storage unavailable, local intact**
- Continue operating with local WARCs
- Re-tier when storage available

**Scenario: Both local and tiered lost**
- Accept data loss
- Re-crawl affected sources
- Document what was lost

### 4.3 Document integrity verification

- Manifest checksum validation
- WARC file integrity (`warcio validate`)
- Database-to-filesystem consistency

### 4.4 Document re-tiering procedure

If WARCs need to be re-tiered after recovery:
- Verify local WARCs are complete
- Run tiering service
- Verify tiered copies
- Update manifests

**Deliverables:**
- Archive root recovery procedure
- Integrity verification steps
- Re-tiering procedure

**Exit criteria:** Clear path to recover archive data.

---

## Phase 5 — Document DR drill procedure and schedule

**Objective:** Establish regular DR testing to validate procedures.

### 5.1 Define drill types

**Tabletop drill (quarterly):**
- Walk through DR procedure without execution
- Identify gaps in documentation
- Update procedures based on findings

**Partial restore drill (quarterly):**
- Restore database to temporary instance
- Verify data integrity
- Document results in ops log

**Full DR drill (annual):**
- Provision temporary VPS
- Execute full restoration procedure
- Verify all services functional
- Document total time and issues
- Tear down temporary infrastructure

### 5.2 Define drill schedule

| Drill Type | Frequency | Next Due | Owner |
|------------|-----------|----------|-------|
| Tabletop | Quarterly | Q1 2026 | Operator |
| Partial restore | Quarterly | Q1 2026 | Operator |
| Full DR | Annual | 2026-06 | Operator |

### 5.3 Document drill procedure

For each drill type:
- Prerequisites
- Step-by-step procedure
- Success criteria
- Documentation requirements
- Follow-up actions

### 5.4 Document drill results template

```markdown
# DR Drill: <type> (<date>)

## Summary
- Type: Tabletop / Partial / Full
- Duration: X hours
- Result: Pass / Fail / Partial

## Procedure Followed
1. Step...

## Issues Encountered
- Issue 1...

## Documentation Updates Needed
- Update X...

## Follow-up Actions
- [ ] Action 1...
```

**Deliverables:**
- DR drill schedule
- Drill procedures by type
- Results template

**Exit criteria:** Drill schedule established; procedures testable.

---

## Phase 6 — Document escalation procedures

**Objective:** Create escalation policy and contact management approach.

### 6.1 Define escalation levels

| Level | Criteria | Response Time | Actions |
|-------|----------|---------------|---------|
| **Sev0** | Complete outage, data loss risk | Immediate | All hands, external comms |
| **Sev1** | Major degradation, user impact | < 1 hour | Primary on-call engaged |
| **Sev2** | Partial degradation, workaround exists | < 4 hours | Investigate, schedule fix |
| **Sev3** | Minor issue, no user impact | < 24 hours | Track, fix in normal flow |

### 6.2 Define escalation path

For single-operator setup:
1. **Primary:** Operator (self)
2. **Backup:** Documented break-glass procedures
3. **External:** Community/support channels (if applicable)

For future multi-operator setup:
1. **Primary:** On-call operator
2. **Secondary:** Backup operator
3. **Escalation:** Project lead

### 6.3 Document DRI assignments

| Area | DRI | Backup |
|------|-----|--------|
| Backend API | Operator | (self) |
| Worker/Crawls | Operator | (self) |
| Database | Operator | (self) |
| Storage/WARC | Operator | (self) |
| Replay service | Operator | (self) |
| Infrastructure | Operator | (self) |

### 6.4 Document contact information storage

**Approach:** Store contacts in secure, non-git location.

Recommended:
- `/etc/healtharchive/contacts.env` on VPS (mode 600)
- Password manager for personal backup
- Not in git repository

Contents to store:
- Primary operator contact (phone, email)
- Backup contacts (if any)
- Hosting provider support contacts
- Domain registrar contacts

### 6.5 Document break-glass procedures

**Break-glass: API unresponsive**
1. SSH to VPS via Tailscale
2. Check service status: `systemctl status healtharchive-api`
3. Check logs: `journalctl -u healtharchive-api -n 100`
4. Restart service: `sudo systemctl restart healtharchive-api`
5. If restart fails, check database connectivity

**Break-glass: Database unreachable**
1. Check PostgreSQL status: `systemctl status postgresql`
2. Check disk space: `df -h`
3. Check PostgreSQL logs: `journalctl -u postgresql -n 100`
4. Restart PostgreSQL: `sudo systemctl restart postgresql`
5. If restart fails, check for corruption

**Break-glass: VPS unreachable**
1. Check Tailscale status from another device
2. Access Hetzner console
3. Reboot via Hetzner panel
4. If persistent, provision new VPS and restore

**Deliverables:**
- Escalation level definitions
- Escalation path documentation
- DRI assignments
- Contact storage approach
- Break-glass procedures

**Exit criteria:** Clear escalation path for all severity levels.

---

## Phase 7 — Integration and finalization

**Objective:** Integrate new docs into documentation structure and validate.

### 7.1 Create the canonical docs

Create:
- `docs/deployment/disaster-recovery.md`
- `docs/operations/escalation-procedures.md`

### 7.2 Update navigation

Add to `mkdocs.yml`:
```yaml
nav:
  - Deployment:
    - ...
    - Disaster Recovery: deployment/disaster-recovery.md
  - Operations:
    - ...
    - Escalation Procedures: operations/escalation-procedures.md
```

### 7.3 Add cross-references

Update existing docs to link to new procedures:
- `docs/operations/incident-response.md` → link to escalation
- `docs/deployment/production-single-vps.md` → link to DR
- `docs/operations/risk-register.md` → reference DR mitigation

### 7.4 Review and validate

- Read through procedures end-to-end
- Verify commands are accurate
- Check all links resolve
- Ensure no secrets are exposed

### 7.5 Archive this plan

Move to `docs/roadmaps/implemented/` when complete.

**Deliverables:**
- Canonical docs created
- Navigation updated
- Cross-references added
- Plan archived

**Exit criteria:** Docs are discoverable and integrated; `make docs-build` passes.

---

## Risk register (pre-mortem)

- **Risk:** Procedures become stale if not tested.
  - **Mitigation:** Quarterly drill schedule ensures regular validation.
- **Risk:** Contact information becomes outdated.
  - **Mitigation:** Review contacts during quarterly ops cadence.
- **Risk:** DR procedure is too complex to follow under stress.
  - **Mitigation:** Keep procedures simple; use checklists; practice with drills.
- **Risk:** Single operator bottleneck.
  - **Mitigation:** Document everything; consider backup operator for future.

---

## Appendix: Document templates

### Disaster Recovery Runbook structure

```markdown
# Disaster Recovery Runbook

## Recovery Objectives
- RPO: 24 hours
- RTO: 8 hours
- MTTR: 4 hours

## Scenarios
### Scenario A: Complete VPS loss
### Scenario B: Database corruption
### Scenario C: Storage failure

## Procedures
### 1. VPS Provisioning
### 2. Database Restoration
### 3. Archive Root Recovery
### 4. Service Startup
### 5. Verification

## DR Drills
### Schedule
### Procedures
### Results Log

## References
```

### Escalation Procedures structure

```markdown
# Escalation Procedures

## Severity Levels
## Escalation Path
## DRI Assignments
## Contact Management
## Break-Glass Procedures
## Handoff Procedures

## References
```
