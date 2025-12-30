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

## When jobs are stuck (crawl/indexing pipeline)

If the worker is running but jobs never advance, check for a job stuck in
`status=running` after a reboot or unexpected termination.

0. Load production environment (so the CLI targets Postgres):
   - `set -a; source /etc/healtharchive/backend.env; set +a`
1. Inspect recent jobs:
   - `/opt/healtharchive-backend/.venv/bin/ha-backend list-jobs --limit 50`
2. Recover stale running jobs (safe dry-run first):
   - `/opt/healtharchive-backend/.venv/bin/ha-backend recover-stale-jobs --older-than-minutes 180`
   - Apply (sets `status=retryable`): `/opt/healtharchive-backend/.venv/bin/ha-backend recover-stale-jobs --older-than-minutes 180 --apply`
3. Verify the worker picks them up:
   - `sudo journalctl -u healtharchive-worker -n 200 --no-pager`

## If you need to deploy a fix

- Follow `deploy-and-verify.md` (don’t skip the deploy gate).

## What “done” means

- The public surface verification passes again.
- The underlying cause is identified (config drift, failed migration, disk, external dependency, etc.).
