# Roadmaps

## Current backlog

- Future roadmap (what is *not* implemented yet): `roadmap.md`

## Implementation plans (active)

Implementation plans live directly under `docs/planning/` while they are active.
When complete, move them to `docs/planning/implemented/` and date them.

Active plans:

- Disk usage investigation (48GB discrepancy): `2026-02-01-disk-usage-investigation.md`
- WARC discovery consistency improvements: `2026-01-29-warc-discovery-consistency.md`
- Deploy workflow hardening (single VPS): `2026-02-07-deploy-workflow-hardening.md`
- CI schema + governance guardrails: `2026-02-06-ci-schema-and-governance-guardrails.md`
- Storage watchdog observability hardening: `2026-02-06-storage-watchdog-observability-hardening.md`
- Crawl operability (locks, writability, retry controls): `2026-02-06-crawl-operability-locks-and-retry-controls.md`
- Hot-path staleness root-cause investigation: `2026-02-06-hotpath-staleness-root-cause-investigation.md`

## Operator Follow-Through (Maintenance Window)

Some plans are "implemented in repo" but still require a short, operator-run maintenance step on the VPS.

Current known items:

- Job lock-dir cutover: restart services that read `/etc/healtharchive/backend.env` after crawls are idle.
  - Plan: `2026-02-06-crawl-operability-locks-and-retry-controls.md` (Phase 4)
  - Hard requirement: do not restart the worker mid-crawl unless you explicitly accept interrupting crawls.

## Implemented plans (history)

- Implemented plans archive: `implemented/README.md`
- Operational resilience improvements: `implemented/2026-02-01-operational-resilience-improvements.md`
- WARC manifest verification: `implemented/2026-01-29-warc-manifest-verification.md`
- Patch-job-config CLI + integration tests: `implemented/2026-01-28-patch-job-config-and-integration-tests.md`
- archive_tool hardening + ops improvements: `implemented/2026-01-27-archive-tool-hardening-and-ops-improvements.md`
- Annual crawl throughput and WARC-first artifacts: `implemented/2026-01-23-annual-crawl-throughput-and-artifacts.md`
- Infra-error retry storms + Storage Box hot-path resilience: `implemented/2026-01-24-infra-error-and-storage-hotpath-hardening.md`
- SLA and service commitments (v1): `implemented/2026-01-17-sla-and-service-commitments.md`
- Test coverage: critical business logic: `implemented/2026-01-17-test-coverage-critical-business-logic.md`
- Disaster recovery and escalation procedures: `implemented/2026-01-17-disaster-recovery-and-escalation-procedures.md`
- Operational hardening: tiering alerting + incident follow-ups: `implemented/2026-01-17-ops-tiering-alerting-and-incident-followups.md`
- Search ranking + snippet quality iteration (v3): `implemented/2026-01-03-search-ranking-and-snippets-v3.md`
- Storage Box / sshfs stale mount recovery + integrity: `implemented/2026-01-08-storagebox-sshfs-stale-mount-recovery-and-integrity.md`

## Historical context

- HealthArchive 6-Phase Upgrade Roadmap (2025; archived): `implemented/2025-12-24-6-phase-upgrade-roadmap-2025.md`
