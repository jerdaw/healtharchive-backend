# Crawl-Safe Roadmap Batch (Implemented 2026-01-03)

**Status:** Implemented | **Scope:** Four roadmap items implementable without touching the running annual crawl: frontend power controls, dataset pipeline hardening, repo governance, and external outreach scaffolding.

## Outcomes

### Frontend Archive Power Controls
- URL params `view=pages|snapshots`, `includeNon2xx=true`, `includeDuplicates=true` fully wired and documented
- URL canonicalization removes ineffective flags (e.g., `includeDuplicates` when `view=pages`)
- Regression tests for URL semantics and filter round-trips added

### Dataset Release Pipeline Hardening
- Release bundle validation script: `healtharchive-datasets/scripts/validate_release_bundle.py`
- Enforces manifest fields, `truncated=false`, SHA-256 verification, gzip integrity
- Workflow hardening: concurrency control, timeouts, artifact uploads on failure, immutable tags by default

### Repo Governance
- Documented Mode A (solo-fast) vs Mode B (multi-committer) governance
- CI check name inventory for all repos (backend, frontend, datasets)
- Added CODEOWNERS, PR template, and pre-push hook to datasets repo

### External Outreach Scaffolding
- Operator playbook: `docs/operations/playbooks/outreach-and-verification.md`
- Public-safe mentions log: `docs/operations/mentions-log.md`
- Healthchecks.io alignment completed for all enabled timers

## Canonical Docs Updated

- Frontend implementation guide: `healtharchive-frontend/docs/implementation-guide.md` (query param contract)
- Dataset release runbook: [operations/dataset-release-runbook.md](../../operations/dataset-release-runbook.md)
- Datasets README: `healtharchive-datasets/README.md`
- Monitoring/CI: [operations/monitoring-and-ci-checklist.md](../../operations/monitoring-and-ci-checklist.md)
- Ops indexes: [operations/README.md](../../operations/README.md), [playbooks/README.md](../../operations/playbooks/README.md)

## Historical Context

This plan was explicitly designed to be "crawl-safe" — implementable without production VPS restarts or DB migrations while the annual scrape was running. Detailed phase notes (800+ lines) preserved in git history.
