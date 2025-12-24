# Automation Verification Rituals (internal)

Use these checks before claiming automation is “on”.

## systemd timers

- One-command posture check (recommended): `./scripts/verify_ops_automation.sh`
- `systemctl is-enabled <timer>` (should be `enabled`)
- sentinel file exists under `/etc/healtharchive/*enabled`
- `systemctl list-timers --all | grep healtharchive-` (shows next/last run)
- `journalctl -u <service> -n 200` (shows last run success)

## Dataset releases

- Confirm GitHub Actions are enabled in `jerdaw/healtharchive-datasets`
- Confirm a release exists for the expected quarter/date
- Download assets and verify: `sha256sum -c SHA256SUMS`

## Restore tests

- Confirm a dated log file exists in `/srv/healtharchive/ops/restore-tests/`
- Ensure it includes: backup source, schema check, API checks, pass/fail, follow-ups
