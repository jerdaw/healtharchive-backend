# Disaster Recovery and Escalation Procedures (Implemented 2026-01-18)

**Status:** Implemented | **Scope:** Comprehensive DR runbook and escalation procedures for single-VPS production environment.

## Outcomes

### Disaster Recovery Runbook
- **RTO/RPO targets:** RPO 24 hours, RTO 8 hours, MTTR 4 hours (appropriate for single-VPS)
- **VPS restoration procedure:** Complete steps from NAS backup to verified services
- **Database restoration:** `pg_restore` procedure with integrity verification
- **Archive root reconstruction:** WARC recovery scenarios and integrity checks
- **Service startup sequence:** PostgreSQL → API → Worker → pywb → Caddy
- **DR drill schedule:** Quarterly tabletop/partial, annual full drill

### Escalation Procedures
- **Severity levels:** Sev0-Sev3 with response times and actions
- **Escalation path:** Primary operator → break-glass procedures → documented backups
- **DRI assignments:** All areas assigned to operator (single-operator reality)
- **Break-glass procedures:** API unresponsive, database unreachable, VPS unreachable
- **Contact storage:** Secure location (`/etc/healtharchive/contacts.env`), not in git

## Canonical Docs Created

- [deployment/disaster-recovery.md](../../deployment/disaster-recovery.md)
- [operations/escalation-procedures.md](../../operations/escalation-procedures.md)

## Docs Updated

- [operations/playbooks/core/incident-response.md](../../operations/playbooks/core/incident-response.md) — links to escalation
- [deployment/production-single-vps.md](../../deployment/production-single-vps.md) — links to DR
- [operations/risk-register.md](../../operations/risk-register.md) — references DR mitigation
- `mkdocs.yml` — navigation updated

## Historical Context

7-phase documentation plan (630+ lines) with detailed procedures for each DR scenario, break-glass commands, and drill templates. Preserved in git history.
