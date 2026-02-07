# Automation maintenance playbook (systemd timers)

Goal: keep automation boring, observable, and explicitly controlled.

Canonical references:

- systemd unit templates + enable/rollback: `../../../deployment/systemd/README.md`
- Verification ritual: `../../automation-verification-rituals.md`

## Install/update templates (after repo updates)

On the VPS:

- `cd /opt/healtharchive-backend`
- `sudo ./scripts/vps-install-systemd-units.sh --apply --restart-worker`

## Bootstrap ops directories (one-time)

If `/srv/healtharchive/ops/` is not prepared:

- `sudo ./scripts/vps-bootstrap-ops-dirs.sh`

## Enablement controls (sentinel files)

Automation is intentionally gated by sentinel files under `/etc/healtharchive/`.

Follow the enable/rollback steps in `../../../deployment/systemd/README.md`.

## Verify posture

- `./scripts/verify_ops_automation.sh`
- Spot-check logs:
  - `journalctl -u <service> -n 200`

## Storage watchdog cadence (monthly)

For stale-mount watchdog reliability, include this in the periodic automation review:

1. Re-run a safe dry-run watchdog drill:
   - `../storage/storagebox-sshfs-stale-mount-drills.md` (Section 1)
2. Re-run the safe persistent failed-apply alert-condition drill:
   - `../storage/storagebox-sshfs-stale-mount-drills.md` (Section 2)
3. Review watchdog state + key metrics:
   - `/srv/healtharchive/ops/watchdog/storage-hotpath-auto-recover.json`
   - `healtharchive_storage_hotpath_auto_recover_last_apply_ok`
   - `healtharchive_storage_hotpath_auto_recover_apply_total`
4. If `HealthArchiveStorageHotpathApplyFailedPersistent` fired recently, follow:
   - `../storage/storagebox-sshfs-stale-mount-recovery.md`

Burn-in helper command (safe, read-only summary):

- `python3 scripts/vps-storage-watchdog-burnin-report.py --window-hours 168 --json`
