# HealthArchive Documentation

This documentation portal covers the **HealthArchive backend** and links to
frontend and datasets documentation.

## Quick Start by Role

**Choose your path:**

- **👤 Operators**: Start with [Operations Overview](operations/README.md) → [Operator Responsibilities](operations/playbooks/core/operator-responsibilities.md)
- **💻 Developers**: Start with [Development Guide](development/README.md) → [Live Testing](development/live-testing.md)
- **🔧 Deploying**: Start with [Production Runbook](deployment/production-single-vps.md)
- **📊 API consumers**: Start with [API Documentation](api.md)
- **📚 Researchers**: Start with [Project Overview](project.md) → [Datasets](datasets-external/README.md)

## Key Resources

| Need | Documentation |
|------|---------------|
| Architecture overview | [Architecture](architecture.md) |
| Production deployment | [Production Runbook](deployment/production-single-vps.md) |
| Local development setup | [Dev Setup](development/dev-environment-setup.md) |
| Incident response | [Incident Response](operations/playbooks/core/incident-response.md) |
| Search API | [API Documentation](api.md) |
| Monitoring setup | [Monitoring](operations/monitoring-and-ci-checklist.md) |

## Documentation Structure

This docs portal is built from the backend repo only. Frontend and datasets
docs are canonical in their repos and are linked-to from this site:

- Frontend pointers: `frontend-external/README.md`
- Datasets pointers: `datasets-external/README.md`

Shared VPS facts that are not specific to the backend are canonical in:

- `/home/jer/repos/platform-ops`
- `/home/jer/repos/platform-ops/PLAT-009-shared-vps-documentation-boundary.md`

## Recommended reading order

0. Project docs portal (multi-repo navigation)
   - `project.md`
1. Architecture & implementation (how the code works)
   - `architecture.md`
2. Documentation guidelines (how docs stay sane)
   - `documentation-guidelines.md`
   - `documentation-process-audit.md` (audit of doc processes; 2026-01-09)
   - `decisions/README.md` (decision records for high-stakes choices)
3. Local development / live testing (how to run it locally)
   - `development/live-testing.md`
   - `development/dev-environment-setup.md` (local setup + local vs VPS guidance)
   - `development/testing-guidelines.md` (backend test expectations)
4. Deployment (how to run it on a server)
   - `deployment/production-single-vps.md` (current production runbook)
   - `deployment/systemd/README.md` (systemd units: annual scheduler, crawl monitoring + auto-recovery, baseline drift, replay reconcile + smoke tests, change tracking, annual search verify, coverage guardrails, cleanup automation, worker priority)
   - `deployment/replay-service-pywb.md` (pywb replay service for full-fidelity browsing)
   - `deployment/search-rollout.md` (enable v2 search + rollback)
   - `deployment/pages-table-rollout.md` (pages table backfill + browse fast path)
  - `deployment/hosting-and-live-server-to-dos.md` (historical hosting notes + optional future staging ideas)
   - `deployment/environments-and-configuration.md` (cross‑repo env vars + host matrix)
   - `deployment/production-rollout-checklist.md` (generic production checklist)
   - `deployment/staging-rollout-checklist.md` (optional future staging)
5. Operations (how to keep it healthy)
   - `operations/README.md` (index of ops docs)
6. Roadmaps and implementation plans
   - `planning/README.md`
   - `roadmap-process.md` (short pointer)

## Notes

- No secrets live in this repo. Any token/password values shown in docs must be
  placeholders.
- The `archive_tool` crawler has its own internal documentation at
  `src/archive_tool/docs/documentation.md`.
