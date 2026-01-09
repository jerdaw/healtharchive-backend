# Backend docs index

This folder contains the canonical documentation for the **HealthArchive backend**
(`healtharchive-backend`).

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
   - `deployment/hosting-and-live-server-to-dos.md` (deployment checklist + Vercel wiring)
   - `deployment/environments-and-configuration.md` (cross‑repo env vars + host matrix)
   - `deployment/production-rollout-checklist.md` (generic production checklist)
   - `deployment/staging-rollout-checklist.md` (optional future staging)
5. Operations (how to keep it healthy)
   - `operations/README.md` (index of ops docs)
6. Roadmaps and implementation plans
   - `roadmaps/README.md`
   - `roadmap-process.md` (short pointer)

## Notes

- No secrets live in this repo. Any token/password values shown in docs must be
  placeholders.
- The `archive_tool` crawler has its own internal documentation at
  `src/archive_tool/docs/documentation.md`.
