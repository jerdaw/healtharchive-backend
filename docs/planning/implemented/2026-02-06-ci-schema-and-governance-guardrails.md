# CI Schema and Governance Guardrails (Implemented 2026-02-06)

**Status:** Implemented | **Scope:** Prevent schema-related API regressions and keep merges low-risk with lightweight enforcement.

## Outcomes

- Added schema parity tests that exercise real query paths:
  - `tests/test_ci_schema_parity.py`
- Added a migration-required guard for PRs (diff-driven; hard-fail with an exceptions file + expiry):
  - `scripts/ci_migration_guard.py`
  - `.github/migration-guard-exceptions.txt`
  - `tests/test_ci_migration_guard.py`
- Wired these into the fast CI gate without making it burdensome:
  - `Makefile` targets used by `make ci`
  - `.github/workflows/backend-ci.yml` PR behavior
- Documented solo-dev branch protection expectations and required checks:
  - `docs/operations/monitoring-and-ci-checklist.md`
  - `docs/deployment/production-rollout-checklist.md`

## Canonical Docs Updated

- `docs/development/playbooks/database-migrations.md`
- `docs/operations/monitoring-and-ci-checklist.md`
- `docs/deployment/production-rollout-checklist.md`

## Validation

- `make ci` remains green and includes the guardrails.

## Historical Context

Detailed implementation narrative is preserved in git history.
