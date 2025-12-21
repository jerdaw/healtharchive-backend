# Phase 6 — Restore Test Procedure (quarterly)

Purpose: prove backups are usable by performing a clean restore and verifying core API behavior.

This procedure is intentionally minimal and public-safe. It does **not** require production secrets to be stored in the repo.

## Preconditions

- A recent database dump exists (from the normal backup routine).
- A temporary restore target is available (local Postgres on the VPS or a separate staging host).
- You have enough disk space to restore the dump.

## Step 1 — Choose a restore target

Options (pick one):

- **Local temporary database** on the VPS (preferred for speed).
- **Staging database** on a separate host.

Record the target in the restore test log.

## Step 2 — Restore the database dump

Follow your standard backup tool instructions. Examples (adjust paths):

```bash
# Example: restore into a temporary database named healtharchive_restore_test
createdb healtharchive_restore_test
psql healtharchive_restore_test < /path/to/latest-backup.sql
```

If your backups are compressed, decompress first (or pipe through `gunzip`).

## Step 3 — Point the backend to the restored DB

Run API checks against the restored DB by temporarily overriding
`HEALTHARCHIVE_DATABASE_URL`. Example:

```bash
export HEALTHARCHIVE_DATABASE_URL="postgresql+psycopg://.../healtharchive_restore_test"
/opt/healtharchive-backend/.venv/bin/alembic current
```

This confirms the restored schema is usable.

## Step 4 — Run minimal verification checks

Run these against the restored DB:

- `GET /api/health` (DB check must be `ok`)
- `GET /api/stats` (counts should be non-zero)
- `GET /api/sources` (sources list should load)

If you need a quick CLI-only check, run:

```bash
/opt/healtharchive-backend/.venv/bin/ha-backend stats
```

## Step 5 — Record results

Use `restore-test-log-template.md` and record:

- date + operator,
- backup source used,
- restore target,
- pass/fail,
- any anomalies or follow-up actions.

## Step 6 — Clean up

Remove the temporary database when done:

```bash
dropdb healtharchive_restore_test
```

If you used a staging host, remove any temporary credentials or files.

