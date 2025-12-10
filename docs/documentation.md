# HealthArchive Backend – Architecture & Implementation Guide

This document is an in‑depth walkthrough of the **HealthArchive.ca backend**
(`healtharchive-backend` repo). It covers:

- How the backend is structured.
- How it integrates with the vendored `archive_tool`.
- The data model and job lifecycle.
- The indexing pipeline (WARCs → snapshots).
- HTTP APIs (public + admin) and metrics.
- Worker loop, retries, and cleanup/retention (Phase 9).

For `archive_tool` internals (log parsing, Docker orchestration, run modes),
see `src/archive_tool/docs/documentation.md`.

---

## 1. High‑level architecture

### 1.1 Components

- **archive_tool** (vendored under `src/archive_tool/`):
  - CLI wrapper around `zimit` + Docker.
  - Manages temporary output dirs, WARCs, and final ZIM build.
  - Tracks persistent state in `.archive_state.json` + `.tmp*` directories.
  - Implements stall/error detection, adaptive worker reductions, and VPN
    rotation (when enabled).

- **Backend package** (`src/ha_backend/`):
  - Orchestrates crawl **jobs** using `archive_tool` as a subprocess.
  - Stores job and snapshot metadata in a relational database via SQLAlchemy.
  - Indexes WARCs into `Snapshot` rows.
  - Exposes HTTP APIs via FastAPI.
  - Provides a worker loop to process queued jobs.
  - Offers CLI commands for admins (job creation, status, retry, cleanup).

- **External dependencies**:
  - Docker & `ghcr.io/openzim/zimit` image.
  - Database (SQLite by default; Postgres recommended in production).
  - Optional VPN client/command for rotation (e.g., `nordvpn`).

### 1.2 Data flow overview

1. **Job creation**:
   - Admin runs `ha-backend create-job --source hc`.
   - Backend:
     - Ensures a `Source` row exists.
     - Uses `SourceJobConfig` to build seeds, tool options, and `output_dir`.
     - Inserts an `ArchiveJob` with `status="queued"`.

2. **Crawl (archive_tool)**:
   - Worker or CLI runs `run_persistent_job(job_id)`:
     - Builds `archive_tool` CLI args from `ArchiveJob.config` and `output_dir`.
     - Runs `archive_tool` as a subprocess (no in‑process calls).
     - Marks job `running` → `completed` or `failed` with `crawler_exit_code`
       and `crawler_status`.
   - `archive_tool`:
     - Validates Docker.
     - Determines run mode (Fresh/Resume/New‑with‑Consolidation/Overwrite).
     - Spawns `docker run ghcr.io/openzim/zimit zimit ...`.
     - Tracks temp dirs and state, discovers WARCs, and optionally runs a
       final ZIM build (depending on its configuration).

3. **Indexing (WARCs → Snapshot)**:
   - Worker calls `index_job(job_id)` when crawl succeeds.
   - Backend:
     - Uses `CrawlState` + `find_all_warc_files` to locate WARCs under
       `output_dir`.
     - Streams WARC records, extracts HTML, text, language, etc.
     - Writes `Snapshot` rows for each captured page.
     - Marks job `indexed` with `indexed_page_count`.

4. **Serving**:
   - FastAPI app:
     - `GET /api/search` queries `Snapshot` for search results.
     - `GET /api/sources` summarises captures per `Source`.
     - `GET /api/snapshot/{id}` returns metadata for a single snapshot.
     - `GET /api/snapshots/raw/{id}` replays archived HTML from a WARC.

5. **Admin & cleanup**:
   - Admin API:
     - `GET /api/admin/jobs` / `{id}` for job status and config.
     - `GET /metrics` for Prometheus‑style metrics.
   - CLI:
     - `ha-backend retry-job` to reattempt failed jobs.
     - `ha-backend cleanup-job` to delete temp dirs/state for indexed jobs,
       updating `cleanup_status`.

---

## 2. Configuration & environment

### 2.1 Config module (`ha_backend/config.py`)

Key roles:

- Locate the **archive root** (`--output-dir` base) and `archive_tool` command.
- Read the **database URL**.

#### ArchiveToolConfig

```python
@dataclass
class ArchiveToolConfig:
    archive_root: Path = DEFAULT_ARCHIVE_ROOT
    archive_tool_cmd: str = DEFAULT_ARCHIVE_TOOL_CMD

    def ensure_archive_root(self) -> None:
        self.archive_root.mkdir(parents=True, exist_ok=True)
```

Defaults:

- `DEFAULT_ARCHIVE_ROOT` = `/mnt/nasd/nobak/healtharchive/jobs`
- `DEFAULT_ARCHIVE_TOOL_CMD` = `"archive-tool"`

Env overrides:

- `HEALTHARCHIVE_ARCHIVE_ROOT` → archive root.
- `HEALTHARCHIVE_TOOL_CMD` → CLI to call (e.g., `archive-tool`, `python run_archive.py`).

#### DatabaseConfig

```python
@dataclass
class DatabaseConfig:
    database_url: str = DEFAULT_DATABASE_URL
```

Defaults:

- `DEFAULT_DATABASE_URL = "sqlite:///healtharchive.db"` in the repo root.

Env override:

- `HEALTHARCHIVE_DATABASE_URL`.

### 2.2 Logging (`ha_backend/logging_config.py`)

Centralized logging configuration:

- Reads `HEALTHARCHIVE_LOG_LEVEL` (default `INFO`).
- On first call, uses `logging.basicConfig(...)` with:
  - Format: `"%(asctime)s [%(levelname)s] %(name)s: %(message)s"`.
- Adjusts noisy loggers:
  - `sqlalchemy.engine` → `WARNING`.
  - `uvicorn.access` → `INFO`.

Used in:

- `ha_backend.api.__init__` (API startup).
- `ha_backend.cli.main` (CLI entrypoint).

---

## 3. Data model (SQLAlchemy ORM)

Defined in `src/ha_backend/models.py`, with `Base` from `ha_backend.db`.

### 3.1 Source

Represents a logical content origin (e.g., Health Canada, PHAC).

Important fields:

- `id: int` (PK)
- `code: str` – short code (`"hc"`, `"phac"`) – unique, indexed.
- `name: str` – human‑readable name.
- `base_url: str | None`
- `description: str | None`
- `enabled: bool`
- Timestamps: `created_at`, `updated_at`

Relationships:

- `jobs: List[ArchiveJob]` – all jobs for this source.
- `snapshots: List[Snapshot]` – all snapshots for this source.

### 3.2 ArchiveJob

Represents a single `archive_tool` run (or family of runs) for a source.

Key fields:

- Identity:
  - `id: int` (PK)
  - `source_id: int | None` → FK to `sources.id`
  - `name: str` – must match `--name` for `archive_tool`; used in ZIM naming.
  - `output_dir: str` – host path used as `--output-dir` for `archive_tool`.

- Lifecycle/status:
  - `status: str` – high‑level state; typical values:
    - `queued`
    - `running`
    - `retryable`
    - `failed`
    - `completed` (crawl succeeded)
    - `indexing`
    - `indexed`
    - `index_failed`
  - `queued_at`, `started_at`, `finished_at`: timestamps.
  - `retry_count: int` – number of times the worker retried the crawl.

- Configuration:
  - `config: JSON | None` – “opaque” config used to reconstruct the CLI:

    ```json
    {
      "seeds": ["https://..."],
      "zimit_passthrough_args": ["--profile", "foo"],
      "tool_options": {
        "cleanup": false,
        "overwrite": false,
        "enable_monitoring": false,
        "enable_adaptive_workers": false,
        "enable_vpn_rotation": false,
        "initial_workers": 1,
        "log_level": "INFO",
        "...": "..."
      }
    }
    ```

- Crawl metrics:
  - `crawler_exit_code: int | None` – exit code from the `archive_tool` process.
  - `crawler_status: str | None` – summarised status (e.g. `"success"`, `"failed"`).
  - `crawler_stage: str | None` – last known stage (not heavily used yet).
  - `last_stats_json: JSON | None` – reserved for parsed crawl stats (if you choose to populate it later).
  - `pages_crawled`, `pages_total`, `pages_failed`: simple integer metrics (currently set by indexer if desired).

- WARC/ZIM counts:
  - `warc_file_count: int` – number of WARCs discovered for this job.
  - `indexed_page_count: int` – number of `Snapshot`s created during indexing.

- Filesystem paths:
  - `final_zim_path: str | None` – if a ZIM is produced by `archive_tool` or manual `warc2zim`.
  - `combined_log_path: str | None` – path to the latest combined log, used for stats/debugging.
  - `state_file_path: str | None` – path to `.archive_state.json` within `output_dir` (may be `None` after cleanup).

- Cleanup state (Phase 9):
  - `cleanup_status: str` – describes whether any cleanup has occurred:
    - `"none"` (default) – temp dirs & state still present (or never existed).
    - `"temp_cleaned"` – `cleanup-job` or an equivalent operation removed temp dirs/state.
    - Future values could represent more aggressive cleanup.
  - `cleaned_at: datetime | None` – when cleanup was performed.

Relationships:

- `source: Source | None` – parent source.
- `snapshots: List[Snapshot]` – all snapshots produced by this job.

### 3.3 Snapshot

Represents a single captured web page (an HTML response) extracted from a WARC.

Key fields:

- Identity:
  - `id: int` (PK)
  - `job_id: int | None` → FK to `archive_jobs.id`
  - `source_id: int | None` → FK to `sources.id`

- URL & grouping:
  - `url: str` – full URL of the capture (including query string).
  - `normalized_url_group: str | None` – optional canonicalised URL for grouping (e.g., removing query or anchors).

- Timing:
  - `capture_timestamp: datetime` – from `WARC-Date` or HTTP headers.

- HTTP & content:
  - `mime_type: str | None`
  - `status_code: int | None`
  - `title: str | None` – extracted from `<title>` or headings.
  - `snippet: str | None` – short preview text.
  - `language: str | None` – ISO language (e.g. `"en"`, `"fr"`).

- Storage / replay:
  - `warc_path: str` – path to the `.warc.gz` file on disk.
  - `warc_record_id: str | None` – WARC record identifier or offset (see `indexing.viewer`).
  - `raw_snapshot_path: str | None` – optional path to a static HTML export, if you create such stubs.
  - `content_hash: str | None` – hash of the HTML body for deduplication.

Relationships:

- `job: ArchiveJob | None`
- `source: Source | None`
- `topics: List[Topic]` – many‑to‑many via `snapshot_topics`.

### 3.4 Topic

Simple tag used for grouping snapshots by theme (COVID‑19, mpox, etc.).

- `id: int` (PK)
- `slug: str` – unique, e.g. `"covid-19"`.
- `label: str` – human‑readable label.
- `snapshots: List[Snapshot]` – many‑to‑many relation.

---

## 4. Job registry & creation (`ha_backend/job_registry.py`)

The job registry defines default behavior and seeds for each source code (`"hc"`, `"phac"`).

### 4.1 SourceJobConfig

```python
@dataclass
class SourceJobConfig:
    source_code: str
    name_template: str
    default_seeds: List[str]
    default_zimit_passthrough_args: List[str]
    default_tool_options: Dict[str, Any]
    schedule_hint: Optional[str] = None
```

Examples:

- `hc` (Health Canada):

  - `name_template = "hc-{date:%Y%m%d}"`
  - `default_seeds = ["https://www.canada.ca/en/health-canada.html"]`
  - `default_tool_options`:
    - `cleanup = False`
    - `overwrite = False`
    - `enable_monitoring = False` (can be changed per environment)
    - `enable_adaptive_workers = False`
    - `enable_vpn_rotation = False`
    - `initial_workers = 1`
    - `log_level = "INFO"`

- `phac` (Public Health Agency of Canada) is similar with a PHAC home page seed.

### 4.2 Job name and output dir

- `generate_job_name(source_cfg, now)`:
  - Renders `name_template` using `{date:%Y%m%d}` from UTC timestamp.
  - E.g. `hc-20251209`.

- `build_output_dir_for_job(source_code, job_name, archive_root, now)`:

  ```text
  <archive_root>/<source_code>/<YYYYMMDDThhmmssZ>__<job_name>
  ```

  Example:

  ```text
  /mnt/nasd/nobak/healtharchive/jobs/hc/20251209T210911Z__hc-20251209
  ```

### 4.3 Job config JSON

- `build_job_config(source_cfg, extra_seeds=None, overrides=None)`:
  - Merges `default_seeds` + extra seeds.
  - Copies `default_zimit_passthrough_args`.
  - Copies and updates `default_tool_options` with any `overrides`.
  - Performs basic validation of `tool_options` to fail fast on
    misconfiguration:

    - If `enable_adaptive_workers=True` but `enable_monitoring` is not `True`,
      a `ValueError` is raised.
    - If `enable_vpn_rotation=True` but `enable_monitoring` is not `True`,
      a `ValueError` is raised.
    - If `enable_vpn_rotation=True` but `vpn_connect_command` is missing or
      empty, a `ValueError` is raised.

Result structure:

```json
{
  "seeds": ["https://...", "..."],
  "zimit_passthrough_args": [],
  "tool_options": {
    "cleanup": false,
    "overwrite": false,
    "enable_monitoring": false,
    "enable_adaptive_workers": false,
    "enable_vpn_rotation": false,
    "initial_workers": 1,
    "log_level": "INFO"
  }
}
```

### 4.4 create_job_for_source

```python
def create_job_for_source(
    source_code: str,
    *,
    session: Session,
    overrides: Optional[Dict[str, Any]] = None,
) -> ORMArchiveJob:
```

Steps:

1. Look up `SourceJobConfig` for `source_code`.
2. Ensure a `Source` row with that code exists (or raise).
3. Resolve `archive_root` from config.
4. Generate `job_name` and `output_dir`.
5. Build `job_config`.
6. Insert an `ArchiveJob`:
   - `status="queued"`, `queued_at=now`, `config=job_config`.

The CLI command `ha-backend create-job --source hc` is a thin wrapper around this.

---

## 5. archive_tool integration & job runner (`ha_backend/jobs.py`)

### 5.1 RuntimeArchiveJob

`RuntimeArchiveJob` is a small helper for ad‑hoc runs (`ha-backend run-job`) that:

- Holds just a `name` and `seeds: list[str]`.
- Creates a timestamped job directory under the archive root (unless overridden).
- Builds the `archive_tool` CLI command.
- Executes it via `subprocess.run(...)`.

This path is used by:

- `ha-backend run-job` – direct, non‑persistent jobs.

### 5.2 run_persistent_job – DB‑backed jobs

```python
def run_persistent_job(job_id: int) -> int:
    ...
```

Responsibilities:

1. **Load job and mark running**:

   - Using `get_session()`:

     - Fetch `ArchiveJob` by ID.
     - Validate `status in ("queued", "retryable")`.
     - Extract `config`, splitting into:
       - `tool_options`
       - `zimit_passthrough_args`
       - `seeds`
     - Validate that `seeds` is non‑empty.
     - Record `output_dir` and `name`.
     - Set:
       - `status = "running"`
       - `started_at = now`

2. **Build CLI options from tool_options**:

   - Core:

     ```python
     initial_workers = int(tool_options.get("initial_workers", 1))
     cleanup = bool(tool_options.get("cleanup", False))
     overwrite = bool(tool_options.get("overwrite", False))
     log_level = str(tool_options.get("log_level", "INFO"))
     ```

   - Monitoring options:

     Only if `enable_monitoring` is `True`:

     - Adds `--enable-monitoring`.
     - Optionally:
       - `monitor_interval_seconds` → `--monitor-interval-seconds N`
       - `stall_timeout_minutes` → `--stall-timeout-minutes N`
       - `error_threshold_timeout` → `--error-threshold-timeout N`
       - `error_threshold_http` → `--error-threshold-http N`

   - Adaptive workers:

     Only if both `enable_monitoring` and `enable_adaptive_workers` are `True`:

     - Adds `--enable-adaptive-workers`.
     - Optionally:
       - `min_workers` → `--min-workers N`
       - `max_worker_reductions` → `--max-worker-reductions N`

   - VPN rotation:

     Only if `enable_monitoring`, `enable_vpn_rotation`, and `vpn_connect_command`
     are all present:

     - Adds:

       ```bash
       --enable-vpn-rotation
       --vpn-connect-command "<vpn_connect_command>"
       ```

     - Optionally:
       - `max_vpn_rotations` → `--max-vpn-rotations N`
       - `vpn_rotation_frequency_minutes` → `--vpn-rotation-frequency-minutes N`

   - Backoff:

     Only when monitoring is enabled and `backoff_delay_minutes` is set:

     - `--backoff-delay-minutes N`.

   - Zimit passthrough:

     - `zimit_passthrough_args` are appended **after** a literal `"--"` so
       `archive_tool` passes them directly to `zimit`.

   - The final `extra_args` passed to `RuntimeArchiveJob.run(...)` look like:

     ```bash
     [archive_tool_flags..., "--", zimit_passthrough_args...]
     ```

3. **Execute archive_tool**:

   - Instantiates `RuntimeArchiveJob(name, seeds)`.
   - Calls:

     ```python
     rc = runtime_job.run(
         initial_workers=initial_workers,
         cleanup=cleanup,
         overwrite=overwrite,
         log_level=log_level,
         extra_args=full_extra_args,
         stream_output=True,
         output_dir_override=Path(output_dir_str),
     )
     ```

   - `output_dir_override` ensures a specific job directory under the archive
     root (matching the DB record) is used, and created if needed.

4. **Update job status**:

   - After the subprocess returns:

     - `crawler_exit_code = rc`
     - `finished_at = now`
     - `status = "completed"` and `crawler_status = "success"` if `rc == 0`
     - Otherwise:
       - `status = "failed"`
       - `crawler_status = "failed"`

The worker uses `run_persistent_job(job_id)` for each queued job.

---

## 6. Indexing pipeline (`ha_backend/indexing/*`)

The indexing pipeline converts the WARCs produced by `archive_tool` into
structured `Snapshot` rows.

### 6.1 WARC discovery (`warc_discovery.py`)

```python
from archive_tool.state import CrawlState
from archive_tool.utils import find_all_warc_files, find_latest_temp_dir_fallback
```

```python
def discover_warcs_for_job(
    job: ArchiveJob,
    *,
    allow_fallback: bool = True,
) -> List[Path]:
```

Steps:

1. Resolve `host_output_dir = Path(job.output_dir).resolve()`.
2. Instantiate `CrawlState(host_output_dir, initial_workers=1)`:
   - This loads `.archive_state.json` if present.
3. Get `temp_dirs = state.get_temp_dir_paths()`:
   - Returns only existing directories and prunes missing ones from state.
4. If `temp_dirs` is empty and `allow_fallback`:
   - Use `find_latest_temp_dir_fallback(host_output_dir)` to scan for `.tmp*`
     directories.
5. If still empty → return `[]`.
6. Call `find_all_warc_files(temp_dirs)`:
   - Returns a de‑duplicated list of `*.warc.gz` files under each
     `collections/crawl-*/archive` directory.

This ensures the backend uses **exactly the same** WARC discovery logic as
`archive_tool` itself.

### 6.2 WARC reading (`warc_reader.py`)

Wraps `warcio` to stream HTML response records from a `.warc.gz` file.

Exports a generator like:

```python
def iter_html_records(warc_path: Path) -> Iterator[ArchiveRecord]:
    ...
```

Where `ArchiveRecord` provides:

- `url: str`
- `capture_timestamp: datetime`
- `headers: dict[str, str]`
- `body_bytes: bytes`
- `warc_path: Path`
- `warc_record_id: str | None`

### 6.3 Text extraction (`text_extraction.py`)

Helpers:

- `extract_title(html: str) -> str` – heuristics over `<title>` / headings.
- `extract_text(html: str) -> str` – uses BeautifulSoup to pull visible text.
- `make_snippet(text: str) -> str` – short preview (~N chars/words).
- `detect_language(text: str, headers: dict) -> str` – simple language detection,
  leveraging headers or heuristics (kept basic for now).

### 6.4 Mapping records to Snapshot (`mapping.py`)

`record_to_snapshot(job, source, rec, title, snippet, language)`:

- Takes:
  - `ArchiveJob`
  - `Source`
  - `ArchiveRecord` from `iter_html_records`
  - `title`, `snippet`, `language` from text extraction
- Produces a new `Snapshot` instance with:
  - `job_id`, `source_id`
  - `url`, `normalized_url_group`
  - `capture_timestamp`
  - `mime_type`, `status_code`
  - `title`, `snippet`, `language`
  - `warc_path`, `warc_record_id`
  - `content_hash` (if computed)

### 6.5 Orchestration (`pipeline.py`)

```python
def index_job(job_id: int) -> int:
```

Steps:

1. Load `ArchiveJob` by ID, ensure:
   - `job.source` is not `None`.
   - `job.status in ("completed", "index_failed", "indexed")`.
2. Validate `output_dir` exists.
3. Discover WARCs:
   - `warc_paths = discover_warcs_for_job(job)`.
   - Sets `job.warc_file_count = len(warc_paths)`.
   - If no WARCs found:
     - Logs warning.
     - Sets `job.status = "index_failed"` and returns `1`.
4. Clear previous snapshots for this job:
   - `DELETE FROM snapshots WHERE job_id = :job_id`.
5. Mark job as indexing:
   - `job.indexed_page_count = 0`, `job.status = "indexing"`.
6. For each WARC path:
   - Iterate `iter_html_records(warc_path)`.
   - Decode `html = rec.body_bytes.decode("utf-8", errors="replace")`.
   - Use text extraction functions to get `title`, `text`, `snippet`, `language`.
   - Call `record_to_snapshot(...)` to construct a `Snapshot`.
   - `session.add(snapshot)`; flush every 500 additions.
   - Count snapshots in `n_snapshots`.
   - On per‑record errors, log and continue.
7. On success:
   - Set `job.indexed_page_count = n_snapshots`.
   - Set `job.status = "indexed"`.
   - Return `0`.
8. On unexpected error:
   - Log at error level.
   - Set `job.status = "index_failed"`.
   - Return `1`.

---

## 7. Viewer helper (`ha_backend/indexing/viewer.py`)

The viewer helper is used by `GET /api/snapshots/raw/{id}` to reconstruct the
HTML for a snapshot from its WARC.

Design:

- Either:
  - Use `warc_record_id` to seek directly to a known record, or
  - Fallback to scanning `warc_path` for the first matching URL + timestamp.

The API route:

- Validates that `Snapshot` and its `warc_path` exist.
- Calls `find_record_for_snapshot(snapshot)`:
  - Returns an `ArchiveRecord` or `None`.
- Decodes `record.body_bytes` as UTF‑8 with replacement.
- Writes `HTMLResponse(content=html, media_type="text/html")`.

This is used by the Next.js frontend for the embedded snapshot viewer.

---

## 8. HTTP API (`ha_backend/api/*`)

### 8.1 Public schemas (`schemas.py`)

Public Pydantic models:

- `SourceSummarySchema` – used by `/api/sources`:

  ```python
  sourceCode: str
  sourceName: str
  recordCount: int
  firstCapture: str
  lastCapture: str
  topics: List[str]
  latestRecordId: Optional[int]
  ```

- `SnapshotSummarySchema` – used by `/api/search`:

  - `id`, `title`, `sourceCode`, `sourceName`, `language`, `topics`,
    `captureDate`, `originalUrl`, `snippet`, `rawSnapshotUrl`.

- `SearchResponseSchema`:

  - `results: List[SnapshotSummarySchema]`, `total`, `page`, `pageSize`.

- `SnapshotDetailSchema` – used by `/api/snapshot/{id}`:

  - Contains metadata for a single snapshot including `mimeType` and
    `statusCode`, plus `rawSnapshotUrl`.

### 8.2 Public routes (`routes_public.py`)

- `GET /api/health`:

  - Returns JSON with:

    ```json
    {
      "status": "ok",
      "checks": {
        "db": "ok",
        "jobs": {
          "queued": 1,
          "indexed": 5,
          ...
        },
        "snapshots": {
          "total": 12345
        }
      }
    }
    ```

  - If the DB connectivity check fails, returns HTTP 500 with
    `{"status": "error", "checks": {"db": "error"}}`.

- `GET /api/sources`:

  - Aggregates `Snapshot` by `source_id`:
    - Counts, first/last capture dates, distinct topics, latest snapshot ID.

- `GET /api/search`:

  - Query params:
    - `q: str | None` – keyword.
    - `source: str | None` – source code (e.g. `"hc"`).
    - `topic: str | None` – topic slug.
    - `page: int`, `pageSize: int`.
  - Filters:
    - `Source.code == source.lower()` when `source` set.
    - Joins `Snapshot.topics` / `Topic` when filtering by `topic`.
    - Keyword filter via `ILIKE` on `title`, `snippet`, and `url`.
  - Orders by `capture_timestamp DESC, id DESC`.

- `GET /api/snapshot/{id}`:

  - Loads `Snapshot` + `Source` + topics.
  - Returns `SnapshotDetailSchema`.
  - 404 if snapshot or source missing.

- `GET /api/snapshots/raw/{id}`:

  - Validates `Snapshot` exists and `warc_path` points to an existing file.
  - Uses `find_record_for_snapshot(snapshot)` to get a WARC record.
  - Returns HTML via `HTMLResponse`.

### 8.3 Admin auth (`deps.py`)

`require_admin` is a FastAPI dependency used to protect admin and metrics
endpoints.

Behavior:

- Reads `HEALTHARCHIVE_ADMIN_TOKEN` from env.
- If **unset**:
  - Admin endpoints are **open** (dev mode).
- If **set**:
  - Requires token via either:
    - `Authorization: Bearer <token>` header, or
    - `X-Admin-Token: <token>` header.
  - On mismatch/missing token → `HTTP 403`.

### 8.4 Admin schemas (`schemas_admin.py`)

Key models:

- `JobSummarySchema` – used for lists:

  - Contains the key job fields plus:

    ```python
    cleanupStatus: str
    cleanedAt: Optional[datetime]
    ```

- `JobDetailSchema` – extended view for a single job:

  - Includes status, worker counters, pages, WARC counts, ZIM/log/state paths,
    `config` (JSON), and `lastStats` (JSON, reserved).
  - Also includes `cleanupStatus` and `cleanedAt`.

- `JobSnapshotSummarySchema` – minimal `Snapshot` view in a job context.

- `JobListResponseSchema` – wrapper for job list results.

- `JobStatusCountsSchema` – dictionary of `{status: count}`.

### 8.5 Admin routes (`routes_admin.py`)

All routes are under `/api/admin` and use `require_admin` for auth.

- `GET /api/admin/jobs` → `JobListResponseSchema`:
  - Filters:
    - `source: str | None` – by source code.
    - `status: str | None` – by job status.
    - `limit` (1–500, default 50), `offset` (≥0).
  - Joins `ArchiveJob` with `Source` (outer join).

- `GET /api/admin/jobs/{job_id}` → `JobDetailSchema`:
  - Joins `ArchiveJob` with `Source`.
  - 404 if job not found.

- `GET /api/admin/jobs/status-counts` → `JobStatusCountsSchema`:
  - SQL: `SELECT status, COUNT(*) FROM archive_jobs GROUP BY status`.

- `GET /api/admin/jobs/{job_id}/snapshots` → `List[JobSnapshotSummarySchema]`:
  - Lists snapshots for a given job with pagination (`limit`, `offset`).

### 8.6 Metrics (Prometheus‑style)

Defined directly in `ha_backend.api.__init__`:

- `GET /metrics`:
  - Protected by `require_admin` (same token behavior).
  - Computes:
    - `healtharchive_jobs_total{status="..."}`
    - `healtharchive_jobs_cleanup_status_total{cleanup_status="..."}`
    - `healtharchive_snapshots_total`
    - `healtharchive_snapshots_total{source="hc"}`, etc.
  - Returns plain‑text body with HELP/TYPE comments suitable for Prometheus.

---

## 9. Worker loop (`ha_backend/worker/main.py`)

The worker processes jobs end‑to‑end: crawl and index.

### 9.1 Selection

`_select_next_crawl_job(session)`:

- Query:

  ```python
  session.query(ArchiveJob) \
    .join(Source) \
    .filter(ArchiveJob.status.in_(["queued", "retryable"])) \
    .order_by(ArchiveJob.queued_at.asc().nullsfirst(),
              ArchiveJob.created_at.asc()) \
    .first()
  ```

- Chooses the oldest queued/retryable job, preferring jobs with the earliest
  `queued_at`.

### 9.2 Processing a single job

`_process_single_job()`:

1. Select a job → get `job_id`.
2. Run `run_persistent_job(job_id)`:
   - Executes `archive_tool` and returns a process exit code.
3. Reload job in a new session and apply retry semantics:
   - If `crawl_rc != 0` or `job.status == "failed"`:
     - If `job.retry_count < MAX_CRAWL_RETRIES`:
       - Increment `job.retry_count`.
       - Set `job.status = "retryable"`.
     - Else:
       - Log error; job remains in `failed`.
   - Else (crawl succeeded):
     - Log that indexing will start.
4. If crawl succeeded:
   - Run `index_job(job_id)`.
   - Log success/failure for indexing.

Returns `True` if a job was processed, `False` if no jobs were found.

### 9.3 Main loop

`run_worker_loop(poll_interval=30, run_once=False)`:

- Logs startup with the given interval and `run_once`.
- In a loop:
  - Calls `_process_single_job()`.
  - If `run_once` → break after first iteration.
  - If no job processed:
    - Logs and sleeps for `poll_interval` seconds.
- Handles `KeyboardInterrupt` gracefully.

---

## 10. Cleanup & retention (Phase 9)

Job‑level cleanup is focused on removing **temporary crawl artifacts** (`.tmp*`
dirs and `.archive_state.json`) after indexing is complete.

### 10.1 Cleanup flags on ArchiveJob

New fields:

- `cleanup_status: str`:
  - `"none"` – no cleanup performed (default).
  - `"temp_cleaned"` – temporary dirs and state file have been deleted.
  - Future values could represent more aggressive cleanup modes.
- `cleaned_at: datetime | None` – when cleanup occurred.

These fields are exposed through:

- Admin schemas (`JobSummarySchema`, `JobDetailSchema`).
- Metrics (`healtharchive_jobs_cleanup_status_total`).

### 10.2 CLI command: cleanup-job

`ha-backend cleanup-job --id JOB_ID [--mode temp]`

Implementation notes:

- Currently supports only `--mode temp`:
  - Any other mode → error.

- Behavior:

  1. Load the `ArchiveJob` by ID.
  2. If job is missing → error, exit 1.
  3. If `job.status` is **not** one of:
     - `"indexed"` – indexing completed successfully, or
     - `"index_failed"` – indexing failed and you have decided not to retry,
     then refuse cleanup and exit 1.
     - This ensures we don’t delete temp dirs while a job might still be
       resumed or indexing is in progress.
  4. Validate `output_dir` exists and is a directory.
  5. Use `archive_tool.state.CrawlState(output_dir, initial_workers=1)` to
     instantiate state and locate the state file.
  6. Use `state.get_temp_dir_paths()` to get known temp dirs; fall back to
     `find_latest_temp_dir_fallback` if none are tracked.
  7. If neither temp dirs nor the state file exist:
     - Print a message that there is nothing to clean up and **do not** change
       `cleanup_status` or `cleaned_at`.
  8. Otherwise (if temp dirs and/or state file exist):
     - Call `cleanup_temp_dirs(temp_dirs, state.state_file_path)`:
       - Deletes `.tmp*` directories and the `.archive_state.json`.
     - Update job:
       - `cleanup_status = "temp_cleaned"`
       - `cleaned_at = now`
       - `state_file_path = None`

> **Caution:** This cleanup removes WARCs stored under `.tmp*` directories,
> consistent with `archive_tool`’s own `--cleanup` behavior. In v1 you should
> only run it once you have:
> - Indexed the job successfully (`status="indexed"`), and
> - Verified any desired ZIM or exports derived from these WARCs.

### 10.3 Metrics for cleanup

`/metrics` includes:

- `healtharchive_jobs_cleanup_status_total{cleanup_status="none"}`
- `healtharchive_jobs_cleanup_status_total{cleanup_status="temp_cleaned"}`

This gives a quick overview of how many jobs still have temp artifacts versus
those that have been cleaned.

---

## 11. CLI commands summary

All commands are available via the `ha-backend` entrypoint.

- Environment / connectivity:
  - `check-env` – show archive root and ensure it exists.
  - `check-archive-tool` – run `archive-tool --help`.
  - `check-db` – simple DB connectivity check.

- Direct, non‑persistent job:
  - `run-job` – run `archive_tool` immediately with explicit `--name`, `--seeds`,
    `--initial-workers`, etc.

- Persistent jobs (DB‑backed):
  - `create-job --source CODE` – create `ArchiveJob` using registry defaults.
  - `run-db-job --id ID` – run `archive_tool` for an existing job.
  - `index-job --id ID` – index an existing job’s WARCs into snapshots.
  - `register-job-dir --source CODE --output-dir PATH [--name NAME]` –
    attach a DB `ArchiveJob` to an existing archive_tool output directory
    (useful when a crawl has already been run and you want to index its
    WARCs).

- Seeding:
  - `seed-sources` – insert baseline `Source` rows for `hc`, `phac`.

- Admin / introspection:
  - `list-jobs` – list recent jobs with basic fields.
  - `show-job --id ID` – detailed job info including config.
  - `retry-job --id ID` – mark:
    - `failed` jobs as `retryable` (for another crawl).
    - `index_failed` jobs as `completed` (for re-indexing).
  - `cleanup-job --id ID [--mode temp]` – cleanup temp dirs/state for jobs in
    status `indexed` or `index_failed`.
  - `start-worker [--poll-interval N] [--once]` – start the worker loop.

---

## 12. Testing & development

- Tests are written with `pytest` and live under `tests/`.
- To run tests:

  ```bash
  pip install -e ".[dev]"
  pytest -q
  ```

- Many tests configure a temporary SQLite DB by:
  - Setting `HEALTHARCHIVE_DATABASE_URL` to a temp file.
  - Resetting `db_module._engine` and `_SessionLocal`.
  - Calling `Base.metadata.drop_all()` / `create_all()` to fully reset the schema.

This allows development and CI to run in isolated environments without
touching real data.

---

## 13. Relationship to archive_tool and the frontend

- **archive_tool**:
  - Lives under `src/archive_tool/`, vendored from the upstream project.
  - The backend calls it strictly via the CLI (`archive-tool`) as a subprocess.
  - Its internal behavior (Docker orchestration, run modes, monitoring,
    adaptive strategies) is documented in
    `src/archive_tool/docs/documentation.md`.

- **Frontend (healtharchive-frontend)**:
  - Next.js 16 app using the backend’s HTTP APIs:
    - `/api/health`
    - `/api/sources`
    - `/api/search`
    - `/api/snapshot/{id}`
    - `/api/snapshots/raw/{id}`
  - The frontend currently still supports a demo dataset, but is gradually
    being wired to these real APIs.

Together, the backend + `archive_tool` + frontend form a pipeline from:

> Web → crawl (Docker + `zimit`) → WARCs → Snapshots in DB → searchable
> archive UI at HealthArchive.ca.
