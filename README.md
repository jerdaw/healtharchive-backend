# HealthArchive.ca – Backend

This repository contains the backend services and archiving pipeline for
[HealthArchive.ca](https://healtharchive.ca).

The backend has three main responsibilities:

- **Run crawl jobs** for sources like Health Canada (`hc`) and PHAC (`phac`)
  by calling the `archive_tool` CLI (which wraps `zimit` in Docker).
- **Index WARCs into snapshots** (URL + timestamp + HTML text, etc.) in a
  relational database.
- **Expose HTTP APIs** that the Next.js frontend uses for search, source
  summaries, and snapshot viewing.

For a deep architecture and implementation walkthrough, see
`docs/architecture.md`. For a step‑by‑step local live‑testing guide, see
`docs/development/live-testing.md`. For the current production runbook (single
VPS + Tailscale-only SSH + nightly backups), see
`docs/deployment/production-single-vps.md`.
This README is intentionally shorter and focused on practical usage.

---

## Project layout (high level)

```text
.
├── README.md
├── docs/
│   ├── README.md             # Docs index
│   ├── architecture.md       # Detailed architecture and implementation guide
│   ├── development/          # Local dev + live-testing flows
│   ├── deployment/           # Deployment/runbooks/checklists
│   └── operations/           # Monitoring/uptime/CI guidance
├── pyproject.toml            # Package + dependency metadata
├── requirements.txt          # Convenience requirements file (mirrors pyproject)
├── alembic/                  # Database migrations
├── src/
│   ├── ha_backend/           # Backend package
│   │   ├── api/              # FastAPI app, public + admin routes
│   │   ├── cli.py            # ha-backend CLI entrypoint
│   │   ├── config.py         # Archive root + DB + tool config
│   │   ├── db.py             # SQLAlchemy engine/session helpers
│   │   ├── indexing/         # WARC discovery, parsing, text extraction, mapping
│   │   ├── job_registry.py   # Per-source job templates (hc, phac)
│   │   ├── jobs.py           # Persistent job runner → archive_tool
│   │   ├── logging_config.py # Shared logging configuration
│   │   ├── models.py         # ORM models (Source, ArchiveJob, Snapshot, Topic)
│   │   ├── seeds.py          # Initial Source seeding
│   │   └── worker/           # Long-running worker loop for queued jobs
│   └── archive_tool/         # Crawler/orchestrator subpackage, with its own docs
└── tests/                    # Pytest suite
```

The `archive_tool` package started as a separate repository and is now
maintained in-tree as the backend's crawler/orchestrator subpackage. It is
invoked primarily via its CLI (`archive-tool`) and integrates closely with the
backend's job, worker, and indexing code. Its internal documentation lives
under `src/archive_tool/docs/documentation.md`.

---

## Installation & setup

### 1. Prerequisites

- Python **3.11+**
- Docker (required by `archive_tool` / `zimit` for crawls)
- A Python virtual environment (recommended)

### 2. Install dependencies

From the repo root:

```bash
python -m venv .venv
source .venv/bin/activate

# Install runtime + dev dependencies and entrypoints:
pip install -e ".[dev]"
```

This provides:

- `ha-backend` – backend CLI
- `archive-tool` – console script pointing at the in-repo `archive_tool` package

### 3. Database

By default the backend uses a SQLite file at `sqlite:///healtharchive.db` in
the repo root, or whatever you point `HEALTHARCHIVE_DATABASE_URL` at.

To verify connectivity:

```bash
ha-backend check-db
```

For production, you will typically point `HEALTHARCHIVE_DATABASE_URL` at a
Postgres instance and run Alembic migrations:

```bash
alembic upgrade head
```

For local development it is common to isolate everything under the repo
directory:

```bash
export HEALTHARCHIVE_DATABASE_URL=sqlite:///$(pwd)/.dev-healtharchive.db
export HEALTHARCHIVE_ARCHIVE_ROOT=$(pwd)/.dev-archive-root
export HEALTHARCHIVE_ADMIN_TOKEN=localdev-admin  # optional for admin routes
# Optional CORS overrides (defaults already cover localhost + prod domains)
# export HEALTHARCHIVE_CORS_ORIGINS=http://localhost:3000,http://localhost:5173
alembic upgrade head
```

If you want to use Postgres locally via Docker for testing:

```bash
docker run --name ha-pg \
  -e POSTGRES_USER=healtharchive \
  -e POSTGRES_PASSWORD=healtharchive \
  -e POSTGRES_DB=healtharchive \
  -p 5432:5432 -d postgres:16

export HEALTHARCHIVE_DATABASE_URL=postgresql+psycopg://healtharchive:healtharchive@localhost:5432/healtharchive
alembic upgrade head
```

### 4. Archive root & archive_tool

The backend writes job output under an archive root directory:

- `HEALTHARCHIVE_ARCHIVE_ROOT` (env) **or**
- `/mnt/nasd/nobak/healtharchive/jobs` by default.

To verify the archive root and `archive_tool`:

```bash
ha-backend check-env           # shows archive root and checks writability
ha-backend check-archive-tool  # runs 'archive-tool --help'
```

---

## Running the API

The FastAPI app lives at `ha_backend.api:app`. Once your virtualenv and DB
are configured:

```bash
uvicorn ha_backend.api:app --reload
```

Key public endpoints (all prefixed with `/api`):

- `GET /api/health`  
  Basic health check (`status`, DB connectivity, job and snapshot counts).

- `GET /api/sources`  
  Per-source summaries derived from indexed snapshots.

- `GET /api/search`  
  Full-text style search over snapshots (with filters for `source`, `topic`,
  pagination, etc.).

  Ranking controls:
  - Default ranking is controlled by `HA_SEARCH_RANKING_VERSION` (`v1` or `v2`).
  - Per-request override: add `ranking=v1|v2` to `/api/search`.

- `GET /api/snapshot/{id}`  
  Snapshot metadata for a single record.

- `GET /api/snapshots/raw/{id}`  
  Returns the archived HTML document for embedding in the frontend.

Admin + observability endpoints (protected by a simple admin token):

- `GET /api/admin/jobs` – list jobs (filters: `source`, `status`)
- `GET /api/admin/jobs/{id}` – detailed job info
- `GET /api/admin/jobs/status-counts` – job counts by status
- `GET /api/admin/jobs/{id}/snapshots` – list snapshots for a job
- `GET /api/admin/search-debug` – admin-only search scoring breakdown
- `GET /metrics` – Prometheus-style metrics (jobs, cleanup_status, snapshots)

Admin endpoints require a token when `HEALTHARCHIVE_ADMIN_TOKEN` is set (see
“Admin auth” below).

These endpoints are intended for internal operators and monitoring systems
only. The public Next.js frontend does **not** call `/api/admin/*` or
`/metrics` directly.

### Dev .env helper

For convenience, you can copy `.env.example` to `.env` (git-ignored) and source
it in your shell:

```bash
cp .env.example .env
source .env
alembic upgrade head
uvicorn ha_backend.api:app --reload --port 8001
```

Do not commit real secrets in `.env`; use host-managed env vars for staging/prod.

---

## Search evaluation tools

This repo includes lightweight scripts to capture and compare search results:

- Capture a standard query set (v1 vs v2):
  - `./scripts/search-eval-capture.sh --out-dir /tmp/ha-search-eval --page-size 20 --ranking v1`
  - `./scripts/search-eval-capture.sh --out-dir /tmp/ha-search-eval --page-size 20 --ranking v2`
- Diff two capture directories:
  - `python ./scripts/search-eval-diff.py --a /tmp/ha-search-eval/<TS_A> --b /tmp/ha-search-eval/<TS_B> --top 20`

Docs:
- `docs/operations/search-quality.md`
- `docs/operations/search-golden-queries.md`

### CORS / frontend origins

The API enables CORS for the public endpoints. Allowed origins come from
`HEALTHARCHIVE_CORS_ORIGINS` (comma-separated). Defaults cover local dev and
production:

```
http://localhost:3000, http://localhost:5173, https://healtharchive.ca, https://www.healtharchive.ca
```

Set `HEALTHARCHIVE_CORS_ORIGINS` when your frontend runs on a different host
or port (e.g., a preview/staging domain). Admin routes remain token-gated even
when CORS is enabled.

---

## Running the worker

The worker process polls for queued jobs and runs both the crawl (`archive_tool`)
and indexing pipeline.

Start it via the CLI:

```bash
ha-backend start-worker
```

Options:

- `--poll-interval SECONDS` – sleep delay when no work is found (default 30).
- `--once` – process at most one job and exit (useful for cron / debugging).

The worker:

- Looks for `ArchiveJob` rows with `status in ("queued", "retryable")`.
- Runs `run_persistent_job(job_id)` which calls `archive_tool` as a subprocess.
- On crawl success, runs `index_job(job_id)` to ingest WARCs into `Snapshot`s.
- Applies a simple retry policy (`MAX_CRAWL_RETRIES`) before marking jobs
  permanently `failed`.

---

## Creating and managing jobs

The backend exposes a small CLI layer for managing `ArchiveJob` rows.

### Seed sources

Ensure `Source` rows for `hc` and `phac` exist:

```bash
ha-backend seed-sources
```

### Create a job from registry defaults

For example, a monthly Health Canada job:

```bash
ha-backend create-job --source hc
```

This:

- Uses the `SourceJobConfig` for `hc` (seeds, naming template, tool options).
- Creates an `ArchiveJob` row with `status="queued"` and a unique `output_dir`.

### Run a specific DB-backed job once

```bash
ha-backend run-db-job --id 42
```

This calls `archive_tool` with the stored seeds, `output_dir`, and tool
options. It updates `status`, timestamps, and `crawler_exit_code`.

### Index an existing job

If you ran a crawl separately and just want to index WARCs:

```bash
ha-backend index-job --id 42
```

If you have an existing `archive_tool` output directory on disk (e.g. from a
manual run) and want to attach it to the DB for indexing, use:

```bash
ha-backend register-job-dir --source hc --output-dir /path/to/job_dir [--name NAME]
ha-backend index-job --id <printed ID>
```

**Permissions note:** crawls run as root inside Docker. The registry defaults now
enable `relax_perms` so temp WARCs are chmod’d readable after the crawl, allowing
indexing without a host-side `sudo chown`. If you disable `relax_perms`, you may
need to chown `.tmp*` before indexing.

### List and inspect jobs

```bash
ha-backend list-jobs
ha-backend show-job --id 42
```

### Validate a job's configuration (dry-run)

To validate that a job's configuration is coherent (seeds, tool options, and
zimit args) without actually running a crawl, you can invoke the integrated
`archive_tool` CLI in dry-run mode via:

```bash
ha-backend validate-job-config --id 42
```

This:

- Reconstructs the `archive_tool` CLI arguments from `ArchiveJob.config`.
- Runs `archive-tool` with `--dry-run` so it validates the configuration and
  prints a summary.
- Does **not** change the job's status or timestamps.

### Retry and cleanup

- Retry a failed crawl or reindex:

  ```bash
  ha-backend retry-job --id 42
  ```

  - For `status="failed"` → sets `status="retryable"` for another crawl.
  - For `status="index_failed"` → sets `status="completed"` so indexing can re-run.
  - For other statuses, the command logs that there is nothing to retry.

- Cleanup temp dirs and state for an **indexed** or **index_failed** job:

  ```bash
  ha-backend cleanup-job --id 42
  ```

  This:

  - Uses `archive_tool`’s `CrawlState` and `cleanup_temp_dirs(...)` to delete
    `.tmp*` directories and the `.archive_state.json` file under `output_dir`.
  - Leaves the job directory and any final ZIM in place.
  - Updates `ArchiveJob.cleanup_status = "temp_cleaned"` and `cleaned_at` when
    there was actually a state file and/or temp dirs to remove.

> **Note:** `cleanup-job` is destructive for temporary crawl artifacts
> (including WARCs under `.tmp*`). Only run it after you are confident the
> job has been fully indexed (or indexing has failed in a way you do not
> plan to recover from) and any desired ZIMs or exports are verified.

---

## Configuration (environment variables)

The backend reads configuration from environment variables with sensible
defaults:

- `HEALTHARCHIVE_DATABASE_URL`  
  SQLAlchemy URL for the DB. Defaults to `sqlite:///healtharchive.db` in the
  repo root.

- `HEALTHARCHIVE_ARCHIVE_ROOT`  
  Base directory for job output dirs (passed as `--output-dir` to `archive_tool`).
  Defaults to `/mnt/nasd/nobak/healtharchive/jobs`.

- `HEALTHARCHIVE_TOOL_CMD`  
  Command used to invoke the archiver. Defaults to `archive-tool`.

- `HEALTHARCHIVE_ENV`  
  High-level environment hint used by admin auth. Recognised values:
  - `"development"` (default when unset): admin endpoints are open when
    `HEALTHARCHIVE_ADMIN_TOKEN` is unset (dev convenience).
  - `"staging"` or `"production"`: admin endpoints fail closed with HTTP 500
    if `HEALTHARCHIVE_ADMIN_TOKEN` is not configured.

- `HEALTHARCHIVE_ADMIN_TOKEN`  
  Optional admin token. If set, `/api/admin/*` and `/metrics` require either:
  - `Authorization: Bearer <token>` or
  - `X-Admin-Token: <token>`  
  If unset and `HEALTHARCHIVE_ENV` is `"development"` (or unset), admin
  endpoints are open (intended only for local development). In staging and
  production you should **always** set a long, random token and store it as a
  secret in your hosting platform (never committed to the repo); when
  `HEALTHARCHIVE_ENV` is `"staging"` or `"production"` and this token is
  missing, admin and metrics endpoints return HTTP 500.

- `HEALTHARCHIVE_LOG_LEVEL`  
  Global log level (`DEBUG`, `INFO`, etc.). Defaults to `INFO`.

- `HEALTHARCHIVE_CORS_ORIGINS`  
  Comma-separated list of allowed Origins for CORS on the public API routes.
  If unset, a built-in default is used:

  - `http://localhost:3000`
  - `http://localhost:5173`
  - `https://healtharchive.ca`
  - `https://www.healtharchive.ca`

  In production and staging you should set this explicitly so that only
  expected frontend hosts can call the API from a browser. Examples:

  - **Production (frontend at healtharchive.ca):**

    ```bash
    export HEALTHARCHIVE_CORS_ORIGINS="https://healtharchive.ca,https://www.healtharchive.ca"
    ```

- **Preview (frontend at healtharchive.vercel.app):**

    ```bash
    export HEALTHARCHIVE_CORS_ORIGINS="https://healtharchive.vercel.app"
    ```

  You can also include `http://localhost:3000` if you want local development
  to talk directly to a remote API instance.

For a more complete checklist covering staging/production configuration,
DNS, and Vercel env vars, see:

- `docs/deployment/hosting-and-live-server-to-dos.md`
- `docs/deployment/environment-matrix.md`
- `docs/deployment/production-single-vps.md`
- `docs/deployment/staging-rollout-checklist.md`
- `docs/deployment/production-rollout-checklist.md`
- `docs/operations/monitoring-and-ci-checklist.md`.

---

## Continuous integration

A GitHub Actions workflow (`.github/workflows/backend-ci.yml`) is intended to
run on pushes to `main` and on pull requests. It:

- Checks out the repository.
- Sets up Python 3.11.
- Installs dependencies with:

  ```bash
  pip install -e ".[dev]"
  ```

- Runs the test suite:

  ```bash
  pytest -q
  ```
  - Runs a lightweight security scan over the backend package using Bandit:

  ```bash
  bandit -r src/ha_backend -q
  ```

The CI job uses a temporary SQLite database via:

```bash
HEALTHARCHIVE_DATABASE_URL=sqlite:///./ci-healtharchive.db
```

so no external DB or Docker services are required. Crawls are not executed in
CI; tests focus on unit-level behavior (DB models, APIs, job orchestration,
etc.).

---

## Detailed architecture

For a full walkthrough of:

- ORM models and status lifecycle
- Job registry and how per-source jobs are configured
- `archive_tool` integration and adaptive strategies
-- Indexing pipeline and snapshot schema
-- HTTP API routes and JSON schemas
-- Worker loop and retry semantics
-- Cleanup and retention strategy (Phase 9)
-- How the backend integrates with the in-repo `archive_tool` crawler

see `docs/architecture.md`.

### Frontend integration smoke test

Once a frontend is pointed at this backend (via `NEXT_PUBLIC_API_BASE_URL` on
the frontend side and `HEALTHARCHIVE_CORS_ORIGINS` here), you can perform a
quick end-to-end smoke test:

1. **Verify API health from the frontend host**

   From a shell:

   ```bash
   curl -i "$API_BASE_URL/api/health"
   curl -i "$API_BASE_URL/api/sources"
   ```

   You should see HTTP 200 responses and JSON bodies. If you add an `Origin`
   header matching the frontend (e.g. `https://healtharchive.ca`), the response
   should include:

   ```text
   Access-Control-Allow-Origin: https://healtharchive.ca
   Vary: Origin
   ```

2. **Exercise the UI**

   From the frontend domain (staging or production):

   - Visit `/archive`:
     - With the backend up, the filters should show `Filters (live API)` and
       search/pagination should be backed by real snapshot data.
     - If you intentionally stop the backend (in staging), the UI should show
       a small “Backend unreachable” banner (when enabled) and fall back to
       the demo dataset with a clear notice.
   - Visit `/archive/browse-by-source` and `/snapshot/[id]` to confirm source
     summaries and snapshot details load correctly against the live API.

The `archive_tool` subpackage also has its own detailed documentation in
`src/archive_tool/docs/documentation.md` describing its internal state
machine and Docker orchestration, and how it cooperates with the backend.
