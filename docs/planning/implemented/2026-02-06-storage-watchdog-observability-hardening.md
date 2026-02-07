# Storage Watchdog Observability Hardening (Implemented 2026-02-06)

**Status:** Implemented | **Scope:** Improve confidence and signal quality for Storage Box hot-path auto-recovery (Errno 107).

## Outcomes

- Added explicit tests for hot-path auto-recover behavior and failure modes:
  - `tests/test_ops_storage_hotpath_auto_recover.py`
- Added persistent failed-apply alerting (startup-safe, low-noise default severity):
  - `ops/observability/alerting/healtharchive-alerts.yml` (`HealthArchiveStorageHotpathApplyFailedPersistent`)
  - `tests/test_ops_alert_rules.py`
- Added burn-in tooling for evidence capture and a clean gate:
  - `scripts/vps-storage-watchdog-burnin-report.py`
  - `tests/test_ops_storage_watchdog_burnin_report.py`
- Added optional daily snapshot scheduling for burn-in (read-only):
  - `docs/deployment/systemd/healtharchive-storage-watchdog-burnin-snapshot.service`
  - `docs/deployment/systemd/healtharchive-storage-watchdog-burnin-snapshot.timer`
  - `scripts/vps-storage-watchdog-burnin-snapshot.sh`

## Canonical Docs Updated

- `docs/operations/monitoring-and-alerting.md`
- `docs/operations/thresholds-and-tuning.md`
- `docs/operations/playbooks/storage/storagebox-sshfs-stale-mount-recovery.md`
- `docs/operations/playbooks/storage/storagebox-sshfs-stale-mount-drills.md`
- `docs/operations/playbooks/validation/automation-maintenance.md`

## Validation

- `make ci` remains green.
- Burn-in gate command returns exit 0 when no unresolved issues exist:
  - `python3 scripts/vps-storage-watchdog-burnin-report.py --window-hours 168 --require-clean`

## Historical Context

Detailed implementation narrative and tuning is preserved in git history.
