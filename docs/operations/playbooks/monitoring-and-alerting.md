# Monitoring + alerting playbook (operators)

Goal: detect user-visible outages and silent automation failures with low noise.

Canonical reference:

- `../monitoring-and-ci-checklist.md`

## External uptime monitors (required)

Ensure monitors exist for:

- `https://api.healtharchive.ca/api/health`
- `https://www.healtharchive.ca/archive`
- `https://replay.healtharchive.ca/` (only if you rely on replay)

After changes, you can smoke-test from any machine with internet:

- `healtharchive-backend/scripts/smoke-external-monitors.sh`

## “Timer ran” monitoring (optional, recommended)

If you want alerts when systemd timers stop running:

1. Create checks in your Healthchecks provider.
2. Store ping URLs only on the VPS:
   - `/etc/healtharchive/healthchecks.env` (root-owned)
3. Keep the unit templates installed/updated on the VPS:
   - `sudo ./scripts/vps-install-systemd-units.sh --apply --restart-worker`

## What “done” means

- External monitors are green and alert routing is confirmed.
- If enabled, Healthchecks pings are configured without committing URLs to git.
- If you use internal Prometheus-based alerts (Phase 8), Alertmanager is configured and test alerts deliver:
  - `observability-alerting.md`
