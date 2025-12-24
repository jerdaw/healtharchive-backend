# Automation maintenance playbook (systemd timers)

Goal: keep automation boring, observable, and explicitly controlled.

Canonical references:

- systemd unit templates + enable/rollback: `../../deployment/systemd/README.md`
- Verification ritual: `../automation-verification-rituals.md`

## Install/update templates (after repo updates)

On the VPS:

- `cd /opt/healtharchive-backend`
- `sudo ./scripts/vps-install-systemd-units.sh --apply --restart-worker`

## Bootstrap ops directories (one-time)

If `/srv/healtharchive/ops/` is not prepared:

- `sudo ./scripts/vps-bootstrap-ops-dirs.sh`

## Enablement controls (sentinel files)

Automation is intentionally gated by sentinel files under `/etc/healtharchive/`.

Follow the enable/rollback steps in `../../deployment/systemd/README.md`.

## Verify posture

- `./scripts/verify_ops_automation.sh`
- Spot-check logs:
  - `journalctl -u <service> -n 200`
