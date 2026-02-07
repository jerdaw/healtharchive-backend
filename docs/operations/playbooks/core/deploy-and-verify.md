# Deploy + verify playbook (production VPS)

Goal: deploy a known-good `main` and verify production matches policy.

Canonical references:

- Production runbook: `../../../deployment/production-single-vps.md`
- Monitoring/CI gate: `../../monitoring-and-ci-checklist.md`
- Baseline drift: `../../baseline-drift.md`

## Preconditions

- CI is green on the commit you intend to deploy.
- You are on the production VPS and can `sudo`.

## Procedure

1. Update the repo on the VPS:

   - `cd /opt/healtharchive-backend && git pull`

2. Run the deploy gate (recommended one command):

   - `./scripts/vps-deploy.sh --apply --baseline-mode live`

   Recommended wrapper (routine use):

   - `./scripts/vps-hetzdeploy.sh`

   This includes:

   - DB migrations
   - service restarts (API always; worker may be skipped during active crawls)
   - baseline drift verification
   - public surface verification

   If your change updates systemd unit templates or Prometheus alert rules, you can
   apply those as part of the deploy:

   - `./scripts/vps-deploy.sh --apply --baseline-mode live --install-systemd-units --apply-alerting`

   If the public frontend is externally down (e.g., Vercel `402 Payment required`), you can deploy backend-only:

   - `./scripts/vps-hetzdeploy.sh --mode backend-only`

   Optional: install the wrapper outside the repo so it never dirties `/opt/healtharchive-backend`:

   - `sudo ./scripts/vps-install-hetzdeploy.sh --apply`
   - Then run: `hetzdeploy` or `hetzdeploy --mode backend-only`

   Notes:

   - Prefer a real command over an alias; aliases can accidentally persist `set -euo pipefail` in your interactive shell.
   - If `hetzdeploy --mode backend-only` errors with `syntax error near unexpected token`, you probably still have an alias named `hetzdeploy`.
     - Check: `type hetzdeploy`
     - Remove: `unalias hetzdeploy 2>/dev/null || true` and delete the alias line from your shell startup files.
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
