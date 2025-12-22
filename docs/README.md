# Backend docs index

This folder contains the canonical documentation for the **HealthArchive backend**
(`healtharchive-backend`).

## Recommended reading order

1. Architecture & implementation (how the code works)
   - `architecture.md`
2. Local development / live testing (how to run it locally)
   - `development/live-testing.md`
3. Deployment (how to run it on a server)
   - `deployment/production-single-vps.md` (current production runbook)
   - `deployment/systemd/README.md` (systemd units: annual scheduler timer, worker priority, replay reconcile)
   - `deployment/replay-service-pywb.md` (pywb replay service for full-fidelity browsing)
   - `deployment/search-rollout.md` (enable v2 search + rollback)
   - `deployment/pages-table-rollout.md` (pages table backfill + browse fast path)
   - `deployment/hosting-and-live-server-to-dos.md` (deployment checklist + Vercel wiring)
   - `deployment/environment-matrix.md` (cross‑repo env var + host matrix)
   - `deployment/production-rollout-checklist.md` (generic production checklist)
   - `deployment/staging-rollout-checklist.md` (optional future staging)
4. Operations (how to keep it healthy)
   - `operations/monitoring-and-ci-checklist.md`
   - `operations/annual-campaign.md` (canonical annual scope + seeds; Jan 01 UTC)
   - `operations/agent-handoff-guidelines.md` (internal handoff rules)
   - `operations/claims-registry.md` (proof artifacts for reliability claims)
   - `operations/data-handling-retention.md` (internal data handling contract)
   - `operations/export-integrity-contract.md` (manifest + immutability rules)
   - `operations/automation-verification-rituals.md` (timer verification checks)
   - `operations/dataset-release-runbook.md` (release verification checklist)
   - `operations/risk-register.md` (top risks + mitigations)
   - `operations/ops-cadence-checklist.md` (weekly/monthly/quarterly routines)
   - `operations/growth-constraints.md` (storage + scope budgets)
   - `operations/restore-test-procedure.md` (quarterly restore checks)
   - `operations/restore-test-log-template.md` (public-safe log entries)
   - `operations/adoption-signals-log-template.md` (public-safe adoption log)
   - `operations/healtharchive-upgrade-plan.md` (todo-only; remaining tasks)
   - `operations/automation-implementation-plan.md` (detailed phased plan for production-only automation)
   - `operations/replay-and-preview-automation-plan.md` (design plan + guardrails; reconciler exists as `ha-backend replay-reconcile`)
   - `operations/search-quality.md` (how to evaluate relevance changes)
   - `operations/search-golden-queries.md` (curated queries + expectations)
   - `operations/legacy-crawl-imports.md` (import legacy WARCs from old ZIM crawl dirs)
5. Roadmaps (historical)
   - `roadmaps/healtharchive-upgrade-2025.md` (Phases 0–6 historical plan)

## Notes

- No secrets live in this repo. Any token/password values shown in docs must be
  placeholders.
- The `archive_tool` crawler has its own internal documentation at
  `src/archive_tool/docs/documentation.md`.
