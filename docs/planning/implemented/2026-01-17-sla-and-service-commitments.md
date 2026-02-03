# SLA and Service Commitments (Implemented 2026-01-18)

**Status:** Implemented | **Scope:** Explicit service level documentation defining availability, response time, data freshness, and communication commitments.

## Outcomes

### Service Level Objectives
- **Availability:** 99.5% monthly (allows ~3.6 hours downtime; realistic for single-VPS)
- **Response times:** p95 targets per endpoint (/api/health <100ms, /api/search <2s)
- **Data freshness:** Primary sources crawled monthly; new content searchable within 48 hours

### Maintenance Windows
- **Routine:** 24 hours notice, <30 minutes
- **Major:** 72 hours notice, <4 hours
- **Emergency:** As needed, documented afterward

### Communication Commitments
- Planned changes in changelog
- Incidents: public-safe summary when user expectations affected
- No dedicated status page (future consideration)

### Performance Baselines
- API response time baselines documented
- Crawl/indexing throughput baselines documented
- Semi-annual review cadence

## Canonical Doc Created

- [operations/service-levels.md](../../operations/service-levels.md)

## Docs Updated

- [operations/monitoring-and-ci-checklist.md](../../operations/monitoring-and-ci-checklist.md) — references SLOs
- [operations/playbooks/core/incident-response.md](../../operations/playbooks/core/incident-response.md) — references communication commitments
- `mkdocs.yml` — navigation updated

## Key Decisions

- **Best-effort, not contractual:** Clear language that these are targets, not SLAs
- **Single-VPS reality:** Conservative targets that don't require HA infrastructure
- **Solo operator:** Response times reflect operator availability constraints

## Historical Context

7-phase documentation plan (547+ lines) with detailed target rationale, measurement approaches, and baseline templates. Preserved in git history.
