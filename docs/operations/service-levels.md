# Service Levels

This document describes the service level objectives and commitments for HealthArchive.ca. These are targets, not contractual guarantees.

**Last Updated:** 2026-01-18

---

## Scope and Context

HealthArchive is a public-interest research archive operated as a best-effort service. These service levels reflect commitments appropriate for the project's resources and mission:

- **Infrastructure:** Single VPS (Hetzner cx33: 4 vCPU / 8GB RAM / 80GB SSD)
- **Staffing:** Solo operator, no 24/7 coverage
- **Purpose:** Public good research tool, not a commercial service

All targets are measured and reviewed on a best-effort basis. Incidents outside business hours may see delayed response.

---

## Availability

### Target

**99.5% monthly availability**

This allows for approximately 3.6 hours of downtime per month, which is realistic for:
- Single-server architecture (no redundancy)
- Manual maintenance operations
- Solo operator response times

### Measurement

- **Primary endpoint:** `GET /api/health` (https://api.healtharchive.ca/api/health)
- **Monitoring method:** External uptime monitoring (Healthchecks.io, UptimeRobot)
- **Measurement window:** Calendar month
- **Exclusions:** Planned maintenance with advance notice (see Maintenance Windows)

### Review

- Semi-annual review of actuals vs target
- Adjust target if infrastructure or staffing changes significantly

---

## Response Times

Target response times for key API endpoints, measured server-side (excludes network latency):

| Endpoint | p50 Target | p95 Target | p99 Target | Notes |
|----------|------------|------------|------------|-------|
| `GET /api/health` | 50ms | 100ms | 200ms | Minimal processing |
| `GET /api/search` | 500ms | 2000ms | 5000ms | Complex queries, database-dependent |
| `GET /api/sources` | 100ms | 300ms | 500ms | Lightweight, typically cached |
| `GET /api/snapshot/{id}` | 100ms | 300ms | 500ms | Single record lookup |
| `GET /api/changes` | 200ms | 500ms | 1000ms | Precomputed change feed |

### Degradation Criteria

The service is considered degraded when:
- p95 latency exceeds target by 2× for 5+ consecutive minutes
- p99 latency exceeds target by 3× for 5+ consecutive minutes
- Any endpoint timeout rate exceeds 1%

### Exclusions

These targets do not apply to:
- Attack traffic or abusive request patterns
- Bulk export operations (`/api/exports/*`)
- Replay operations (separate service: `replay.healtharchive.ca`)

---

## Data Freshness

### Crawl Cadence

**Primary sources (Health Canada, PHAC):** Crawled at least annually, with ad-hoc updates as resources permit

- Major annual crawl campaign: typically January
- Ad-hoc crawls: triggered by significant health events or policy changes
- Schedule is best-effort and subject to operator availability

### Indexing Latency

- **Crawl-to-indexed:** Within 24 hours of crawl completion, subject to operator availability
- **Indexed-to-searchable:** Immediate (same database transaction)

### Change Tracking

- **Changes computed:** Within 24 hours of new snapshots being indexed, subject to operator availability
- **Change feed updated:** On next `compute-changes` run (automated via systemd timer)

### Exceptions

- Manual crawls may have different timelines based on urgency
- Emergency updates (e.g., public health crises) prioritized on case-by-case basis

---

## Maintenance Windows

### Window Types

#### Routine Maintenance
- **Examples:** Security updates, dependency patches, configuration changes
- **Advance Notice:** 24 hours (via changelog)
- **Maximum Duration:** 30 minutes
- **Typical Downtime:** < 15 minutes

#### Major Maintenance
- **Examples:** Database migrations, infrastructure changes, new feature deployments
- **Advance Notice:** 72 hours (via changelog + announcement if user-facing)
- **Maximum Duration:** 4 hours
- **Typical Downtime:** 1-2 hours

#### Emergency Maintenance
- **Examples:** Critical security patches, severe bug fixes
- **Advance Notice:** ASAP (post-hoc notification if required immediately)
- **Duration:** As needed
- **Communication:** Documented in changelog after completion

### Preferred Timing

- **Weekdays, off-peak hours:** Early morning (00:00-06:00 UTC) or late evening (22:00-24:00 UTC)
- **Avoid:** Business hours (14:00-22:00 UTC), weekends, holidays

### Post-Maintenance Verification

After all maintenance:
- Health check validation (`/api/health`, `/archive`)
- Smoke test (search query, snapshot retrieval)
- External uptime monitor confirmation
- Documented in changelog

---

## Communication Commitments

### Channels

**Public Channels:**
- **Changelog:** https://healtharchive.ca/changelog - primary source for planned changes and incidents
- **Status:** https://healtharchive.ca/status - service status overview
- No dedicated status page (updates via changelog)

**Internal/Operator:**
- Incident notes (selected public-safe summaries published)
- Operations logs (private)

### Incident Communication

Following the incident disclosure policy ([Option B](playbooks/core/incident-response.md)):

**Sev0/Sev1 (Service Down / Major Degradation):**
- Communicate within 48 hours of resolution, or as soon as practical
- Public-safe summary published to changelog
- Includes impact, timeline, resolution, and prevention measures

**Sev2/Sev3 (Minor Issues):**
- Include in regular changelog if user-facing
- Internal documentation only if operator-only impact

### Changelog Cadence

- **Major changes:** Immediate entry
- **Minor changes:** Batched weekly or monthly
- **Security updates:** Published as appropriate (may delay for responsible disclosure)

### Limitations

Communication timelines are best-effort and depend on:
- Solo operator availability (no 24/7 coverage)
- Incident severity and complexity
- Need for coordination with external parties (e.g., infrastructure provider)

---

## Performance Baselines

### Purpose

Baselines provide reference points for detecting performance degradation and validating improvements. They are not targets but rather observations of typical performance under normal conditions.

### Baseline Measurement Approach

Baselines should be measured:
- On production hardware (single VPS, current configuration)
- Under typical load (not during crawls or heavy operations)
- Multiple samples to account for variance
- Documented with measurement date and conditions

### Current Baselines

> [!NOTE]
> Initial baselines to be measured and documented during implementation. This section will be updated with actual measurements.

**API Response Times** (server-side, measured via curl timing):

| Endpoint | Baseline p50 | Baseline p95 | Measured Date |
|----------|--------------|--------------|---------------|
| `GET /api/health` | TBD | TBD | TBD |
| `GET /api/search?q=covid` | TBD | TBD | TBD |
| `GET /api/sources` | TBD | TBD | TBD |
| `GET /api/snapshot/{id}` | TBD | TBD | TBD |

**Operational Baselines:**

| Operation | Baseline Throughput | Measured Date |
|-----------|---------------------|---------------|
| Crawl (pages/hour) | TBD | TBD |
| Indexing (records/second) | TBD | TBD |
| Change computation (changes/minute) | TBD | TBD |

### Baseline Review

- **Semi-annual review:** Compare current performance against baselines
- **After major changes:** Re-baseline if infrastructure or architecture changes
- **Drift documentation:** Document and investigate significant baseline drift (>20%)

---

## Review and Update Process

### Review Cadence

**Annual Review:**
- Assess targets vs actuals for the past year
- Evaluate appropriateness of commitments
- Update targets if resources or infrastructure change significantly

**Triggered Reviews:**
- After infrastructure changes (e.g., VPS upgrade, migration)
- After staffing changes (e.g., additional operators)
- After major architectural changes (e.g., HA implementation)

### Update Process

1. **Propose changes:** Document in roadmap or ADR
2. **Review against actuals:** Compare proposed targets to historical data
3. **Update documentation:** Revise this document
4. **Communicate changes:** Announce via changelog if user-facing impact
5. **Update monitoring:** Adjust alerts and dashboards to match new targets

### Document Maintenance

- **Location:** `docs/operations/service-levels.md`
- **Owner:** Primary operator
- **Format:** Markdown, version-controlled in healtharchive-backend repo
- **Navigation:** Linked from operations index and docs site navigation

---

## References

- [Production Runbook](../deployment/production-single-vps.md) - Infrastructure details and deployment procedures
- [Incident Response Playbook](playbooks/core/incident-response.md) - Incident classification and response procedures
- [Monitoring Checklist](monitoring-and-ci-checklist.md) - Monitoring setup and external checks
- [Disaster Recovery](../deployment/disaster-recovery.md) - Recovery procedures and RTO/RPO targets
- [Ops Cadence Checklist](ops-cadence-checklist.md) - Routine operational tasks

---

## Changelog

| Date | Change | Rationale |
|------|--------|-----------|
| 2026-01-18 | Initial version | Established baseline service level documentation |
