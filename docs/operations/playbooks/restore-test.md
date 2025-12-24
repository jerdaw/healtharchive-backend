# Restore test playbook (quarterly)

Goal: prove backups are usable by performing a restore and minimal API checks.

Canonical reference:

- `../restore-test-procedure.md`

## Procedure (high level)

1. Follow `../restore-test-procedure.md`.
2. Record results using the template:
   - `../restore-test-log-template.md`
3. Store the public-safe log on the VPS:
   - `/srv/healtharchive/ops/restore-tests/`

## What “done” means

- A dated restore-test log exists under `/srv/healtharchive/ops/restore-tests/`.
- Core API checks against the restored DB succeed (health, stats, sources).
