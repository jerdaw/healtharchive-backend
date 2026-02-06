# Incident: API search/changes 500 due to missing dedupe migration (2026-02-06)

Status: closed

## Metadata

- Date (UTC): 2026-02-06
- Severity (see `operations/incidents/severity.md`): sev2
- Environment: production
- Primary area: api
- Owner: jerdaw
- Start (UTC): 2026-02-06T14:55:33Z
- End (UTC): 2026-02-06T15:05:15Z

---

## Summary

After deploying a backend change that referenced `Snapshot.deduplicated`, production API endpoints `/api/search` and `/api/changes` returned HTTP 500 because the Postgres schema was missing the new `snapshots.deduplicated` column. The issue was detected by the deploy verification script and confirmed via loopback repro + API logs. Recovery was completed by adding the missing Alembic migration and re-deploying, which applied the migration and restored API functionality.

## Impact

- User-facing impact: `/api/search` and `/api/changes` returned 500 during the incident window.
- Internal impact: deploy verification failed; required a follow-up deploy.
- Data impact:
  - Data loss: no
  - Data integrity risk: low (read queries failing, not silent corruption)
  - Recovery completeness: complete
- Duration: ~10 minutes

## Detection

- Detected via deploy verification (`scripts/verify_public_surface.py`) failing on `/api/search` and `/api/changes`.
- Confirmed via loopback repro and `journalctl -u healtharchive-api` showing:
  - `psycopg.errors.UndefinedColumn: column snapshots.deduplicated does not exist`

## Timeline (UTC)

- 2026-02-06T14:55:33Z — Deploy verification reports `/api/search` 500; loopback repro confirms 500.
- 2026-02-06T14:55:34Z — API logs show UndefinedColumn error for `snapshots.deduplicated`.
- 2026-02-06T15:04:45Z — Follow-up deploy pulls migration change and runs Alembic upgrade.
- 2026-02-06T15:05:15Z — Loopback `/api/search` and `/api/changes` return 200; public API verification passes.

## Root cause

- Immediate trigger: application code queried `snapshots.deduplicated` before the corresponding migration existed/applied.
- Underlying cause(s): the deploy introduced a schema-dependent feature without an accompanying migration in the same deployable unit.

## Contributing factors

- The public surface verifier correctly detected the regression, but it was run after the first deploy had already restarted the API.

## Resolution / Recovery

- Added the missing Alembic migration for dedupe schema:
  - `alembic/versions/0014_snapshot_deduplication.py`
- Re-deployed backend so Alembic applied the migration and the API restarted cleanly.

## Post-incident verification

- Local on VPS:
  - `curl -sS -i "http://127.0.0.1:8001/api/search?pageSize=1" | head`
  - `curl -sS -i "http://127.0.0.1:8001/api/changes?pageSize=1" | head`
- Public surface:
  - `python3 ./scripts/verify_public_surface.py --api-base https://api.healtharchive.ca --frontend-base https://www.healtharchive.ca --skip-frontend`

## Action items (TODOs)

- [ ] Add a lightweight CI guard that fails if an API query references a missing column in the test DB schema (owner=jerdaw, priority=medium, due=2026-03)
- [ ] Update the dedupe feature checklist to explicitly require an Alembic migration in the same PR (owner=jerdaw, priority=low, due=2026-03)

## References / Artifacts

- Deploy script: `scripts/vps-deploy.sh`
- Public verifier: `scripts/verify_public_surface.py`
- Migration: `alembic/versions/0014_snapshot_deduplication.py`
