# 2026-02-06: CI Schema and Governance Guardrails

**Plan Version**: v1.3
**Status**: Implemented (Phases 0-4 complete)
**Scope**: Prevent schema-related production API regressions and harden merge governance.
**Batched items**: #1, #2, #10

## Implementation Progress

- **Phase 1**: Implemented in repository.
  - Added Alembic-backed schema parity test for `/api/search` and `/api/changes`:
    - `tests/test_ci_schema_parity.py`
  - Wired guard into fast CI gate via `make ci`:
    - `Makefile` (`test-fast`)
  - Added developer playbook guidance for running schema parity checks locally:
    - `docs/development/playbooks/database-migrations.md`
- **Phase 2**: Implemented in repository.
  - Added migration-required diff guard script:
    - `scripts/ci_migration_guard.py`
  - Added unit coverage for guard heuristics:
    - `tests/test_ci_migration_guard.py`
  - Wired PR-only migration guard into CI workflow:
    - `.github/workflows/backend-ci.yml`
  - Added local reproducible Make target:
    - `Makefile` (`migration-guard`)
  - Updated PR checklist and migration playbook guidance:
    - `.github/pull_request_template.md`
    - `docs/development/playbooks/database-migrations.md`
- **Phase 0**: Implemented via design freeze decisions and rollout choices.
  - Enforcement mode established as hard-fail in PR CI for migration-required guard.
  - Branch protection check scope finalized for solo-dev profile:
    - Required check: `Backend CI / test`
    - Non-required by design: `Backend CI / e2e-smoke`, `Backend CI (Full) / test-full`
- **Phase 3**: Implemented (docs + GitHub UI policy recorded).
  - Updated governance docs with exact branch/ruleset/check settings:
    - `docs/operations/monitoring-and-ci-checklist.md`
    - `docs/deployment/production-rollout-checklist.md`
  - Recorded branch-protection evidence snapshot in ops checklist:
    - `docs/operations/monitoring-and-ci-checklist.md` (§3.2)
- **Phase 4**: Implemented (stabilization + exception process).
  - Tuned migration guard with temporary exception-rule support and expiry enforcement:
    - `scripts/ci_migration_guard.py`
    - `.github/migration-guard-exceptions.txt`
  - Added tests for exception matching and expiry constraints:
    - `tests/test_ci_migration_guard.py`
  - Documented false-positive handling + cleanup expectations:
    - `docs/development/playbooks/database-migrations.md`
    - `docs/operations/monitoring-and-ci-checklist.md`
  - Added phase-complete note for ongoing burn-in:
    - monthly review of migration guard exceptions and false-positive trend.

## Current State Summary

HealthArchive has strong baseline CI (`.github/workflows/backend-ci.yml`) and a deploy-time public-surface verifier (`scripts/verify_public_surface.py`). Following the February 6, 2026 incident, migration parity and migration-required guardrails are now in PR CI, and solo-dev branch protection settings have been documented with concrete required checks.

Relevant current assets:

- CI gates:
  - `.github/workflows/backend-ci.yml` (`make ci` on PR and push)
  - `.github/workflows/backend-ci-full.yml` (nightly full checks)
- Deploy verifier:
  - `scripts/verify_public_surface.py`
- Migration workflow docs:
  - `docs/development/playbooks/database-migrations.md`
- Governance checklist docs:
  - `docs/operations/monitoring-and-ci-checklist.md`
- PR template:
  - `.github/pull_request_template.md`

### Key Unknowns

- Whether any non-standard release flows bypass normal PR validation.

### Assumptions

- Mainline changes may land via direct push or PR in current solo-dev mode.
- Adding a PR-time schema guard will not affect the running crawl because it only changes CI/docs/process.
- Operator capacity supports short-term manual branch-protection setup in GitHub UI.

## Goals

- Catch missing migration issues before merge.
- Make migration expectations explicit and enforceable in PR workflow.
- Reduce reliance on deploy-time discovery for schema/query mismatches.
- Document and standardize branch protection and required checks.

## Non-Goals

- Refactoring API query logic.
- Replacing Alembic tooling.
- Introducing a separate staging environment.

## Constraints

- Must not disturb active crawl operations.
- Must stay compatible with existing `make ci` ergonomics.
- Must keep CI runtime predictable (avoid large PR latency jumps).

## Phased Implementation Plan

### Phase 0: Baseline and Design Freeze

**Goal**: Define exact enforcement boundaries before workflow changes.

**Tasks**:

1. Inventory CI runtime and current failure signal quality (PR and nightly).
2. Define schema-guard contract:
   - What endpoints/queries are exercised.
   - What constitutes fail/pass.
3. Decide enforcement mode by check:
   - Hard fail vs advisory for first rollout.

**Deliverables**:

- Written CI guard spec in this plan (updated from proposed to approved details).
- Agreed list of required checks for branch protection.

**Validation**:

- Review sign-off from maintainer(s) on guard scope and check list.

### Phase 1: Migration-Parity API Guard in CI

**Goal**: Ensure API query paths run against an Alembic-built schema, not only ORM-created tables.

**Tasks**:

1. Add a focused schema-parity test or script (new test module under `tests/` or script under `scripts/`) that:
   - Creates a fresh temp DB.
   - Runs `alembic upgrade head`.
   - Executes minimal API query paths covering schema-sensitive endpoints (`/api/search`, `/api/changes`, and related read paths).
2. Integrate this guard into PR CI workflow (`.github/workflows/backend-ci.yml`).
3. Add local developer command entrypoint (Make target or documented command) for reproducibility.

**Deliverables**:

- New schema-parity CI check that fails on missing-column regressions.
- Updated CI workflow configuration.
- Updated developer guidance for running the check locally.

**Validation**:

- New tests pass on clean branch.
- Guard fails in a simulated missing-migration scenario.
- `make ci` remains green and runtime increase is acceptable.

### Phase 2: Migration-Required PR Guard

**Goal**: Enforce "schema-changing code must ship with migration" as a first-class rule.

**Tasks**:

1. Add a lightweight PR guard script to detect likely schema-changing diffs without matching Alembic changes.
2. Wire guard into PR workflow with clear failure messages and remediation guidance.
3. Update `.github/pull_request_template.md` with explicit migration checkbox and link to `docs/development/playbooks/database-migrations.md`.

**Deliverables**:

- Automated migration-required check in CI.
- PR template update with explicit migration checklist.

**Validation**:

- Guard passes on docs-only and non-schema PRs.
- Guard fails on synthetic model/query-only schema change without migration.
- Guard passes once migration file is added.

### Phase 3: Branch Protection and Governance Hardening

**Goal**: Ensure required checks actually block merges.

**Tasks**:

1. Update governance docs with exact required checks and branch rules:
   - `docs/operations/monitoring-and-ci-checklist.md`
   - optionally `docs/deployment/production-rollout-checklist.md` (governance verification section).
2. Apply branch protection settings in GitHub UI:
   - PR required before merge.
   - Required status checks (including new schema/migration checks).
   - Dismiss stale approvals on new commits (recommended).
3. Record setup evidence in ops notes for repeatability.

**Deliverables**:

- Updated governance documentation with concrete check names.
- Branch protection configured and verified.

**Validation**:

- Test PR confirms blocked merge when required checks fail.
- Monitoring checklist reflects current repo settings.

### Phase 4: Stabilization and Signal Tuning

**Goal**: Reduce false positives/noise while preserving strict safety.

**Tasks**:

1. Review first 1-2 weeks of CI failures.
2. Tighten or refine heuristic paths in migration-required guard if needed.
3. Document known exceptions and approved override process.

**Deliverables**:

- Tuned guard logic.
- Short ops/developer note on exception handling.

**Validation**:

- No unexplained false-positive trend.
- No repeated post-deploy schema regressions of this class.

## Dependencies

- Alembic migration flow remains canonical (`alembic upgrade head`).
- GitHub Actions and branch-protection administration access.
- Maintainer agreement on required-check strictness.

## Risks and Mitigations

- Risk: CI runtime increases materially.
  - Mitigation: keep schema parity checks minimal and targeted; profile runtime in Phase 0/1.
- Risk: Migration-required heuristic has false positives.
  - Mitigation: start with conservative detector and clear override/update path.
- Risk: Governance docs drift from actual GitHub settings.
  - Mitigation: require quarterly checklist verification already in ops cadence.

## Progress Validation Framework

Per phase, progress is considered complete only when both are true:

1. Technical artifact exists (tests/scripts/workflow/docs).
2. Verification evidence is captured (passing CI, synthetic failing case, or policy-setting confirmation).

## Timeline and Milestones

Assuming one maintainer, no blockers, and normal review cadence:

- Milestone A (Days 1-2): Phase 0 complete (guard spec finalized).
- Milestone B (Days 3-5): Phase 1 merged (schema-parity CI guard active).
- Milestone C (Days 6-8): Phase 2 merged (migration-required guard + PR template update).
- Milestone D (Days 9-10): Phase 3 completed (branch protection enforced, docs updated).
- Milestone E (Week 3): Phase 4 tuning complete after burn-in.

## Rollout Approach

1. Land schema-parity guard first (highest value, lowest process friction).
2. Land migration-required guard with clear failure messages.
3. Enable/enforce branch protection requirements once checks are stable.
4. Monitor CI failure patterns for 1-2 weeks and tune.

All rollout steps are repo/CI/process only and do not require crawl or VPS service interruption.

## Rollback Approach

- Immediate rollback: revert workflow/guard commits.
- Partial rollback: keep schema-parity guard, temporarily disable strict migration-required guard.
- Governance rollback: temporarily relax required checks in GitHub branch settings if blocking critical emergency patches.

Rollback does not require database or crawl runtime changes.

## Exit Criteria

- PR CI fails reliably when schema-dependent code lacks required migration.
- Branch protection prevents merges when required checks fail.
- Migration policy is explicit in PR template and developer docs.
- No repeat of the 2026-02-06 missing-migration failure mode across at least one release cycle.

## Related Sources

- `docs/operations/incidents/2026-02-06-api-search-changes-500-missing-migration.md`
- `.github/workflows/backend-ci.yml`
- `.github/pull_request_template.md`
- `docs/development/playbooks/database-migrations.md`
- `docs/operations/monitoring-and-ci-checklist.md`
