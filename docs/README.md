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
   - `deployment/hosting-and-live-server-to-dos.md` (deployment checklist + Vercel wiring)
   - `deployment/environment-matrix.md` (cross‑repo env var + host matrix)
   - `deployment/production-rollout-checklist.md` (generic production checklist)
   - `deployment/staging-rollout-checklist.md` (optional future staging)
4. Operations (how to keep it healthy)
   - `operations/monitoring-and-ci-checklist.md`

## Notes

- No secrets live in this repo. Any token/password values shown in docs must be
  placeholders.
- The `archive_tool` crawler has its own internal documentation at
  `src/archive_tool/docs/documentation.md`.
