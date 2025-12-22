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

Follow your standard backup tool instructions. In production, backups are typically created with:

- `pg_dump -Fc` (custom-format dump)

Examples (adjust paths):

```bash
# Example: restore into a temporary database named healtharchive_restore_test_YYYYMMDD
DBNAME="healtharchive_restore_test_$(date -u +%Y%m%d)"

# Pick a backup file (example naming from the production runbook)
BACKUP="/srv/healtharchive/backups/healtharchive_YYYY-MM-DDTHHMMSSZ.dump"

sudo -u postgres createdb -O healtharchive "$DBNAME"

# If backups live under a directory the `postgres` user cannot traverse, copy it first:
TMPDUMP="/var/tmp/${DBNAME}.dump"
sudo install -m 600 -o postgres -g postgres "$BACKUP" "$TMPDUMP"

# Restore the custom-format dump:
sudo -u postgres pg_restore --no-owner --role=healtharchive -d "$DBNAME" "$TMPDUMP"

sudo rm -f "$TMPDUMP"
```

If your backup is plain SQL (not custom-format), you can restore with `psql -f`, but production defaults are custom-format.

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
/opt/healtharchive-backend/.venv/bin/ha-backend check-db
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
