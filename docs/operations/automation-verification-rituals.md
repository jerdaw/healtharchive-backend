# Automation Verification Rituals (internal)

Use these checks before claiming automation is “on”.

## systemd timers

- One-command posture check (recommended): `./scripts/verify_ops_automation.sh`
- Diff-friendly JSON summary (optional): `./scripts/verify_ops_automation.sh --json | python3 -m json.tool`
- JSON-only artifact (optional): `./scripts/verify_ops_automation.sh --json-only > /srv/healtharchive/ops/automation/posture.json`
- Strict checks (optional):
  - all timers present: `./scripts/verify_ops_automation.sh --require-all-present`
  - all timers enabled: `./scripts/verify_ops_automation.sh --require-all-enabled`
- `systemctl is-enabled <timer>` (should be `enabled`)
- sentinel file exists under `/etc/healtharchive/*enabled`
- `systemctl list-timers --all | grep healtharchive-` (shows next/last run)
- `journalctl -u <service> -n 200` (shows last run success)

## Posture snapshots (optional)

- Keep dated JSON under `/srv/healtharchive/ops/automation/` so you can diff over time.
- If the directory is missing, run: `sudo ./scripts/vps-bootstrap-ops-dirs.sh` (idempotent).
- Diff examples:
  - `diff -u <(python3 -m json.tool < old.json) <(python3 -m json.tool < new.json)`

## Dataset releases

- Confirm GitHub Actions are enabled in `jerdaw/healtharchive-datasets`
- Confirm a release exists for the expected quarter/date
- Download assets and verify: `sha256sum -c SHA256SUMS`

## Restore tests

- Confirm a dated log file exists in `/srv/healtharchive/ops/restore-tests/`
- Ensure it includes: backup source, schema check, API checks, pass/fail, follow-ups
