# CLI Commands Reference

Complete reference for `ha-backend` command-line interface.

---

## Installation

The `ha-backend` command is installed when you install the package:

```bash
pip install -e .
# or
make venv
```

Verify installation:
```bash
ha-backend --help
```

---

## Command Categories

| Category | Commands |
|----------|----------|
| **Environment** | `check-env`, `check-archive-tool`, `check-db` |
| **Job Management** | `create-job`, `run-db-job`, `index-job`, `register-job-dir` |
| **Direct Execution** | `run-job` |
| **Inspection** | `list-jobs`, `show-job` |
| **Maintenance** | `retry-job`, `cleanup-job`, `replay-index-job` |
| **Seeding** | `seed-sources` |
| **Worker** | `start-worker` |
| **Change Tracking** | `compute-changes` |

---

## Environment Commands

### check-env

Check environment configuration and ensure archive root exists.

**Usage**:
```bash
ha-backend check-env
```

**Output**:
```
Archive root: /mnt/nasd/nobak/healtharchive/jobs
Archive root exists: True
Archive tool command: archive-tool
```

**Exit codes**:
- `0` - Success
- `1` - Archive root missing

---

### check-archive-tool

Verify archive-tool is available and functional.

**Usage**:
```bash
ha-backend check-archive-tool
```

**What it does**:
- Runs `archive-tool --help`
- Validates command is available

**Exit codes**:
- `0` - archive-tool available
- `1` - archive-tool not found or failed

---

### check-db

Test database connectivity.

**Usage**:
```bash
ha-backend check-db
```

**Output**:
```
Database connection successful
```

**Exit codes**:
- `0` - Database reachable
- `1` - Connection failed

---

## Job Management Commands

### create-job

Create a new archive job using source defaults.

**Usage**:
```bash
ha-backend create-job --source SOURCE_CODE [--override JSON]
```

**Arguments**:
- `--source`, `-s` (required) - Source code (`hc`, `phac`)
- `--override` (optional) - JSON string with config overrides

**Examples**:

```bash
# Create Health Canada job with defaults
ha-backend create-job --source hc

# Create with custom worker count
ha-backend create-job --source hc --override '{"tool_options": {"initial_workers": 2}}'

# Create a "search-first" crawl (skip optional .zim build) with a larger Docker /dev/shm
ha-backend create-job --source hc --override '{"tool_options": {"initial_workers": 2, "skip_final_build": true, "docker_shm_size": "1g"}}'

# Enable monitoring and stall detection
ha-backend create-job --source phac --override '{
  "tool_options": {
    "enable_monitoring": true,
    "stall_timeout_minutes": 60
  }
}'
```

**Output**:
```
Created job ID: 42
Name: hc-20260118
Output directory: /mnt/nasd/nobak/healtharchive/jobs/hc/20260118T210911Z__hc-20260118
Status: queued
```

**Exit codes**:
- `0` - Job created successfully
- `1` - Failed (invalid source, config validation error)

---

### run-db-job

Execute a queued job by ID.

**Usage**:
```bash
ha-backend run-db-job --id JOB_ID
```

**Arguments**:
- `--id` (required) - Job ID to run

**Example**:
```bash
ha-backend run-db-job --id 42
```

**What it does**:
1. Validates job status is `queued` or `retryable`
2. Sets status to `running`
3. Executes archive-tool subprocess
4. Updates status to `completed` or `failed`

**Exit codes**:
- `0` - Crawl succeeded
- `1` - Crawl failed or job invalid

---

### index-job

Index WARCs from a completed job into the database.

**Usage**:
```bash
ha-backend index-job --id JOB_ID
```

**Arguments**:
- `--id` (required) - Job ID to index

**Example**:
```bash
ha-backend index-job --id 42
```

**What it does**:
1. Discovers WARC files in job output directory
2. Parses WARC records
3. Extracts text, title, snippet
4. Creates Snapshot rows
5. Sets job status to `indexed`

**Output**:
```
Indexing job 42...
Found 245 WARC files
Indexed 12,347 snapshots
Job status: indexed
```

**Exit codes**:
- `0` - Indexing succeeded
- `1` - Failed (no WARCs, parsing error)

---

### register-job-dir

Attach an existing archive_tool output directory to a new database job.

**Usage**:
```bash
ha-backend register-job-dir --source SOURCE --output-dir PATH [--name NAME]
```

**Arguments**:
- `--source` (required) - Source code
- `--output-dir` (required) - Existing directory path
- `--name` (optional) - Job name (default: derived from directory)

**Example**:
```bash
ha-backend register-job-dir \
  --source hc \
  --output-dir /mnt/nasd/nobak/healtharchive/jobs/hc/20260101T120000Z__hc-20260101
```

**Use case**: Import externally-run crawls into database

**Exit codes**:
- `0` - Job registered
- `1` - Directory doesn't exist or validation failed

---

## Direct Execution

### run-job

Run archive-tool directly without database persistence.

**Usage**:
```bash
ha-backend run-job \
  --name NAME \
  --seeds URL [URL...] \
  [--initial-workers N] \
  [--output-dir DIR]
```

**Arguments**:
- `--name` (required) - Job name
- `--seeds` (required) - One or more seed URLs
- `--initial-workers` (optional) - Worker count (default: 1)
- `--output-dir` (optional) - Output directory (default: auto-generated)

**Example**:
```bash
ha-backend run-job \
  --name test-crawl \
  --seeds https://www.canada.ca/en/health-canada.html \
  --initial-workers 2
```

**Use case**: Quick testing without database overhead

**Exit codes**:
- `0` - Crawl succeeded
- Non-zero - archive-tool exit code

---

## Inspection Commands

### list-jobs

List recent jobs with summary information.

**Usage**:
```bash
ha-backend list-jobs [--limit N] [--status STATUS] [--source SOURCE]
```

**Arguments**:
- `--limit` (optional) - Number of jobs to show (default: 20)
- `--status` (optional) - Filter by status
- `--source` (optional) - Filter by source code

**Examples**:
```bash
# List 20 most recent jobs
ha-backend list-jobs

# Show only failed jobs
ha-backend list-jobs --status failed

# Show Health Canada jobs
ha-backend list-jobs --source hc

# Show last 50 jobs
ha-backend list-jobs --limit 50
```

**Output**:
```
ID  Name            Source  Status    Queued              Started             Finished            Pages
42  hc-20260118     hc      indexed   2026-01-18 20:00    2026-01-18 20:05    2026-01-18 21:30    12,347
41  phac-20260117   phac    completed 2026-01-17 19:00    2026-01-17 19:10    2026-01-17 20:45    8,234
```

---

### show-job

Display detailed information about a specific job.

**Usage**:
```bash
ha-backend show-job --id JOB_ID [--format {text|json}]
```

**Arguments**:
- `--id` (required) - Job ID
- `--format` (optional) - Output format (default: `text`)

**Examples**:
```bash
# Human-readable output
ha-backend show-job --id 42

# JSON output (for scripting)
ha-backend show-job --id 42 --format json
```

**Output** (text format):
```
Job ID: 42
Name: hc-20260118
Source: Health Canada (hc)
Status: indexed
Output Directory: /mnt/nasd/nobak/healtharchive/jobs/hc/20260118T210911Z__hc-20260118

Timeline:
  Queued:  2026-01-18 20:00:00
  Started: 2026-01-18 20:05:00
  Finished: 2026-01-18 21:30:00
  Duration: 1h 25m

Crawl Metrics:
  Exit Code: 0
  Status: success
  Pages Crawled: 12,347
  Pages Total: 12,500
  Pages Failed: 153

Indexing:
  WARC Files: 245
  Snapshots: 12,347

Cleanup:
  Status: none
```

---

## Maintenance Commands

### retry-job

Retry a failed or index-failed job.

**Usage**:
```bash
ha-backend retry-job --id JOB_ID
```

**Arguments**:
- `--id` (required) - Job ID to retry

**Example**:
```bash
ha-backend retry-job --id 42
```

**What it does**:
- If job status is `failed`: Sets to `retryable` (for re-crawl)
- If job status is `index_failed`: Sets to `completed` (for re-index)

**Exit codes**:
- `0` - Job marked for retry
- `1` - Job not in retryable state

---

### cleanup-job

Clean up temporary crawl artifacts.

**Usage**:
```bash
ha-backend cleanup-job --id JOB_ID [--mode MODE] [--force]
```

**Arguments**:
- `--id` (required) - Job ID
- `--mode` (optional) - Cleanup mode (default: `temp`, only supported value)
- `--force` (optional) - Force cleanup even if replay is enabled

**Example**:
```bash
# Clean up temp directories and state file
ha-backend cleanup-job --id 42 --mode temp

# Force cleanup (use with caution)
ha-backend cleanup-job --id 42 --mode temp --force
```

**What it does**:
- Removes `.tmp*` directories
- Removes `.archive_state.json`
- Updates job: `cleanup_status = "temp_cleaned"`, `cleaned_at = now`

**⚠️ Warning**: This deletes WARCs if they're in `.tmp*` directories. Only run on indexed jobs where you don't need replay.

**Exit codes**:
- `0` - Cleanup succeeded
- `1` - Failed (job not indexed, replay enabled without --force)

---

### replay-index-job

Create/refresh pywb collection index for a job.

**Usage**:
```bash
ha-backend replay-index-job --id JOB_ID
```

**Arguments**:
- `--id` (required) - Job ID

**Example**:
```bash
ha-backend replay-index-job --id 42
```

**What it does**:
- Creates pywb collection for job WARCs
- Generates CDX index for fast replay
- Enables browsing via pywb

**Prerequisites**:
- `HEALTHARCHIVE_REPLAY_BASE_URL` set
- pywb installed and configured

**Exit codes**:
- `0` - Index created
- `1` - Failed or replay not configured

---

## Seeding

### seed-sources

Initialize source records in the database.

**Usage**:
```bash
ha-backend seed-sources
```

**What it does**:
- Inserts `Source` rows for `hc` and `phac`
- Idempotent (safe to run multiple times)

**Example**:
```bash
ha-backend seed-sources
```

**Output**:
```
Seeded source: hc (Health Canada)
Seeded source: phac (Public Health Agency of Canada)
```

**Exit codes**:
- `0` - Sources seeded or already exist

---

## Worker

### start-worker

Start the job processing worker loop.

**Usage**:
```bash
ha-backend start-worker [--poll-interval SECONDS] [--once]
```

**Arguments**:
- `--poll-interval` (optional) - Seconds between polls (default: 30)
- `--once` (optional) - Process one job then exit

**Examples**:
```bash
# Run continuously with 30s polling
ha-backend start-worker

# Poll every 60 seconds
ha-backend start-worker --poll-interval 60

# Process one job and exit (for testing)
ha-backend start-worker --once
```

**What it does**:
1. Polls for jobs with status `queued` or `retryable`
2. Runs oldest job first
3. Crawls → Indexes → Repeats
4. Sleeps if no jobs found

**Exit**: Press Ctrl+C to stop gracefully

---

## Change Tracking

### compute-changes

Compute change events between adjacent snapshots.

**Usage**:
```bash
ha-backend compute-changes [--limit N] [--source SOURCE]
```

**Arguments**:
- `--limit` (optional) - Max snapshot groups to process
- `--source` (optional) - Limit to specific source

**Example**:
```bash
# Compute changes for all snapshots
ha-backend compute-changes

# Process 100 page groups
ha-backend compute-changes --limit 100

# Only Health Canada changes
ha-backend compute-changes --source hc
```

**What it does**:
- Groups snapshots by `normalized_url_group`
- Compares adjacent captures (by timestamp)
- Generates `SnapshotChange` rows with diff metadata

**Exit codes**:
- `0` - Changes computed
- `1` - Error

---

## Global Options

All commands support:

```bash
ha-backend COMMAND --help  # Show command help
```

## Environment Variables

Commands respect these environment variables:

| Variable | Purpose | Default |
|----------|---------|---------|
| `HEALTHARCHIVE_DATABASE_URL` | Database connection | `sqlite:///healtharchive.db` |
| `HEALTHARCHIVE_ARCHIVE_ROOT` | Base directory for jobs | `/mnt/nasd/nobak/healtharchive/jobs` |
| `HEALTHARCHIVE_TOOL_CMD` | archive-tool command | `archive-tool` |
| `HEALTHARCHIVE_LOG_LEVEL` | Logging level | `INFO` |

**Set in `.env` file**:
```bash
HEALTHARCHIVE_DATABASE_URL=postgresql://user:pass@localhost/healtharchive
HEALTHARCHIVE_ARCHIVE_ROOT=/data/healtharchive/jobs
HEALTHARCHIVE_LOG_LEVEL=DEBUG
```

---

## Exit Codes

Standard exit codes:
- `0` - Success
- `1` - General error
- `2` - Command-line usage error

---

## Scripting Examples

### Process a job end-to-end

```bash
#!/bin/bash
set -e

# Create job
JOB_ID=$(ha-backend create-job --source hc | grep "Created job ID:" | awk '{print $4}')
echo "Created job $JOB_ID"

# Run crawl
ha-backend run-db-job --id $JOB_ID

# Index WARCs
ha-backend index-job --id $JOB_ID

# Clean up
ha-backend cleanup-job --id $JOB_ID --mode temp

echo "Job $JOB_ID complete"
```

### Monitor worker

```bash
#!/bin/bash

while true; do
  clear
  echo "=== Job Status ==="
  ha-backend list-jobs --limit 10
  sleep 10
done
```

### Retry all failed jobs

```bash
#!/bin/bash

ha-backend list-jobs --status failed --limit 100 --format json | \
  jq -r '.[].id' | \
  while read job_id; do
    echo "Retrying job $job_id"
    ha-backend retry-job --id $job_id
  done
```

---

## Related Documentation

- **Architecture Guide**: [../architecture.md](../architecture.md)
- **Job Registry**: [../architecture.md#4-job-registry--creation](../architecture.md#4-job-registry--creation-ha_backendjob_registrypy)
- **Worker Loop**: [../architecture.md#9-worker-loop](../architecture.md#9-worker-loop-ha_backendworkermainpy)
- **Data Model**: [data-model.md](data-model.md)
- **Live Testing**: [../development/live-testing.md](../development/live-testing.md)
