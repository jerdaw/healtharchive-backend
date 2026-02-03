# Operator responsibilities (must-do list)

Goal: keep HealthArchive operating safely and predictably over time.

This file is intentionally brief; it points to canonical docs when you need details.

## Always (every deploy)

- Treat green `main` as the deploy gate (run checks, push, wait for CI).
  - Canonical: `../../monitoring-and-ci-checklist.md`
- Run the deploy helper on the VPS (safe deploy + verify):
  - `cd /opt/healtharchive-backend && ./scripts/vps-deploy.sh --apply --baseline-mode live`
  - Playbook: `deploy-and-verify.md`
- If the deploy script fails: don’t retry blindly.
  - Read the drift report and verifier output and fix the underlying mismatch.
  - Canonical: `../../baseline-drift.md`
- If you had to do manual steps or discovered drift, update the canonical runbook/playbook so the next operator can follow reality.
  - Canonical: `../../ops-cadence-checklist.md`

## Ongoing automation maintenance

- Keep systemd unit templates installed/updated on the VPS after repo updates:
  - `sudo ./scripts/vps-install-systemd-units.sh --apply --restart-worker`
  - Playbook: `../validation/automation-maintenance.md`
- Maintain sentinel files under `/etc/healtharchive/` (explicit automation on/off controls).
  - Canonical: `../../../deployment/systemd/README.md`
- If you enable Healthchecks pings:
  - keep ping URLs only in the root-owned VPS env file (never in git):
    - `/etc/healtharchive/healthchecks.env`
  - Note: this file may contain both legacy `HC_*` variables (DB backup + disk)
    and newer `HEALTHARCHIVE_HC_PING_*` variables (systemd unit templates).
  - Canonical: `../../monitoring-and-ci-checklist.md`

## Quarterly ops cadence (sustainability loop)

- Run a restore test and write a public-safe log entry.
  - Playbook: `../validation/restore-test.md`
- Verify dataset release checksum integrity (`SHA256SUMS`).
  - Playbook: `../validation/dataset-release.md`
- Add an adoption signals entry (links + aggregate counts only).
  - Playbook: `../external/adoption-signals.md`
- Confirm timers are still enabled and not silently failing.
  - `./scripts/verify_ops_automation.sh` and spot-check `journalctl`
  - Playbook: `../validation/automation-maintenance.md`
- Do a quick docs drift skim (production runbook + incident response) and fix anything stale.
  - Canonical: `../../ops-cadence-checklist.md`

## Security posture (always)

- Keep secrets (admin token, DB URL, ping URLs) out of git forever.
  - Canonical: `../../../deployment/production-single-vps.md`
- Maintain HSTS at Caddy for `api.healtharchive.ca`.
  - Canonical: `../../../deployment/hosting-and-live-server-to-dos.md`
- Maintain a strict CORS allowlist; treat widening it as a deliberate security decision.
  - Canonical: `../../../deployment/environments-and-configuration.md`
