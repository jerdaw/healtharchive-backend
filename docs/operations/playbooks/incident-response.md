# Incident response playbook (operators)

Goal: restore service safely and capture enough context to prevent repeat incidents.

Canonical references:

- Production runbook: `../../deployment/production-single-vps.md`
- Monitoring checklist: `../monitoring-and-ci-checklist.md`
- Baseline drift: `../baseline-drift.md`

## When the site/API looks broken

1. Confirm what’s failing (public surface):
   - `cd /opt/healtharchive-backend && ./scripts/verify_public_surface.py`
2. Check services:
   - `sudo systemctl status healtharchive-api healtharchive-worker --no-pager -l`
3. Check recent logs:
   - `sudo journalctl -u healtharchive-api -n 200 --no-pager`
   - `sudo journalctl -u healtharchive-worker -n 200 --no-pager`
4. Check baseline drift (production correctness):
   - `./scripts/check_baseline_drift.py --mode live`

## If you need to deploy a fix

- Follow `deploy-and-verify.md` (don’t skip the deploy gate).

## What “done” means

- The public surface verification passes again.
- The underlying cause is identified (config drift, failed migration, disk, external dependency, etc.).

