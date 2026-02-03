# Deploy + verify playbook (production VPS)

Goal: deploy a known-good `main` and verify production matches policy.

Canonical references:

- Production runbook: `../../deployment/production-single-vps.md`
- Monitoring/CI gate: `../monitoring-and-ci-checklist.md`
- Baseline drift: `../baseline-drift.md`

## Preconditions

- CI is green on the commit you intend to deploy.
- You are on the production VPS and can `sudo`.

## Procedure

1. Update the repo on the VPS:

   - `cd /opt/healtharchive-backend && git pull`

2. Run the deploy gate (recommended one command):

   - `./scripts/vps-deploy.sh --apply --baseline-mode live`

   This includes:

   - DB migrations
   - service restarts (API always; worker may be skipped during active crawls)
   - baseline drift verification
   - public surface verification

   If your change updates systemd unit templates or Prometheus alert rules, you can
   apply those as part of the deploy:

   - `./scripts/vps-deploy.sh --apply --baseline-mode live --install-systemd-units --apply-alerting`

   Notes:

   - `--apply-alerting` requires alerting to be configured on the VPS (webhook secret present at
     `/etc/healtharchive/observability/alertmanager_webhook_url`).

   If you are updating the replay banner/template or replay service config on a
   single-VPS deployment, include replay restart + banner install:

   - `./scripts/vps-deploy.sh --apply --baseline-mode live --restart-replay`

   Crawl safety:

   - If any jobs are `status=running`, the deploy helper will **skip restarting** `healtharchive-worker`
     by default to avoid SIGTERMing an active crawl.
   - When you need to force a worker restart (only when safe): `./scripts/vps-deploy.sh --apply --baseline-mode live --force-worker-restart`
   - If you want to explicitly keep the worker untouched regardless of job status: `./scripts/vps-deploy.sh --apply --baseline-mode live --skip-worker-restart`

3. If the deploy gate fails:

   - Do **not** retry blindly.
   - Read the failure output:
     - drift report artifacts under `/srv/healtharchive/ops/baseline/`
     - verifier output from `verify_public_surface.py`
   - Fix the underlying mismatch (production state vs policy) or intentionally update policy.

## Quick follow-ups (optional)

- Confirm timers/sentinels posture (if you operate automation):
  - `./scripts/verify_ops_automation.sh`
