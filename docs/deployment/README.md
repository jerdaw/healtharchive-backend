# Deployment docs

## Start Here

**Deploying to production?**
- **Main:** [Production Runbook](production-single-vps.md) — Current production setup (Hetzner VPS)
- **Config:** [Configuration](environments-and-configuration.md) — Cross-repo env vars
- **Checklist:** [Hosting Checklist](hosting-and-live-server-to-dos.md) — DNS, CORS, Vercel

**Quick reference:**
| Task | Documentation |
|------|---------------|
| Deploy to VPS | [Production Runbook](production-single-vps.md) |
| Configure environment | [Configuration](environments-and-configuration.md) |
| Setup systemd services | [Systemd Units](systemd/README.md) |
| Rollback search changes | [Search Rollout](search-rollout.md) |

## All Deployment Documentation

- Current production runbook: `production-single-vps.md`
  - Includes the recommended deploy helper: `scripts/vps-deploy.sh`
- Runbook template (for new runbooks): `../_templates/runbook-template.md`
- Systemd unit templates (annual scheduling, worker priority, replay reconcile): `systemd/README.md`
- Search ranking rollout: `search-rollout.md`
- Deployment checklist / Vercel wiring: `hosting-and-live-server-to-dos.md`
- Cross‑repo env vars + host matrix: `environments-and-configuration.md`
- Generic checklists:
  - `production-rollout-checklist.md`
  - `staging-rollout-checklist.md` (optional future)
