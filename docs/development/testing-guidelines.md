# Backend testing guidelines (internal)

This doc describes the **backend** testing expectations and how to run checks locally.

If you want step-by-step “run the app and click it” workflows, use:

- `live-testing.md`

## What CI runs (recommended locally)

From the repo root:

- `make check` (fast CI gate: format check, lint, typecheck, tests)
- `make check-full` (optional: pre-commit, security scan, docs build/lint)

`make check` is intentionally kept low-friction so it can run constantly without blocking development.
Use `make check-full` before deploys or when you want stricter validation.

## End-to-end smoke (public surface)

CI also runs a fast end-to-end smoke check that starts the backend + frontend
locally and verifies user-critical routes (no browser automation):

- `./scripts/ci-e2e-smoke.sh --frontend-dir ../healtharchive-frontend`
  - If the frontend is already built (CI artifact), add: `--skip-frontend-build`

In CI, the smoke check is treated as a post-merge safety net (runs on `main` pushes / manual runs) rather than a PR gate.

## Running subsets

- Unit tests: `pytest`
- One test file: `pytest tests/test_something.py`
- One test: `pytest -k some_keyword`
- Lint + format: `ruff check .` and `ruff format --check .`
- Type-check: `mypy src tests`

## Writing tests

- Put tests in `tests/` and prefer plain `pytest` tests (no custom harness).
- Keep tests deterministic:
  - avoid real network calls
  - avoid wall-clock dependencies
  - avoid global state between tests
- If you add a new API route, add at least one test that exercises the route and asserts the key behavior.
- If you change DB behavior, prefer tests that set up a temporary DB using the existing test fixtures/patterns.

## Scope (what belongs in tests vs scripts)

- Application behavior belongs in `tests/`.
- VPS automation scripts under `scripts/` should stay simple and safe; when logic grows (parsing, policy evaluation), prefer moving that logic into a small Python module that can be tested.
