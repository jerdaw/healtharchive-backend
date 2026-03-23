# Incident response playbook (operators)

Goal: restore service safely and capture enough context to prevent repeat incidents.

Canonical references:

- Production runbook: `../../../deployment/production-single-vps.md`
- Monitoring checklist: `../../monitoring-and-ci-checklist.md`
- Service levels: `../../service-levels.md` — for communication commitments and SLOs
- Escalation procedures: `../../escalation-procedures.md`
- Disaster recovery runbook: `../../../deployment/disaster-recovery.md`
- Baseline drift: `../../baseline-drift.md`
- Incident notes (template + where to file): `../../incidents/README.md`
- Ops runbooks (quick response procedures): `../../runbooks/README.md`

## First: start an incident note

As soon as you suspect this is “an incident” (not routine maintenance), start a note so you can record a timeline and the exact recovery steps.

- Create a new file: `docs/operations/incidents/YYYY-MM-DD-short-slug.md`
- Copy the template: `docs/_templates/incident-template.md`
- Pick an initial severity using: `docs/operations/incidents/severity.md`

If you can’t easily edit the repo on the VPS, capture the note in a local scratchpad and copy it into the repo later.

## Operating rule: repo-first remediation

When an incident appears to require a backend behavior change, source-profile
change, scope fix, watchdog change, or CLI reconciliation fix, do not jump
straight from diagnosis into VPS recovery commands.

Use this order instead:

1. Classify the incident first (storage, stale state, scope/config drift, or crawler/site compatibility).
2. If the fix lives in the repo, make the change in the repo first.
3. Commit and push the change.
4. Deploy a pinned ref on the VPS via `deploy-and-verify.md`.
5. Verify the VPS checkout contains the intended change.
6. Only then run reconcile/recover/restart commands that depend on that fix.

This project is an archive, not a just-keep-it-running service. Prefer one
auditable, versioned fix plus one controlled recovery over repeated ad hoc
retries against stale code.

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
2. Decide whether recovery depends on undeployed repo changes:
   - If yes, stop here and follow `deploy-and-verify.md` first.
   - Verify the live checkout contains the intended change before continuing.
3. Recover stale running jobs (safe dry-run first):
   - `/opt/healtharchive-backend/.venv/bin/ha-backend recover-stale-jobs --older-than-minutes 180`
   - Apply (sets `status=retryable`): `/opt/healtharchive-backend/.venv/bin/ha-backend recover-stale-jobs --older-than-minutes 180 --apply`
4. Verify the worker picks them up:
   - `sudo journalctl -u healtharchive-worker -n 200 --no-pager`

## If you need to deploy a fix

- Follow `deploy-and-verify.md` (don’t skip the deploy gate).

## What “done” means

- The public surface verification passes again.
- The underlying cause is identified (config drift, failed migration, disk, external dependency, etc.).
