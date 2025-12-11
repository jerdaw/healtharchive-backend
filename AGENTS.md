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

- `docs/documentation.md` – architecture & implementation details.
- `docs/live-testing.md` – step-by-step local testing flows.
- `src/archive_tool/docs/documentation.md` – internals of the `archive_tool` crawler CLI.

When you’re doing anything beyond tiny local changes, **open those docs and sync your mental model first**.

---

## Dev environment & commands

From the repo root (Python project):

- Create venv (if not already done) – adjust to my actual workflow:
  ```bash
  python -m venv .venv
  source .venv/bin/activate  # or equivalent on Windows
  pip install -e ".[dev]"
````

* Run tests:

  ```bash
  pytest -q
  ```

For how to:

* Start the FastAPI app for local dev,
* Run specific CLI flows end-to-end,
* Wire up Docker + `archive_tool`,

→ **Follow `docs/live-testing.md` rather than inventing new commands**.

When you add/modify functionality, you should:

* Keep tests passing (`pytest -q`).
* Prefer adding/adjusting tests close to the code you touch (e.g., API route tests, indexing pipeline tests, worker tests).

---

## Core concepts you must respect

### Data model

* SQLAlchemy models defined under `src/ha_backend/models.py`:

  * `Source` – logical content origin (e.g., `"hc"`, `"phac"`).
  * `ArchiveJob` – a crawl job and its lifecycle (`queued`, `running`, `completed`, `failed`, `indexed`, `index_failed`, etc.).
  * `Snapshot` – a single captured page with URL, capture timestamp, WARC path, language, etc.
  * `Topic` – tag objects with `slug` and `label`, many-to-many with `Snapshot`.

If you change models:

* Think through migrations and existing queries/routes.
* Don’t drop/change columns in a way that silently breaks APIs.
* Update Pydantic schemas in `ha_backend/api/schemas.py` (and admin schemas) coherently.

### Job lifecycle / worker

* Job creation via `SourceJobConfig` in `ha_backend/job_registry.py`:

  * Seeds, `name_template`, and `tool_options` live there.
* `ArchiveJob.config` JSON is the **single source of truth** for building `archive_tool` CLI args.
* Worker loop (`ha_backend/worker/main.py`):

  * Picks `queued`/`retryable` jobs.
  * Runs `run_persistent_job(job_id)`.
  * Applies retry semantics.
  * On success, runs `index_job(job_id)`.

If you change job states, retry logic, or indexing behavior, update:

* Worker logic,
* Admin views (status counts, job details),
* Metrics (`/metrics`).

---

## archive_tool integration – internal crawler subpackage

The `archive_tool` package lives under `src/archive_tool/` and is maintained as
part of this repository. It originated as an earlier standalone crawler repo
but should now be treated as an in-tree, first-class component of the backend.

* The backend primarily talks to it via the CLI (`archive-tool` or the
  configured command) and imports a small set of helpers (`archive_tool.state`,
  `archive_tool.utils`) for WARC and state discovery.
* You **may** change and refactor `src/archive_tool/**` when needed, but:

  * Keep the CLI contract used by `ha_backend/jobs.py` and
    `ha_backend/job_registry.py` in sync (flags such as `--seeds`,
    `--name`, `--output-dir`, monitoring/VPN options, `--relax-perms`,
    etc.).
  * If you change how `.archive_state.json` or temp dirs are laid out,
    update WARC discovery (`ha_backend/indexing/warc_discovery.py`) and
    cleanup (`ha_backend/cli.py:cmd_cleanup_job`) at the same time.
  * Preserve or intentionally migrate any behavior relied on by tests under
    `tests/` (worker flows, job status transitions, indexing expectations).

* When in doubt:

  * Treat `ha_backend/jobs.py` and `ha_backend/job_registry.py` as the main
    integration points for CLI construction and configuration.
  * Treat `ha_backend/indexing/warc_discovery.py` and `cmd_cleanup_job` as
    the integration points for WARC/state discovery and cleanup.
  * Add or adjust tests close to the code you change.

The backend and `archive_tool` are expected to evolve together; it is fine for
changes to span both sides as long as they are coherent and tested.

---

## Indexing pipeline (WARCs → Snapshots)

Key pieces:

* `ha_backend/indexing/warc_discovery.py` – use `CrawlState` + `find_all_warc_files` to discover WARCs.
* `ha_backend/indexing/warc_reader.py` – stream HTML records from WARCs.
* `ha_backend/indexing/text_extraction.py` – extract title/text/snippet/language.
* `ha_backend/indexing/mapping.py` – map an archive record to a `Snapshot` ORM instance.
* `ha_backend/indexing/pipeline.py` – `index_job(job_id)` orchestrates the whole indexing step.

If you change indexing:

* Don’t break the assumption that **indexed snapshots reference a WARC path / record that `viewer.py` can replay**.
* Keep per-record errors logged but non-fatal where feasible.
* Keep pagination and performance in mind with large WARC sets.

---

## HTTP API contract

Public routes (for the frontend):

* `GET /api/health` – status and basic stats.
* `GET /api/sources` – summarized counts by source.
* `GET /api/topics` – canonical topic list (`slug` + `label`).
* `GET /api/search` – paginated search with `q`, `source`, `topic`, `page`, `pageSize`.
* `GET /api/snapshot/{id}` – metadata for one snapshot.
* `GET /api/snapshots/raw/{id}` – raw HTML replay.

Admin & metrics:

* `/api/admin/**` – job lists, details, snapshots per job, status counts.
* `/metrics` – Prometheus-style counts, behind `require_admin`.

When you add/modify endpoints:

* Keep public/admin responsibilities separate.
* Maintain existing query semantics:

  * `page >= 1`, `1 <= pageSize <= 100`, etc.
  * Filtering by `source` and `topic` using `Source.code` and `Topic.slug`.
* Update the relevant Pydantic schemas and tests accordingly.

---

## Security & admin auth

Admin auth is via `require_admin`:

* Controlled by `HEALTHARCHIVE_ENV` and `HEALTHARCHIVE_ADMIN_TOKEN`.
* In `production`/`staging`, a missing admin token should fail closed.
* When the token is set, admin/metrics require either:

  * `Authorization: Bearer <token>`, or
  * `X-Admin-Token: <token>`.

Do **not** weaken this behavior:

* Don’t expose admin endpoints without auth in non-dev environments.
* Don’t log secrets or tokens.

---

## Cleanup & retention

* Cleanup is done per job via `ha-backend cleanup-job --id ID --mode temp`.
* It removes:

  * temp crawl dirs (`.tmp*`),
  * `.archive_state.json`,
    consistent with `archive_tool`’s own cleanup behavior.
* Only safe when jobs are `indexed` or explicitly `index_failed` and not being retried.

Don’t make cleanup more aggressive (e.g. deleting WARCs or ZIMs) without carefully updating docs and CLI semantics.

---

## Testing & expectations

* Tests live under `tests/` and use `pytest`.
* Many tests:

  * Set `HEALTHARCHIVE_DATABASE_URL` to a temporary path.
  * Re-create the schema via `Base.metadata.drop_all()` / `create_all()`.

When you change behavior:

* Add/adjust tests rather than disabling existing ones.
* Keep DB setup/teardown patterns consistent.

---

## Safety rails / things not to touch casually

* Don’t:

  * Change `HEALTHARCHIVE_ARCHIVE_ROOT` semantics in a way that would break existing job locations on disk without explicit migration.
  * Remove or relax job status transitions and retry guards.
  * Expose `/api/admin/**` or `/metrics` publicly.
* Be cautious with:

  * ORM model changes (`ArchiveJob`, `Snapshot`, `Source`, `Topic`).
  * CORS configuration in `ha_backend/api/__init__.py`.
  * Anything under `src/archive_tool/**` beyond doc updates.

For non-trivial changes, **explain your plan and assumptions in the chat before editing**.
