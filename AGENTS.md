# AGENTS.md – HealthArchive.ca backend

## Project overview

- Backend for HealthArchive.ca:
  - Orchestrates web crawls via the internal **`archive_tool`** subpackage + Docker.
  - Indexes WARCs into `Snapshot` rows in a relational DB via SQLAlchemy.
  - Exposes public HTTP APIs for the frontend (search, sources, snapshots).
  - Exposes admin and metrics endpoints for operators.
  - Runs a worker loop that:
    - Picks queued jobs,
    - Runs `archive_tool`,
    - Indexes WARCs into snapshots.

Canonical docs to consult first:

- **Documentation Site**: Run `make docs-serve` locally to view the backend docs site (links out to other repos; does not mirror their docs).
- **AI Context**: `docs/llms.txt` (Auto-generated context for agents).
- `mkdocs.yml` – navigation source of truth.
- `docs/architecture.md` – architecture & implementation details.
- `docs/documentation-guidelines.md` – documentation policy and canonical sources.
- `docs/development/live-testing.md` – step-by-step local testing flows.
- `docs/deployment/environments-and-configuration.md` – cross-repo env vars and host matrix.
- `src/archive_tool/docs/documentation.md` – internals of the `archive_tool` crawler CLI.
- `docs/deployment/hosting-and-live-server-to-dos.md` – hosting/env/DNS/CORS checklist.
- `docs/deployment/staging-rollout-checklist.md` – staging rollout steps.
- `docs/deployment/production-rollout-checklist.md` – production rollout steps.
- `docs/operations/monitoring-and-ci-checklist.md` – monitoring, uptime, and CI guidance.
- `docs/operations/monitoring-and-alerting.md` – **NEW (2026):** Critical crawl metrics and alert thresholds strategies.
- `docs/deployment/production-single-vps.md` – current production runbook (Hetzner + Tailscale-only SSH, nightly backups, NAS pull).
- `docs/roadmaps/roadmap.md` – backlog of not-yet-implemented work.
- `docs/roadmaps/implemented/` – historical implementation plans (executed).
- Frontend bilingual/dev architecture: `healtharchive-frontend/docs/development/bilingual-dev-guide.md`

When you’re doing anything beyond tiny local changes, **open those docs and sync your mental model first**.

---

## Dev environment & commands

From the repo root (Python project):

- Create venv (if not already done) – adjust to my actual workflow:

  ```bash
  make venv
  ```

- Run checks (what CI blocks on):

  ```bash
  make ci
  ```

- Optional full suite (slower / stricter):

  ```bash
  make check-full
  ```

- **Documentation**:

  ```bash
  make docs-serve  # View the docs site locally
  make docs-build  # Build static site to site/
  ```

For how to:

- Start the FastAPI app for local dev,
- Run specific CLI flows end-to-end,
- Wire up Docker + `archive_tool`,

→ **Follow `docs/development/live-testing.md` rather than inventing new commands**.

When you add/modify functionality, you should:

- Keep checks passing (`make ci`).
- Prefer adding/adjusting tests close to the code you touch (e.g., API route tests, indexing pipeline tests, worker tests).

---

## Git workflow (commits & pushes)

Default for agentic work: **do not commit or push** unless the human operator explicitly asks.

Guidelines:

- If asked to commit: prefer small, logically grouped commits over big “catch-all” commits.
- Use the existing message style (e.g., `fix: ...`, `docs: ...`, `ops: ...`).
- Run the closest relevant local checks before pushing (usually `make ci`; use `make check-full` before deploys).
- Never commit secrets, `.env` files, or machine-specific artifacts.

---

## Common engineering best practices (as you go)

When you change behavior, do routine hygiene in the same series of commits:

- Update relevant docs (especially runbooks/playbooks under `docs/**`) so operators and future devs can follow the new reality.
- For new procedural docs, use the templates:
  - Runbook: `docs/deployment/runbook-template.md`
  - Playbook: `docs/operations/playbooks/playbook-template.md`
  - Incident note: `docs/operations/incidents/incident-template.md`
  - Decision record: `docs/decisions/decision-template.md`
- **Update Navigation**: When adding new files, add them to `mkdocs.yml` if you want them in the sidebar.
- Add/adjust tests for new behavior and bug fixes to prevent regressions.
  - Critical business logic modules require >80% line coverage.
  - Use `pytest --cov=path/to/module tests/test_module.py` to verify locally.
- Update `.gitignore` when you introduce new local artifacts, generated files, or caches.
- Keep things tidy: remove dead code, unused imports, and accidental debug logging; keep scripts safe to re-run.
- If you introduce new project conventions/workflows, update `AGENTS.md` to reflect them.

---

## Core concepts you must respect

### Data model

- SQLAlchemy models defined under `src/ha_backend/models.py`:

  - `Source` – logical content origin (e.g., `"hc"`, `"phac"`).
  - `ArchiveJob` – a crawl job and its lifecycle (`queued`, `running`, `completed`, `failed`, `indexed`, `index_failed`, etc.).
  - `Snapshot` – a single captured page with URL, capture timestamp, WARC path, language, etc.

If you change models:

- Think through migrations and existing queries/routes.
- Don’t drop/change columns in a way that silently breaks APIs.
- Update Pydantic schemas in `ha_backend/api/schemas.py` (and admin schemas) coherently.

### Job lifecycle / worker

- Job creation via `SourceJobConfig` in `ha_backend/job_registry.py`:

  - Seeds, `name_template`, and `tool_options` live there.

- `ArchiveJob.config` JSON is the **single source of truth** for building `archive_tool` CLI args.
- Worker loop (`ha_backend/worker/main.py`):

  - Picks `queued`/`retryable` jobs.
  - Runs `run_persistent_job(job_id)`.
  - Applies retry semantics.
  - On success, runs `index_job(job_id)`.

If you change job states, retry logic, or indexing behavior, update:

- Worker logic,
- Admin views (status counts, job details),
- Metrics (`/metrics`).

---

## archive_tool integration – internal crawler subpackage

The `archive_tool` package lives under `src/archive_tool/` and is maintained as
part of this repository. It originated as an earlier standalone crawler repo
but should now be treated as an in-tree, first-class component of the backend.

- The backend primarily talks to it via the CLI (`archive-tool` or the
  configured command) and imports a small set of helpers (`archive_tool.state`,
  `archive_tool.utils`) for WARC and state discovery.
- You **may** change and refactor `src/archive_tool/**` when needed, but:

  - Keep the CLI contract used by `ha_backend/jobs.py` and
    `ha_backend/job_registry.py` in sync (flags such as `--seeds`,
    `--name`, `--output-dir`, monitoring/VPN options, `--relax-perms`,
    etc.).
  - If you change how `.archive_state.json` or temp dirs are laid out,
    update WARC discovery (`ha_backend/indexing/warc_discovery.py`) and
    cleanup (`ha_backend/cli.py:cmd_cleanup_job`) at the same time.
  - Preserve or intentionally migrate any behavior relied on by tests under
    `tests/` (worker flows, job status transitions, indexing expectations).

- When in doubt:

  - Treat `ha_backend/jobs.py` and `ha_backend/job_registry.py` as the main
    integration points for CLI construction and configuration.
  - Treat `ha_backend/indexing/warc_discovery.py` and `cmd_cleanup_job` as
    the integration points for WARC/state discovery and cleanup.
  - Add or adjust tests close to the code you change.

The backend and `archive_tool` are expected to evolve together; it is fine for
changes to span both sides as long as they are coherent and tested.

---

## Indexing pipeline (WARCs → Snapshots)

Key pieces:

- `ha_backend/indexing/warc_discovery.py` – use `CrawlState` + `find_all_warc_files` to discover WARCs.
- `ha_backend/indexing/warc_reader.py` – stream HTML records from WARCs.
- `ha_backend/indexing/text_extraction.py` – extract title/text/snippet/language.
- `ha_backend/indexing/mapping.py` – map an archive record to a `Snapshot` ORM instance.
- `ha_backend/indexing/pipeline.py` – `index_job(job_id)` orchestrates the whole indexing step.

If you change indexing:

- Don’t break the assumption that **indexed snapshots reference a WARC path / record that `viewer.py` can replay**.
- Keep per-record errors logged but non-fatal where feasible.
- Keep pagination and performance in mind with large WARC sets.

---

## HTTP API contract

Public routes (for the frontend):

- `GET /api/health` – status and basic stats.
- `GET /api/sources` – summarized counts by source.
- `GET /api/search` – paginated search with `q`, `source`, `page`, `pageSize`.
- `GET /api/snapshot/{id}` – metadata for one snapshot.
- `GET /api/snapshots/raw/{id}` – raw HTML replay.
- `GET /api/usage` – aggregated daily usage metrics for public reporting.
- `GET /api/changes` – change-event feed (edition-aware by default).
- `GET /api/changes/compare` – precomputed diff between adjacent captures.
- `GET /api/changes/rss` – RSS feed for change events.
- `GET /api/exports` – export manifest (formats + limits).
- `GET /api/exports/snapshots` – snapshot metadata export (JSONL/CSV).
- `GET /api/exports/changes` – change event export (JSONL/CSV).
- `GET /api/snapshots/{id}/timeline` – timeline for a page group.
- `POST /api/reports` – public issue report intake.

Admin & metrics:

- `/api/admin/**` – job lists, details, snapshots per job, status counts.
- `/metrics` – Prometheus-style counts, behind `require_admin`.

When you add/modify endpoints:

- Keep public/admin responsibilities separate.
- Maintain existing query semantics:

  - `page >= 1`, `1 <= pageSize <= 100`, etc.
  - Filtering by `source` using `Source.code`.

- Update the relevant Pydantic schemas and tests accordingly.

---

## Security & admin auth

Admin auth is via `require_admin`:

- Controlled by `HEALTHARCHIVE_ENV` and `HEALTHARCHIVE_ADMIN_TOKEN`.
- In `production`/`staging`, a missing admin token should fail closed.
- When the token is set, admin/metrics require either:

  - `Authorization: Bearer <token>`, or
  - `X-Admin-Token: <token>`.

Do **not** weaken this behavior:

- Don’t expose admin endpoints without auth in non-dev environments.
- Don’t log secrets or tokens.

---

## Cleanup & retention

- Cleanup is done per job via `ha-backend cleanup-job --id ID --mode temp`.
- It removes:

  - temp crawl dirs (`.tmp*`),
  - `.archive_state.json`,
    consistent with `archive_tool`’s own cleanup behavior.

- Only safe when jobs are `indexed` or explicitly `index_failed` and not being retried.

Don’t make cleanup more aggressive (e.g. deleting WARCs or ZIMs) without carefully updating docs and CLI semantics.

---

## Testing & expectations

- Tests live under `tests/` and use `pytest`.
- Many tests:

  - Set `HEALTHARCHIVE_DATABASE_URL` to a temporary path.
  - Re-create the schema via `Base.metadata.drop_all()` / `create_all()`.

When you change behavior:

- Add/adjust tests rather than disabling existing ones.
- Keep DB setup/teardown patterns consistent.

---

## Safety rails / things not to touch casually

- Don’t:

  - Change `HEALTHARCHIVE_ARCHIVE_ROOT` semantics in a way that would break existing job locations on disk without explicit migration.
  - Remove or relax job status transitions and retry guards.
  - Expose `/api/admin/**` or `/metrics` publicly.

- Be cautious with:

  - ORM model changes (`ArchiveJob`, `Snapshot`, `Source`).
  - CORS configuration in `ha_backend/api/__init__.py`.
  - Anything under `src/archive_tool/**` beyond doc updates.

For non-trivial changes, **explain your plan and assumptions in the chat before editing**.
