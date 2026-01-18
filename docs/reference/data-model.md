# Data Model Reference

Quick reference for HealthArchive database models.

**Full details**: See [Architecture Guide](../architecture.md#3-data-model-sqlalchemy-orm)

---

## Entity Relationship

```mermaid
erDiagram
    Source ||--o{ ArchiveJob : has
    Source ||--o{ Snapshot : has
    ArchiveJob ||--o{ Snapshot : produces

    Source {
        int id PK
        string code UK
        string name
        string base_url
        bool enabled
    }

    ArchiveJob {
        int id PK
        int source_id FK
        string name
        string status
        string output_dir
        json config
        int warc_file_count
        int indexed_page_count
    }

    Snapshot {
        int id PK
        int job_id FK
        int source_id FK
        string url
        string normalized_url_group
        datetime capture_timestamp
        string title
        string snippet
        string language
    }
```

---

## Source

Represents a content origin (e.g., Health Canada, PHAC).

**Table**: `sources`

| Field | Type | Nullable | Description |
|-------|------|----------|-------------|
| `id` | Integer | No | Primary key |
| `code` | String(50) | No | Unique short code (`"hc"`, `"phac"`) |
| `name` | String(200) | No | Human-readable name |
| `base_url` | String(500) | Yes | Base URL of source |
| `description` | Text | Yes | Optional description |
| `enabled` | Boolean | No | Whether source is active (default: `true`) |
| `created_at` | DateTime | No | Creation timestamp |
| `updated_at` | DateTime | No | Last update timestamp |

**Indexes**:
- Unique on `code`

**Relationships**:
- `jobs`: One-to-many → `ArchiveJob`
- `snapshots`: One-to-many → `Snapshot`

---

## ArchiveJob

Represents a single crawl job execution.

**Table**: `archive_jobs`

| Field | Type | Nullable | Description |
|-------|------|----------|-------------|
| **Identity** ||||
| `id` | Integer | No | Primary key |
| `source_id` | Integer | Yes | Foreign key → `sources.id` |
| `name` | String(200) | No | Job name (used in ZIM naming) |
| `output_dir` | String(500) | No | Absolute path to job directory |
| **Lifecycle** ||||
| `status` | String(50) | No | `queued`, `running`, `completed`, `failed`, `indexing`, `indexed`, etc. |
| `queued_at` | DateTime | Yes | When job was queued |
| `started_at` | DateTime | Yes | When crawl started |
| `finished_at` | DateTime | Yes | When crawl finished |
| `retry_count` | Integer | No | Number of retry attempts (default: 0) |
| **Configuration** ||||
| `config` | JSON | Yes | Job configuration (seeds, tool_options, zimit args) |
| **Crawl Metrics** ||||
| `crawler_exit_code` | Integer | Yes | Exit code from archive_tool process |
| `crawler_status` | String(50) | Yes | Summarized status (`"success"`, `"failed"`) |
| `crawler_stage` | String(50) | Yes | Last known stage |
| `last_stats_json` | JSON | Yes | Parsed crawl stats from logs |
| `pages_crawled` | Integer | Yes | Pages successfully crawled |
| `pages_total` | Integer | Yes | Total pages discovered |
| `pages_failed` | Integer | Yes | Pages that failed to crawl |
| **Indexing** ||||
| `warc_file_count` | Integer | No | Number of WARC files discovered (default: 0) |
| `indexed_page_count` | Integer | No | Number of snapshots created (default: 0) |
| **File Paths** ||||
| `final_zim_path` | String(500) | Yes | Path to ZIM file (if built) |
| `combined_log_path` | String(500) | Yes | Path to combined crawl log |
| `state_file_path` | String(500) | Yes | Path to `.archive_state.json` |
| **Cleanup** ||||
| `cleanup_status` | String(50) | No | `"none"`, `"temp_cleaned"` (default: `"none"`) |
| `cleaned_at` | DateTime | Yes | When cleanup was performed |
| **Timestamps** ||||
| `created_at` | DateTime | No | Record creation |
| `updated_at` | DateTime | No | Last update |

**Indexes**:
- Index on `source_id`
- Index on `status`
- Index on `queued_at`

**Relationships**:
- `source`: Many-to-one → `Source`
- `snapshots`: One-to-many → `Snapshot`

---

## Snapshot

Represents a single captured web page.

**Table**: `snapshots`

| Field | Type | Nullable | Description |
|-------|------|----------|-------------|
| **Identity** ||||
| `id` | Integer | No | Primary key |
| `job_id` | Integer | Yes | Foreign key → `archive_jobs.id` |
| `source_id` | Integer | Yes | Foreign key → `sources.id` |
| **URL & Grouping** ||||
| `url` | String(2000) | No | Full URL of captured page |
| `normalized_url_group` | String(2000) | Yes | Canonical URL for grouping |
| **Timing** ||||
| `capture_timestamp` | DateTime | No | When page was captured (from WARC) |
| **HTTP & Content** ||||
| `mime_type` | String(100) | Yes | MIME type (usually `"text/html"`) |
| `status_code` | Integer | Yes | HTTP status code |
| `title` | String(500) | Yes | Extracted page title |
| `snippet` | Text | Yes | Short text preview |
| `language` | String(10) | Yes | ISO language code (`"en"`, `"fr"`) |
| **Storage/Replay** ||||
| `warc_path` | String(500) | No | Path to WARC file |
| `warc_record_id` | String(200) | Yes | WARC record identifier |
| `raw_snapshot_path` | String(500) | Yes | Optional static HTML export path |
| `content_hash` | String(64) | Yes | Hash of HTML body (for deduplication) |
| **Timestamps** ||||
| `created_at` | DateTime | No | Record creation |
| `updated_at` | DateTime | No | Last update |

**Indexes**:
- Index on `job_id`
- Index on `source_id`
- Index on `url`
- Index on `normalized_url_group`
- Index on `capture_timestamp`
- Index on `status_code`

**Relationships**:
- `job`: Many-to-one → `ArchiveJob`
- `source`: Many-to-one → `Source`

---

## Job Status Lifecycle

```mermaid
stateDiagram-v2
    [*] --> queued: create-job
    queued --> running: worker starts
    running --> completed: crawl succeeds
    running --> failed: crawl fails
    failed --> retryable: retry if count < MAX
    retryable --> running: worker retries
    completed --> indexing: index-job starts
    indexing --> indexed: indexing succeeds
    indexing --> index_failed: indexing fails
    indexed --> [*]
    failed --> [*]
    index_failed --> completed: retry-job (reindex)
```

**Common status values**:
- `queued` - Job created, waiting for worker
- `running` - Crawl in progress
- `completed` - Crawl succeeded
- `failed` - Crawl failed (terminal if retries exhausted)
- `retryable` - Failed but can retry
- `indexing` - WARC indexing in progress
- `indexed` - Fully indexed and ready to serve
- `index_failed` - Indexing failed

---

## Config JSON Schema

**ArchiveJob.config** structure:

```json
{
  "seeds": [
    "https://www.canada.ca/en/health-canada.html"
  ],
  "zimit_passthrough_args": [
    "--profile", "social-media"
  ],
  "tool_options": {
    "cleanup": false,
    "overwrite": false,
    "enable_monitoring": false,
    "enable_adaptive_workers": false,
    "enable_vpn_rotation": false,
    "initial_workers": 1,
    "log_level": "INFO",
    "relax_perms": true,
    "monitor_interval_seconds": 30,
    "stall_timeout_minutes": 30,
    "error_threshold_timeout": 10,
    "error_threshold_http": 10,
    "min_workers": 1,
    "max_worker_reductions": 2,
    "vpn_connect_command": "vpn connect ca",
    "max_vpn_rotations": 3,
    "vpn_rotation_frequency_minutes": 60,
    "backoff_delay_minutes": 15
  }
}
```

**See**: [Job Registry](../architecture.md#4-job-registry--creation-ha_backendjob_registrypy) for defaults per source

---

## Database Configuration

**Location**: `src/ha_backend/models.py`

**ORM**: SQLAlchemy 2.0

**Migrations**: Alembic (in `alembic/` directory)

### Running Migrations

```bash
# Upgrade to latest
alembic upgrade head

# Downgrade one revision
alembic downgrade -1

# Show current revision
alembic current

# Show migration history
alembic history
```

### Supported Databases

- **SQLite** (default for dev): `sqlite:///healtharchive.db`
- **PostgreSQL** (recommended for production): `postgresql://user:pass@host/dbname`

**Environment variable**: `HEALTHARCHIVE_DATABASE_URL`

---

## Common Queries

### Find jobs by status

```python
from ha_backend.models import ArchiveJob
from ha_backend.db import get_session

session = get_session()
jobs = session.query(ArchiveJob).filter_by(status="queued").all()
```

### Get source with all jobs

```python
from ha_backend.models import Source

session = get_session()
source = session.query(Source).filter_by(code="hc").one()
print(f"{source.name}: {len(source.jobs)} jobs")
```

### Find snapshots by URL

```python
from ha_backend.models import Snapshot

session = get_session()
snapshots = session.query(Snapshot).filter(
    Snapshot.url.like("%health-canada%")
).limit(10).all()
```

---

## Related Documentation

- **Full Architecture Guide**: [architecture.md](../architecture.md)
- **Job Creation**: [architecture.md#4-job-registry--creation](../architecture.md#4-job-registry--creation-ha_backendjob_registrypy)
- **Indexing Pipeline**: [architecture.md#6-indexing-pipeline](../architecture.md#6-indexing-pipeline-ha_backendindexing)
- **CLI Commands**: [cli-commands.md](cli-commands.md)
