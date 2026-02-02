# Storage Watchdog Unmount Filter Bug — 2026-02-02

**Status**: Resolved
**Severity**: Medium (automation broken, manual recovery needed)
**Detection**: Manual observation during routine crawl status check
**Duration**: ~9 days (2026-01-24 last successful watchdog run → 2026-02-02 fix deployed)

## Summary

The storage hotpath auto-recover watchdog (`vps-storage-hotpath-auto-recover.py`) had a bug that prevented it from unmounting stale SSHFS mounts. The watchdog could detect stale mounts (Errno 107) but silently skipped them during the unmount phase due to an overly-strict filter condition. This required manual intervention for every stale mount occurrence.

## Root Cause

The watchdog's stale mount detection calls `_get_mount_info()` to retrieve mount details. For stale FUSE mounts (Errno 107), `_get_mount_info()` often returns `None` or incomplete data because the mount endpoint is inaccessible.

The filter logic at lines 1041-1053 (dry-run) and 1184-1206 (apply) checked:
```python
if target != path:
    continue  # Skip this mount
```

When `_get_mount_info()` returned `None`, the `target` variable became an empty string (`""`), making `target != path` always `True`. This caused **all stale mounts to be filtered out**, leaving the `stale_mountpoints` list empty, and the unmount step never executed.

## Why It Wasn't Caught Earlier

1. **Stale mounts are rare** - The watchdog was only enabled after the initial incident on 2026-01-08
2. **Manual recovery worked** - Operators could manually `umount -l` to fix the issue
3. **Dry-run was misleading** - The dry-run showed intent to unmount but the apply phase diverged
4. **No test coverage** - No integration test simulated stale mount scenarios in the watchdog

## Resolution

**Fix deployed**: 2026-02-02 03:00 UTC (commit `5a48b22`)

Changed the filter logic to accept paths where **either**:
1. Confirmed mountpoint (`target == path` from findmnt), **OR**
2. Errno 107 detected (strong evidence of stale FUSE mount)

The Errno 107 detection itself is sufficient evidence that a path under `jobs_root` is a stale bind mount, since normal directories don't return Errno 107.

**Changes**:
- Updated stale mount filter in both dry-run and apply phases
- Improved dry-run output to show errno when mount info unavailable
- Fixed post-check to prioritize readability over mount info

## Verification

- All 7 storage hotpath tests pass
- Full test suite: 431 tests passed
- Watchdog timers confirmed active (running every 1 minute)
- Manual test: Successfully detected and resolved current stale mounts

## Impact

**Before fix**:
- Watchdog detected stale mounts but failed to unmount them
- Manual `umount -l` required for every stale mount occurrence
- Last successful watchdog run: 2026-01-24 06:28 UTC

**After fix**:
- Watchdog automatically detects and unmounts stale mounts
- No manual intervention needed
- Confirmed healthy run: 2026-02-02 02:44 UTC (detected 0 stale targets)

## Related Incidents

- [2026-01-08 Storage Hotpath SSHFS Stale Mount](./2026-01-08-storage-hotpath-sshfs-stale-mount.md) - Initial incident
- [2026-01-24 Infra Error 107 Hotpath Thrash](./2026-01-24-infra-error-107-hotpath-thrash-and-worker-stop.md) - Retry storm incident

## Related Implementation Plans

- [2026-01-08 Storage Box Stale Mount Recovery](../planning/implemented/2026-01-08-storagebox-sshfs-stale-mount-recovery-and-integrity.md) - Initial watchdog implementation
- [2026-01-24 Infra Error and Storage Hotpath Hardening](../planning/implemented/2026-01-24-infra-error-and-storage-hotpath-hardening.md) - Watchdog improvements
- [2026-02-01 Operational Resilience Improvements](../planning/implemented/2026-02-01-operational-resilience-improvements.md) - Latest hardening work

## Lessons Learned

1. **Test stale mount scenarios** - Add integration test that simulates Errno 107 conditions
2. **Verify dry-run/apply parity** - Ensure dry-run accurately predicts apply behavior
3. **Monitor automation health** - The watchdog was failing silently for 9 days
4. **Trust detection signals** - When Errno 107 is explicitly detected, trust it over missing mount info

## Follow-up Actions

- [ ] Add integration test for stale mount recovery scenarios
- [ ] Add alerting if watchdog runs complete but `last_apply_ok=0` for >24 hours
- [ ] Document expected failure modes in storage watchdog playbook
