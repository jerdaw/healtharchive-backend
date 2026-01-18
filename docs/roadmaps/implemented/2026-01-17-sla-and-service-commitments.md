# SLA and service commitments documentation (v1) — implementation plan

Status: **completed** (created 2026-01-17, implemented 2026-01-18)

## Goal

Create explicit service level documentation that defines what users and operators
can expect from HealthArchive:

- **Service Level Objectives (SLOs)** — measurable targets for availability,
  response time, and data freshness.
- **Maintenance Windows** — documented expectations for planned downtime.
- **Communication Commitments** — how and when users are notified of issues.
- **Performance Baselines** — documented performance expectations for key operations.

This plan produces **documentation only** — no code changes. The deliverables define
expectations rather than enforce them technically.

## Why this is "next" (roadmap selection)

Service level documentation is valuable because:

- **User expectations** — researchers and users should know what to expect.
- **Operator guidance** — clear targets help prioritize incident response.
- **Accountability** — documented commitments enable honest assessment of service quality.
- **Best practice** — any public service should communicate its reliability posture.

Note: This is lower priority than DR/escalation documentation but completes the
operational documentation picture.

## Docs setup (do first)

1) **Create this plan doc**
   - File: `docs/roadmaps/2026-01-17-sla-and-service-commitments.md` (this document)

2) **Roadmaps index**
   - Update `docs/roadmaps/README.md` to list this plan under "Implementation plans (active)".

3) **New canonical docs to create**
   - `docs/operations/service-levels.md` — SLOs and commitments

4) **Existing docs to update**
   - `docs/operations/monitoring-and-ci-checklist.md` — reference SLOs
   - `mkdocs.yml` — add new doc to navigation

---

## Scope, goals, constraints

### In-scope outcomes (what we will deliver)

**Service Level Objectives (SLOs):**
- Availability target (e.g., 99.5% monthly)
- API response time targets (p50, p95, p99)
- Search latency targets
- Data freshness commitments (crawl cadence)

**Maintenance Windows:**
- Planned maintenance schedule approach
- Notification lead time
- Maximum maintenance duration

**Communication Commitments:**
- Incident notification channels
- Status page approach (if any)
- Changelog/announcement cadence

**Performance Baselines:**
- API endpoint response time baselines
- Search query performance baselines
- Crawl throughput baselines

### Non-goals (explicitly out of scope)

- Formal contractual SLA (this is documentation, not a contract)
- Automated SLO enforcement (future work)
- SLI (Service Level Indicator) instrumentation (existing monitoring is sufficient)
- Financial penalties or credits (not applicable)

### Constraints to respect

- **Single-VPS reality** — availability targets must be realistic for single-node.
- **Solo operator** — response times for incidents and communication depend on operator availability.
- **No 24/7 coverage** — incidents outside business hours may see delayed response.
- **Best-effort service** — HealthArchive is a public good, not a commercial service.
- **Honest commitments** — only commit to what can be delivered.

---

## Current-state map (what exists today)

### Monitoring infrastructure

- External uptime monitoring via UptimeRobot/Healthchecks.io
- Prometheus metrics for internal monitoring
- No public status page

### Performance data (approximate, from existing operations)

| Metric | Current Typical | Notes |
|--------|----------------|-------|
| Availability | ~99%+ | Based on incident history |
| `/api/health` p95 | < 100ms | Fast health check |
| `/api/search` p95 | < 2s | For common queries |
| Crawl cadence | Monthly | Per source |

### Documentation gaps

- No explicit availability target
- No documented response time targets
- No maintenance window policy
- No communication commitments

---

## Definition of Done (DoD) + acceptance criteria

### Service Level Documentation

- SLOs are explicit with measurable targets
- Maintenance window policy is documented
- Communication approach is documented
- Performance baselines are documented
- Document is linked from operations index
- Document is discoverable via docs site navigation

### Validation

- SLOs are realistic based on current infrastructure
- Commitments are achievable with current resources
- No over-promising beyond single-VPS capabilities

---

## Phase 1 — Define availability SLOs

**Objective:** Establish explicit availability targets with rationale.

### 1.1 Analyze historical availability

Review:
- Incident history from `docs/operations/incidents/`
- Uptime monitoring data
- Planned maintenance history

### 1.2 Define availability target

**Recommended: 99.5% monthly availability**

Rationale:
- Single VPS means some downtime is inevitable
- 99.5% allows ~3.6 hours downtime per month
- Realistic for manual operations and maintenance
- Higher targets (99.9%) require redundancy not currently in place

**Breakdown:**
| Target | Allowed Downtime | Realistic? |
|--------|-----------------|------------|
| 99% | 7.2 hours/month | Too lenient |
| 99.5% | 3.6 hours/month | Appropriate |
| 99.9% | 43 min/month | Requires HA |

### 1.3 Define measurement approach

- Measured via external uptime monitoring
- Primary endpoint: `/api/health`
- Secondary: homepage availability
- Excludes planned maintenance (with advance notice)

### 1.4 Document availability SLO

Include:
- Target percentage
- Measurement method
- Exclusions (planned maintenance)
- Review cadence (semi-annual)

**Deliverables:**
- Availability SLO documented with rationale

**Exit criteria:** Clear, measurable availability target.

---

## Phase 2 — Define response time SLOs

**Objective:** Establish API response time targets.

### 2.1 Identify key endpoints

**User-facing (public):**
- `GET /api/health` — health check
- `GET /api/search` — search queries
- `GET /api/sources` — source listing
- `GET /api/snapshot/{id}` — snapshot retrieval
- `GET /api/changes` — change feed

**Operator-facing (admin):**
- `GET /api/admin/jobs` — job listing
- `GET /metrics` — Prometheus metrics

### 2.2 Define response time targets

**Recommended targets:**

| Endpoint | p50 | p95 | p99 | Notes |
|----------|-----|-----|-----|-------|
| `/api/health` | 50ms | 100ms | 200ms | Fast, minimal work |
| `/api/search` | 500ms | 2000ms | 5000ms | Complex queries |
| `/api/sources` | 100ms | 300ms | 500ms | Cached/lightweight |
| `/api/snapshot/{id}` | 100ms | 300ms | 500ms | Single record |
| `/api/changes` | 200ms | 500ms | 1000ms | Precomputed |

### 2.3 Define degradation thresholds

When to consider the service degraded:
- p95 exceeds target by 2x for 5+ minutes
- p99 exceeds target by 3x for 5+ minutes
- Any endpoint timeout rate > 1%

### 2.4 Document response time SLOs

Include:
- Per-endpoint targets
- Measurement approach (server-side timing)
- Degradation criteria
- Excluded scenarios (attack traffic, bulk exports)

**Deliverables:**
- Response time SLOs documented per endpoint

**Exit criteria:** Clear, measurable latency targets.

---

## Phase 3 — Define data freshness commitments

**Objective:** Document expectations for data currency.

### 3.1 Define crawl cadence commitments

**Current approach:**
- Monthly crawls for primary sources (hc, phac)
- Annual campaigns for comprehensive coverage
- Ad-hoc crawls for specific needs

**Recommended commitment:**
- Primary sources: crawled at least annually, with ad-hoc updates as resources permit
- New content visibility: within 48 hours of successful crawl, subject to operator availability
- Change detection: computed within 24 hours of indexing, subject to operator availability

### 3.2 Define indexing freshness

- Crawl-to-indexed: within 24 hours of crawl completion
- Indexed-to-searchable: immediate (same transaction)

### 3.3 Define change tracking freshness

- Changes computed: within 24 hours of new snapshots
- Change feed updated: on next `compute-changes` run

### 3.4 Document data freshness commitments

Include:
- Crawl cadence by source type
- Indexing latency expectations
- Change tracking latency expectations
- Exceptions (manual crawls, emergency updates)

**Deliverables:**
- Data freshness commitments documented

**Exit criteria:** Clear expectations for data currency.

---

## Phase 4 — Define maintenance window policy

**Objective:** Document planned downtime approach.

### 4.1 Define maintenance types

**Routine maintenance:**
- Security updates
- Dependency updates
- Configuration changes
- Typically < 15 minutes downtime

**Major maintenance:**
- Database migrations
- Infrastructure changes
- New feature deployments
- May require 1-4 hours

**Emergency maintenance:**
- Security patches
- Critical bug fixes
- No advance notice possible

### 4.2 Define notification requirements

| Type | Advance Notice | Channel |
|------|---------------|---------|
| Routine | 24 hours | Changelog |
| Major | 72 hours | Changelog + announcement |
| Emergency | ASAP (post-hoc if needed) | Changelog |

### 4.3 Define maintenance windows

**Preferred windows:**
- Weekdays, off-peak hours (early morning or late evening)
- Avoid: business hours, weekends, holidays

**Maximum duration:**
- Routine: 30 minutes
- Major: 4 hours
- Emergency: as needed (document afterward)

### 4.4 Document maintenance policy

Include:
- Maintenance types and definitions
- Notification requirements
- Preferred windows
- Maximum durations
- Post-maintenance verification

**Deliverables:**
- Maintenance window policy documented

**Exit criteria:** Clear maintenance expectations.

---

## Phase 5 — Define communication commitments

**Objective:** Document how service status is communicated.

### 5.1 Define communication channels

**Current channels:**
- Changelog (public, in docs)
- Incident notes (internal, some public-safe)
- No dedicated status page

**Recommended approach:**
- Continue using changelog for planned changes
- Publish public-safe incident summaries per existing policy
- Consider simple status page (future, optional)

### 5.2 Define incident communication

Per existing incident disclosure policy (Option B):
- Publish public-safe notes when incidents affect user expectations
- Sev0/Sev1: communicate within 48 hours of resolution, or as soon as practical
- Sev2/Sev3: include in regular changelog if relevant

### 5.3 Define changelog cadence

- Major changes: immediate entry
- Minor changes: batched weekly/monthly
- Security updates: as appropriate (may delay for safety)

### 5.4 Document communication commitments

Include:
- Available channels
- Incident communication timeline
- Changelog cadence
- What is/isn't communicated publicly

**Deliverables:**
- Communication commitments documented

**Exit criteria:** Clear communication expectations.

---

## Phase 6 — Document performance baselines

**Objective:** Establish documented baselines for key operations.

### 6.1 Capture current baselines

Run baseline measurements for:
- API response times (via curl timing)
- Search query latency (sample queries)
- Crawl throughput (pages/hour)
- Indexing throughput (records/second)

### 6.2 Document API baselines

| Endpoint | Baseline p50 | Baseline p95 | Measured |
|----------|-------------|-------------|----------|
| `/api/health` | Xms | Xms | YYYY-MM-DD |
| `/api/search?q=covid` | Xms | Xms | YYYY-MM-DD |
| `/api/sources` | Xms | Xms | YYYY-MM-DD |

### 6.3 Document operational baselines

| Operation | Baseline | Measured |
|-----------|----------|----------|
| Crawl throughput | X pages/hour | YYYY-MM-DD |
| Indexing throughput | X records/second | YYYY-MM-DD |
| Change computation | X changes/minute | YYYY-MM-DD |

### 6.4 Define baseline review cadence

- Semi-annual: review baselines against current performance
- After major changes: re-baseline if needed
- Document baseline drift if observed

**Deliverables:**
- Performance baselines documented
- Baseline review process defined

**Exit criteria:** Current performance is documented and measurable.

---

## Phase 7 — Integration and finalization

**Objective:** Create canonical document and integrate into docs structure.

### 7.1 Create the canonical doc

Create `docs/operations/service-levels.md` with all sections:
- Introduction and scope
- Availability SLO
- Response time SLOs
- Data freshness commitments
- Maintenance window policy
- Communication commitments
- Performance baselines
- Review and update process

### 7.2 Update navigation

Add to `mkdocs.yml`:
```yaml
nav:
  - Operations:
    - ...
    - Service Levels: operations/service-levels.md
```

### 7.3 Add cross-references

Update:
- `docs/operations/monitoring-and-ci-checklist.md` — reference SLOs
- `docs/operations/incident-response.md` — reference communication commitments

### 7.4 Review and validate

- Verify all targets are realistic
- Confirm measurement approaches are feasible
- Check for consistency across sections

### 7.5 Archive this plan

Move to `docs/roadmaps/implemented/` when complete.

**Deliverables:**
- `docs/operations/service-levels.md` created
- Navigation updated
- Cross-references added
- Plan archived

**Exit criteria:** Service level documentation is complete and discoverable.

---

## Risk register (pre-mortem)

- **Risk:** Over-committing to targets that can't be met.
  - **Mitigation:** Conservative targets based on single-VPS reality.
- **Risk:** Targets become stale as service evolves.
  - **Mitigation:** Quarterly review cadence; update when infrastructure changes.
- **Risk:** Users expect contractual SLA from documentation.
  - **Mitigation:** Clear language that this is best-effort, not contractual.
- **Risk:** Performance baselines are not representative.
  - **Mitigation:** Multiple measurements; document conditions; re-baseline regularly.

---

## Appendix: Service levels document template

```markdown
# Service Levels

This document describes the service level objectives and commitments for
HealthArchive.ca. These are targets, not contractual guarantees.

## Scope

HealthArchive is a public-interest research archive. These service levels
reflect best-effort commitments appropriate for the project's resources
and mission.

## Availability

**Target:** 99.5% monthly availability

- Measured via external monitoring of `/api/health`
- Excludes planned maintenance with advance notice
- Reviewed quarterly

## Response Times

| Endpoint | p95 Target |
|----------|-----------|
| /api/health | 100ms |
| /api/search | 2000ms |
| /api/sources | 300ms |
| /api/snapshot/{id} | 300ms |

## Data Freshness

- Primary sources crawled monthly
- New content searchable within 48 hours of crawl
- Changes computed within 24 hours of indexing

## Maintenance Windows

- Routine: 24 hours notice, < 30 minutes
- Major: 72 hours notice, < 4 hours
- Emergency: as needed, documented afterward

## Communication

- Planned changes: documented in changelog
- Incidents: public-safe summary when user expectations affected
- No dedicated status page (may add in future)

## Performance Baselines

[See baseline measurements table]

## Review Process

- Annual review of targets vs actuals
- Update targets when infrastructure changes significantly

---

Last updated: YYYY-MM-DD
```
