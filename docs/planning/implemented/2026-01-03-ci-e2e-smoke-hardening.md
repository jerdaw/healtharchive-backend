# CI e2e smoke hardening (implementation plan)

Status: **implemented** (2026-01-03)

## Goal

Improve the end-to-end CI smoke coverage so it is:

- **Higher-signal** (catches “200 but broken/miswired” regressions),
- **Less flaky** (no fixed ports, better readiness, robust teardown),
- **Easier to debug** (logs uploaded as CI artifacts on failure),
- **Faster** (avoid duplicate frontend builds in CI where possible),
- **Bilingual-aware** (checks both EN unprefixed routes and `/fr/...` routes).

This work builds on the existing smoke harness:

- `scripts/ci-e2e-seed.py`
- `scripts/ci-e2e-smoke.sh`
- `scripts/verify_public_surface.py`
- CI jobs in both repos.

## Scope

### Reliability improvements

- Remove fixed ports in the smoke runner; select free ports dynamically.
- Improve readiness checks:
  - Backend: `GET /api/health`
  - Frontend: `GET /archive` and `GET /fr/archive`
- Ensure teardown kills process groups and always prints useful logs on failure.

### Higher-signal assertions

In `scripts/verify_public_surface.py`:

- Add minimal API contract checks (JSON shape and key invariants).
- Add minimal frontend HTML assertions:
  - For `/archive` and `/fr/archive`, verify a stable `<title>` marker.
  - For `/snapshot/{id}` and `/fr/snapshot/{id}`, verify the page contains the snapshot title
    returned by the backend API (seeded deterministically in CI).

### CI diagnostics and runtime

- Upload smoke logs (and optionally the tiny seeded artifacts) as GitHub Actions
  artifacts on failure.
- Reduce duplicated frontend builds:
  - Frontend CI should build once in the main job and reuse that build for the
    e2e smoke job (artifact download), rather than building twice.

### Guardrails (tests)

- Add a small backend test that prevents accidental regression of EN+FR frontend
  page coverage in the verifier.

## Non-goals

- Browser automation (Playwright/Cypress).
- Replay (pywb) service validation in CI (smoke uses `--skip-replay`).
- Search relevance quality evaluation (golden-query harness is separate).

## Implementation steps

1. Update `scripts/ci-e2e-smoke.sh`:
   - Add dynamic port selection.
   - Add `--skip-frontend-build`.
   - Improve readiness, teardown, and failure log reporting.
2. Update `scripts/verify_public_surface.py`:
   - Strengthen API contract checks.
   - Add minimal HTML assertions for the public pages.
3. CI workflows:
   - Upload smoke logs as artifacts on failure (backend + frontend repos).
   - Frontend repo: reuse the build output for e2e smoke instead of rebuilding.
4. Add tests:
   - Add a test ensuring verifier includes both EN and FR public pages.
5. Update docs:
   - Backend: `docs/operations/monitoring-and-ci-checklist.md`
   - Backend: `docs/development/testing-guidelines.md`
   - Frontend: `healtharchive-frontend/docs/development/bilingual-dev-guide.md` (if needed)
6. Run:
   - `healtharchive-backend: make check`
   - `healtharchive-frontend: npm run check`
   - `healtharchive-backend: ./scripts/ci-e2e-smoke.sh --frontend-dir ../healtharchive-frontend`
7. Archive this plan under `docs/planning/implemented/` and update indices.

## Acceptance criteria

- The smoke script is robust to port collisions (no fixed ports).
- The verifier fails on “wrong API / empty snapshot page” even if the HTTP status is 200.
- CI uploads logs on smoke failures.
- Frontend CI does not rebuild Next twice just to run e2e smoke.
- Tests protect EN+FR surface coverage.
