# HealthArchive Backend – Live Testing Guide

This document describes a practical, incremental way to live‑test the
`healtharchive-backend` in a local development environment, starting with
the smallest checks and working up to more realistic scenarios.

It assumes you are working from the repo root and are comfortable with a
terminal and Python tooling.

---

## 0. One‑time setup

### 0.1 Create a virtualenv and install the backend

```bash
make venv
# or (manual):
# python -m venv .venv
# source .venv/bin/activate
# pip install -e ".[dev]"
```

This provides:

- `ha-backend` – backend CLI
- `archive-tool` – crawler CLI implemented by the in-repo `archive_tool` package (uses Docker + Zimit)

### 0.2 Configure environment variables

Use a local SQLite DB and archive root so you never touch production paths:

```bash
export HEALTHARCHIVE_DATABASE_URL=sqlite:///$(pwd)/.dev-healtharchive.db
export HEALTHARCHIVE_ARCHIVE_ROOT=$(pwd)/.dev-archive-root
export HEALTHARCHIVE_ADMIN_TOKEN=localdev-admin  # optional but recommended
# Optional: set CORS origins if your frontend runs on a non-default host
# (defaults already include http://localhost:3000 and https://healtharchive.ca)
# export HEALTHARCHIVE_CORS_ORIGINS=http://localhost:3000

# Shortcut: copy the sample file and source it
# cp .env.example .env
# source .env
```

Run Alembic migrations once to create the schema:

```bash
alembic upgrade head
```

---

## 1. Smallest checks (no Docker, no jobs)

Goal: prove the Python package and DB wiring work in isolation.

### 1.1 Run the test suite

```bash
make ci
# or (full suite):
# make check-full
# or (tests only):
# pytest -q
```

All tests should pass. (At time of writing, a 422 around
`/api/admin/jobs/status-counts` was fixed so this now passes too.)

### 1.2 Check DB connectivity

```bash
ha-backend check-db
```

You should see:

- The `HEALTHARCHIVE_DATABASE_URL` you set.
- “Database connection OK.”

### 1.3 Check environment / archive root

```bash
ha-backend check-env
```

Confirms:

- `HEALTHARCHIVE_ARCHIVE_ROOT` exists and is writable.
- The configured `archive_tool` command is resolvable.

---

## 2. API‑only live smoke tests (no archive_tool, no jobs)

Goal: run FastAPI + DB with an empty dataset.

### 2.1 Start the API

In one terminal (with `.venv` active and env vars set):

```bash
uvicorn ha_backend.api:app --reload --port 8001
```

### 2.2 Hit public endpoints

From another terminal:

```bash
curl http://localhost:8001/api/health
curl http://localhost:8001/api/sources
curl "http://localhost:8001/api/search?q=test"
```

Expect:

- `/api/health` → `{"status":"ok", ... "db":"ok" ...}`.
- `/api/sources` → `[]` (no data yet).
- `/api/search` → empty results, but HTTP 200.

### 2.3 Admin endpoints

With `HEALTHARCHIVE_ADMIN_TOKEN` **unset** (local dev only):

```bash
curl http://localhost:8001/api/admin/jobs
```

Admin routes are open (dev mode).

With `HEALTHARCHIVE_ADMIN_TOKEN=localdev-admin` set when starting uvicorn:

```bash
curl -H "Authorization: Bearer localdev-admin" \
  http://localhost:8001/api/admin/jobs
```

Confirms admin auth + simple bearer token protection.

### 2.4 Admin access patterns (local vs staging/prod)

In local development it is acceptable to either leave
`HEALTHARCHIVE_ADMIN_TOKEN` unset (open admin endpoints) or to use a simple
token like `localdev-admin` as shown above.

In staging and production you should **always** set a strong, random admin
token and treat it as a secret:

```bash
export HEALTHARCHIVE_ADMIN_TOKEN="prod-admin-token-from-secret-store"
uvicorn ha_backend.api:app --host 0.0.0.0 --port 8001
```

From a trusted machine you can then verify access:

- Without a token (should be forbidden when the env var is set):

  ```bash
  curl -i "https://api.healtharchive.ca/api/admin/jobs"
  curl -i "https://api.healtharchive.ca/metrics"
  ```

- With the correct token:

  ```bash
  curl -i \
    -H "Authorization: Bearer $HEALTHARCHIVE_ADMIN_TOKEN" \
    "https://api.healtharchive.ca/api/admin/jobs"

  curl -i \
    -H "Authorization: Bearer $HEALTHARCHIVE_ADMIN_TOKEN" \
    "https://api.healtharchive.ca/metrics"
  ```

In staging/prod you should call these endpoints only from operator tooling
or monitoring systems (Prometheus, etc.), not from the public frontend.

---

## 3. Minimal archive_tool integration (sanity only)

Goal: prove Docker + `archive_tool` wiring work without committing to long
crawls.

### 3.1 Verify archive_tool & Docker

```bash
ha-backend check-archive-tool
```

This runs `archive-tool --help` via the configured command (by default
`archive-tool`, which uses Docker).

If this fails, fix Docker or PATH before proceeding.

### 3.2 Optional: direct archive_tool dry run

From the repo root:

```bash
archive-tool --seeds https://example.org --name example --output-dir $(pwd)/.dev-archive-root/dry-run --dry-run
```

This is not required for backend work, but is a quick sanity check that the
integrated crawler CLI works directly and that your configuration (seeds,
output directory, workers, monitoring flags) is valid without actually
starting Docker containers.

---

## 4. Small DB‑backed job pipeline in a dev sandbox

Goal: run a single small job end‑to‑end (create → run → index) using the
same flows the worker will use, with the important caveat that the current
Zimit image may not leave WARCs accessible (see notes below).

### 4.1 Seed sources

```bash
ha-backend seed-sources
```

This inserts baseline `Source` rows (e.g., `hc`, `phac`).

```bash
ha-backend list-jobs
```

Should still show no `ArchiveJob` rows initially.

### 4.2 Create a job

Start with Health Canada:

```bash
ha-backend create-job --source hc
```

Note the printed job ID (call it `JOB_ID`). At this point:

- A DB row exists with `status="queued"`.
- An `output_dir` path under `HEALTHARCHIVE_ARCHIVE_ROOT` is reserved.

### 4.3 Run the crawl once

```bash
ha-backend run-db-job --id JOB_ID
```

This:

- Loads the DB row.
- Constructs an `archive_tool` command.
- Runs Docker + Zimit.

It can take a minute or more depending on seeds and limits. If it fails:

- Inspect `ha-backend show-job --id JOB_ID` for `crawler_exit_code`,
  `status`, and `output_dir`.
- Check logs under that `output_dir` with `ls` and `less`.

> **Note:** With the current Zimit image and defaults, small runs may still
> end with `FAILED_NO_WARCS` because no accessible WARCs are left under
> `/output/.tmp*`. This is a limitation of the current crawler image and
> does not block backend/API development (see section 6 for a controlled
> WARC test).

### 4.4 Index the job (best effort)

Once a job has `status="completed"`, you can attempt:

```bash
ha-backend index-job --id JOB_ID
```

This:

- Runs WARC discovery based on `job.output_dir`.
- Streams WARCs into `Snapshot` rows.
- Updates `warc_file_count` and `indexed_page_count`.

If no WARCs are discovered, the job is marked `index_failed`. This is
expected when the crawler leaves no accessible WARCs.

### 4.5 Verify via CLI and API

CLI:

```bash
ha-backend show-job --id JOB_ID
```

Look for:

- `status="indexed"` and `indexed_page_count > 0` (ideal case), or
- `status="index_failed"` if no WARCs were found.

API (with uvicorn running):

```bash
curl http://localhost:8001/api/sources
curl "http://localhost:8001/api/search?q=health&source=hc"
```

If indexing succeeded, these will reflect real crawl data. In practice,
for development we often use synthetic snapshots instead (see 6.2).

---

## 5. Worker loop tests (background processing)

Goal: test the long‑running worker process that automates job execution.

### 5.1 Queue a couple of jobs

```bash
ha-backend create-job --source hc
ha-backend create-job --source phac
ha-backend list-jobs
```

You should see the new jobs in `status="queued"`.

### 5.2 Run worker in single‑cycle mode

```bash
ha-backend start-worker --once
```

The worker:

- Picks the oldest `queued`/`retryable` job.
- Runs `run_persistent_job(job_id)` (archive_tool).
- Immediately runs `index_job(job_id)`.
- Exits after one iteration.

Check transitions:

```bash
ha-backend list-jobs
```

Statuses should move (e.g., `queued` → `completed`/`index_failed`).

### 5.3 Worker loop with a harmless tool command (optional)

For pure orchestration tests, point `archive_tool` at `echo`:

```bash
export HEALTHARCHIVE_TOOL_CMD=echo
```

Then:

```bash
ha-backend create-job --source hc
ha-backend start-worker --once
ha-backend list-jobs
```

You will see jobs flip from `queued` to `completed` (crawl RC 0) and then to
`index_failed` (no WARCs), verifying the worker loop and status updates
without touching Docker.

---

## 6. Raw snapshot viewer tests

Goal: confirm WARC → HTML replay is functioning.

There are two complementary approaches:

### 6.1 Happy‑path viewer using a synthetic WARC

You can create a tiny WARC file and a corresponding `Snapshot` in the DB:

```bash
python - << 'PY'
from datetime import datetime, timezone
from pathlib import Path
from io import BytesIO

from warcio.warcwriter import WARCWriter

from ha_backend.db import get_session
from ha_backend.models import Source, Snapshot

root = Path(".dev-archive-root") / "manual-warcs"
warc_path = root / "viewer-test.warc.gz"
url = "https://example.org/page"
html_body = "<html><body><h1>Hello from WARC</h1></body></html>"

root.mkdir(parents=True, exist_ok=True)
with warc_path.open("wb") as f:
    writer = WARCWriter(f, gzip=True)
    payload = BytesIO(
        (
            "HTTP/1.1 200 OK\\r\\n"
            "Content-Type: text/html; charset=utf-8\\r\\n"
            f"Content-Length: {len(html_body.encode('utf-8'))}\\r\\n"
            "\\r\\n" +
            html_body
        ).encode("utf-8")
    )
    record = writer.create_warc_record(
        uri=url,
        record_type="response",
        payload=payload,
        warc_headers_dict={"WARC-Date": "2025-01-01T12:00:00Z"},
    )
    writer.write_record(record)
    record_id = record.rec_headers.get_header("WARC-Record-ID")

with get_session() as session:
    src = session.query(Source).filter_by(code="test").one_or_none()
    if src is None:
        src = Source(
            code="test",
            name="Test Source",
            base_url="https://example.org",
            description="Test source for viewer",
            enabled=True,
        )
        session.add(src)
        session.flush()

    snap = Snapshot(
        job_id=None,
        source_id=src.id,
        url=url,
        normalized_url_group=url,
        capture_timestamp=datetime(2025, 1, 1, 12, 0, tzinfo=timezone.utc),
        mime_type="text/html",
        status_code=200,
        title="Viewer Test Page",
        snippet="Hello from WARC",
        language="en",
        warc_path=str(warc_path),
        warc_record_id=record_id,
    )
    session.add(snap)
    session.flush()
    print("SNAPSHOT_ID", snap.id)
PY
```

Note the printed `SNAPSHOT_ID` (for example `5`), then:

```bash
curl "http://localhost:8001/api/snapshots/raw/5"
```

You should see the HTML body with `"Hello from WARC"`, confirming that:

- `warc_path` is valid.
- `warc_record_id` is used for lookup.
- `viewer.py` can reconstruct and return HTML.

### 6.2 Error path when WARCs are missing

For snapshots whose `warc_path` points to a non‑existent file (e.g., your
seeded dev snapshots), the route returns a meaningful error:

```bash
curl "http://localhost:8001/api/snapshots/raw/1"
```

Returns HTTP 404 with `{"detail":"Underlying WARC file for this snapshot is missing"}`.

---

## 7. Real WARC indexing (advanced)

Goal: take a small real Zimit crawl, fix any permission issues, and index its
WARCs into snapshots for use via the HTTP API.

This section assumes you have already run a small crawl with something like:

```bash
ha-backend run-job \
  --name hc-dev-warcs \
  --seeds https://www.canada.ca/en/health-canada.html \
  --initial-workers 1 \
  --log-level INFO \
  -- \
  --pageLimit 5 \
  --depth 1
```

and have a job directory such as:

```bash
.dev-archive-root/20251210T013134Z__hc-dev-warcs
```

### 7.1 Fix permissions on the temp dir (if needed)

Zimit may create `.tmp*` directories owned by `root`, which prevents the
backend from reading WARCs. In the job directory:

```bash
cd .dev-archive-root/20251210T013134Z__hc-dev-warcs
ls -ld .tmp*
```

If you see `drwx------ root root ...`, fix ownership:

```bash
sudo chown -R $(id -u):$(id -g) .tmp*
```

Verify you can see WARCs:

```bash
find . -maxdepth 6 -type f \( -name '*.warc' -o -name '*.warc.gz' \) -print
```

You should see something like:

```text
./.tmpXXXX/collections/crawl-.../archive/rec-...warc.gz
```

### 7.2 Create a DB job pointing at this output_dir

From the repo root:

```bash
python - << 'PY'
from datetime import datetime, timezone
from pathlib import Path

from ha_backend.db import get_session
from ha_backend.models import ArchiveJob, Source

job_dir = Path(".dev-archive-root/20251210T013134Z__hc-dev-warcs").resolve()

with get_session() as session:
    src = session.query(Source).filter_by(code="hc").one()
    now = datetime.now(timezone.utc)
    job = ArchiveJob(
        source_id=src.id,
        name="hc-dev-warcs",
        output_dir=str(job_dir),
        status="completed",   # ready for indexing
        queued_at=now,
        started_at=now,
        finished_at=now,
    )
    session.add(job)
    session.flush()
    print("JOB_ID", job.id)
PY
```

Note the printed `JOB_ID` (e.g. `11`).

**Alternative (CLI):** you can now do the same with a helper command:

```bash
ha-backend register-job-dir \
  --source hc \
  --output-dir .dev-archive-root/20251210T013134Z__hc-dev-warcs \
  --name hc-dev-warcs
```

This creates a DB row in `status="completed"` so it is ready for indexing.

### 7.3 Index the job and verify via API

Index the WARCs:

```bash
ha-backend index-job --id JOB_ID
ha-backend show-job --id JOB_ID
```

You should see:

- `status="indexed"`
- `warc_file_count > 0`
- `indexed_page_count > 0`

With uvicorn running:

```bash
curl http://localhost:8001/api/sources
curl "http://localhost:8001/api/search?q=health&source=hc"
```

You will see the real crawl snapshots alongside any synthetic dev data.

---

## 8. Admin, retry, and cleanup flows

Goal: exercise non‑happy‑path and maintenance commands.

### 8.1 Retry jobs

If a job has `status="failed"` or `status="index_failed"`:

```bash
ha-backend retry-job --id JOB_ID
ha-backend show-job --id JOB_ID
```

Behavior:

- `status="failed"` → `status="retryable"` (for another crawl).
- `status="index_failed"` → `status="completed"` (allowing re‑indexing).

### 8.2 Cleanup temp dirs and state

Only allowed for `status in {"indexed", "index_failed"}`:

```bash
ha-backend cleanup-job --id JOB_ID --mode temp
ha-backend show-job --id JOB_ID
```

This:

- Locates temp dirs and `.archive_state.json` via `archive_tool.state`.
- Deletes `.tmp*` dirs and the state file.
- Sets:
  - `cleanup_status = "temp_cleaned"`
  - `cleaned_at` to the cleanup time
  - `state_file_path = None`

> **Caution:** This removes temp crawl artifacts (including WARCs) under
> `.tmp*` for that job. Only run it once you are satisfied with indexing and
> any ZIMs/exports.
>
> If you are using the replay service (pywb) to serve this job’s WARCs, do not
> run `cleanup-job --mode temp` for that job — replay depends on the WARCs
> remaining on disk.
>
> If replay is enabled globally (`HEALTHARCHIVE_REPLAY_BASE_URL` is set),
> `cleanup-job --mode temp` will refuse unless you pass `--force`. Treat
> `--force` as an emergency override (it can break replay by deleting WARCs).

---

## 9. Metrics and observability

Goal: validate Prometheus‑style metrics.

With uvicorn running:

```bash
curl -H "Authorization: Bearer localdev-admin" \
  http://localhost:8001/metrics | head
```

Look for:

- Job status metrics:

  ```text
  healtharchive_jobs_total{status="failed"} 6
  healtharchive_jobs_total{status="indexed"} 1
  ...
  ```

- Cleanup status metrics:

  ```text
  healtharchive_jobs_cleanup_status_total{cleanup_status="none"} ...
  healtharchive_jobs_cleanup_status_total{cleanup_status="temp_cleaned"} ...
  ```

- Snapshot metrics:

  ```text
  healtharchive_snapshots_total 5
  healtharchive_snapshots_total{source="hc"} 3
  healtharchive_snapshots_total{source="test"} 2
  ```

- Page-level crawl metrics (best-effort from crawl logs):

  ```text
  healtharchive_jobs_pages_crawled_total 1234
  healtharchive_jobs_pages_crawled_total{source="hc"} 789
  healtharchive_jobs_pages_failed_total 12
  healtharchive_jobs_pages_failed_total{source="hc"} 3
  ```

Counts should roughly match `ha-backend list-jobs`, `/api/sources` /
`/api/search`, and the page counters shown in `/api/admin/jobs/{id}`.

---

## 10. Scaling up to more realistic scenarios

Once the above is stable, you can incrementally increase realism:

- **Multiple jobs with the worker running continuously.**

  ```bash
  ha-backend start-worker --poll-interval 30
  ```

  In another terminal, periodically run `create-job` and watch statuses
  transition through `queued → running → completed → indexed/index_failed`.

- **Postgres instead of SQLite** by pointing
  `HEALTHARCHIVE_DATABASE_URL` at a dev Postgres instance and re‑running
  `alembic upgrade head`.

- **Monitoring/adaptive options** via `job_registry` overrides:

  - Enable `enable_monitoring`, `enable_adaptive_workers`,
    `enable_vpn_rotation` and confirm they affect archive_tool behavior.

- **Frontend integration** by running the separate `healtharchive-frontend`
  against your local backend (`NEXT_PUBLIC_BACKEND_URL=http://localhost:8001`)
  and exercising the full UI → API → DB → WARC stack.

> **Note on real WARCs:** At time of writing, the default Zimit image may
> not leave WARCs in the expected `/output/.tmp*/collections/.../archive`
> path or may create temp directories with restrictive permissions. For
> backend/API and viewer development, using synthetic WARCs (as in 6.1) and
> seeded snapshots is sufficient. Integrating with live WARCs may require
> either adjusting Zimit options or updating WARC discovery to match the
> crawler’s current layout and permissions.
