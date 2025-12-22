# Deployment docs

- Current production runbook: `production-single-vps.md`
  - Includes the recommended deploy helper: `scripts/vps-deploy.sh`
- Systemd unit templates (annual scheduling, worker priority, replay reconcile): `systemd/README.md`
- Search ranking rollout: `search-rollout.md`
- Deployment checklist / Vercel wiring: `hosting-and-live-server-to-dos.md`
- Cross‑repo env vars + host matrix: `environments-and-configuration.md`
- Generic checklists:
  - `production-rollout-checklist.md`
  - `staging-rollout-checklist.md` (optional future)
