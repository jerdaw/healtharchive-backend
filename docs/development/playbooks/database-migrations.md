# Playbook: Database migrations (developers)

Purpose: safely introduce schema changes and keep Alembic, tests, and docs aligned.

## When to use

- You changed ORM models (or need to) and the DB schema must change.
- You need to apply migrations in local dev before running the API/worker.

## Preconditions

- You can run the backend locally (see: `../dev-environment-setup.md`).
- `HEALTHARCHIVE_DATABASE_URL` points at the database you intend to modify.
  - Local dev example (SQLite): see `../live-testing.md`.

## Safety / guardrails

- **Never** generate or apply migrations against a database you didn’t intend to modify.
- Prefer testing migrations against a fresh local DB and a “realistic” DB with existing data.
- For production rollout considerations, follow the production runbook:
  - `../../deployment/production-single-vps.md`

## Steps

1) Update ORM models (and any related code).
2) Generate a migration:
   - `alembic revision --autogenerate -m "describe change"`
3) Review the generated migration file under `alembic/versions/`.
   - Ensure it matches the intended change (constraints, nullable, defaults, indexes).
4) Apply migrations locally:
   - `alembic upgrade head`
5) Run the test suite:
   - `make ci`
6) Run the schema-parity guard explicitly (recommended for schema-sensitive API work):
   - `pytest -q tests/test_ci_schema_parity.py`
7) Run the migration-required guard against your branch diff (recommended before opening PR):
   - `make migration-guard MIGRATION_GUARD_BASE=origin/main MIGRATION_GUARD_HEAD=HEAD`
8) Update docs if the change affects operators or contributors.
9) Commit the migration + any code/docs changes together.

## Temporary exceptions (false-positive handling)

Use this only when `make migration-guard` fails but you have confirmed there is no real persisted schema
change (for example, query code introducing temporary-table SQL that does not alter app schema).

1) Add a narrowly-scoped temporary rule in `.github/migration-guard-exceptions.txt`:
   - format: `path_glob|signal_regex|expires_yyyy-mm-dd|reason`
2) Keep expiry short (the guard enforces max 30 days).
3) Prefer fixing the underlying heuristic quickly and removing the exception.
4) Include the exception rationale in the PR description.

Never use this to bypass a real schema migration requirement.

## Verification (“done” criteria)

- `alembic upgrade head` succeeds on a clean local DB.
- `make ci` passes.
- `tests/test_ci_schema_parity.py` passes for schema-sensitive API/model changes.
- `make migration-guard MIGRATION_GUARD_BASE=origin/main MIGRATION_GUARD_HEAD=HEAD` passes.
- Any new/changed behavior is documented in the appropriate canonical doc (dev/deploy/ops).

## Rollback / recovery (if needed)

- In dev: revert via `alembic downgrade -1` (only when safe for your current DB state).
- In prod: follow the rollback guidance in the deploy/runbook docs; avoid ad-hoc downgrades.

## References

- Local dev flows: `../live-testing.md`
- Production runbook: `../../deployment/production-single-vps.md`
- Alembic config: `../../../alembic.ini`, `../../../alembic/`
