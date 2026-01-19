# Archive Tool Reference

The **archive_tool** is HealthArchive's internal crawler and orchestrator subpackage.

---

## Quick Overview

**archive_tool** is a Docker-based web crawler that:
- Wraps the `zimit` crawler (from OpenZIM)
- Manages crawl state and resumption
- Monitors crawl health (stall detection, error thresholds)
- Supports adaptive worker scaling
- Optionally rotates VPN connections

**Location**: `src/archive_tool/`

**Technology**:
- Python 3.11+
- Docker (runs `ghcr.io/openzim/zimit` container)
- State persistence (`.archive_state.json`)

---

## Architecture

```
┌─────────────────────────────────────────────────┐
│ HealthArchive Backend                           │
│                                                 │
│  ha_backend.jobs.run_persistent_job()           │
│         │                                       │
│         ├──> Builds CLI args from job config   │
│         │                                       │
│         └──> subprocess.run()                   │
│                     │                           │
└─────────────────────┼───────────────────────────┘
                      │
                      ▼
         ┌────────────────────────────┐
         │   archive-tool CLI         │
         │   (archive_tool/cli.py)    │
         └────────────┬───────────────┘
                      │
                      ├──> Validates Docker
                      ├──> Determines run mode
                      ├──> Spawns zimit in Docker
                      ├──> Monitors progress
                      ├──> Writes WARCs to .tmp_N/
                      └──> Builds ZIM (optional)
```

---

## Canonical Documentation

**Full technical reference**: `src/archive_tool/docs/documentation.md`

**1,508 lines covering**:
- CLI interface and all flags
- Run modes (Fresh, Resume, New-with-Consolidation, Overwrite)
- State management (`.archive_state.json`)
- Docker orchestration details
- Monitoring and adaptive workers
- VPN rotation mechanism
- WARC discovery and consolidation
- Error handling and recovery
- Testing and development

**Read the full docs for**:
- Detailed CLI flag reference
- State machine diagrams
- Docker volume mapping
- Log parsing internals
- Adding new features to archive_tool

---

## Quick Reference

### CLI Usage

```bash
archive-tool \
  --name CRAWL_NAME \
  --output-dir /path/to/output \
  --initial-workers N \
  [--enable-monitoring] \
  [--enable-adaptive-workers] \
  [--enable-vpn-rotation --vpn-connect-command "..."] \
  SEED_URL [SEED_URL...]
```

### Common Flags

| Flag | Purpose |
|------|---------|
| `--name` | Crawl name (used in ZIM filename) |
| `--output-dir` | Output directory path |
| `--initial-workers` | Number of parallel workers (default: 1) |
| `--enable-monitoring` | Enable stall/error detection |
| `--stall-timeout-minutes` | Abort if no progress (requires monitoring) |
| `--enable-adaptive-workers` | Reduce workers on errors (requires monitoring) |
| `--enable-vpn-rotation` | Rotate VPN on stalls (requires monitoring) |
| `--cleanup` | Delete temp dirs after successful crawl |
| `--overwrite` | Delete existing output before starting |

### Run Modes

archive-tool automatically determines the run mode based on state:

1. **Fresh** - No prior state, start new crawl
2. **Resume** - State exists and incomplete, resume from checkpoint
3. **New-with-Consolidation** - State complete, start new crawl but consolidate WARCs
4. **Overwrite** - `--overwrite` flag set, delete everything and start fresh

**See**: `src/archive_tool/docs/documentation.md` (Run Modes) for decision tree

### Output Structure

```
output_dir/
├── .archive_state.json              # Persistent state
├── .tmp_1/                          # First crawl attempt
│   └── collections/
│       └── crawl-YYYYMMDD.../
│           ├── archive/             # WARCs here
│           │   ├── rec-00000-....warc.gz
│           │   └── rec-00001-....warc.gz
│           └── logs/
├── .tmp_2/                          # Second attempt (if restarted)
├── archive_STAGE_TIMESTAMP.log      # Individual stage logs
├── archive_STAGE_TIMESTAMP.combined.log  # Aggregated logs
└── zim/
    └── NAME_DATE.zim                # Optional ZIM file
```

### State File Format

**`.archive_state.json`**:
```json
{
  "current_workers": 4,
  "initial_workers": 4,
  "temp_dirs_host_paths": ["/some/output/.tmp123", "..."],
  "vpn_rotations_done": 1,
  "worker_reductions_done": 1,
  "container_restarts_done": 1
}
```

---

## Backend Integration

The backend calls archive-tool via subprocess. Key files:

### Job Execution

**`ha_backend/jobs.py:run_persistent_job()`** (lines 439-560):
- Loads `ArchiveJob.config` from database
- Translates `tool_options` to CLI flags
- Builds command: `archive-tool --flag1 val1 --flag2 val2 ... SEEDS`
- Executes with `subprocess.run()`
- Updates job status based on exit code

**Config → CLI Mapping**:
```python
config["tool_options"]["enable_monitoring"] → --enable-monitoring
config["tool_options"]["initial_workers"] → --initial-workers N
config["tool_options"]["stall_timeout_minutes"] → --stall-timeout-minutes N
```

### WARC Discovery

**`ha_backend/indexing/warc_discovery.py`**:
- Uses `archive_tool.state.CrawlState` to load `.archive_state.json`
- Uses `archive_tool.utils.find_all_warc_files()` to locate WARCs
- Ensures backend and archive-tool use identical logic

### Cleanup

**`ha_backend/cli/cmd_cleanup_job.py`**:
- Uses `archive_tool.utils.cleanup_temp_dirs()` to remove `.tmp*` directories
- Deletes `.archive_state.json`
- Updates `ArchiveJob.cleanup_status`

---

## Monitoring Features

### Stall Detection

When `--enable-monitoring` is set:
- Monitors log output every `--monitor-interval-seconds` (default: 30)
- Parses "Crawl statistics" JSON from logs
- Detects stalls: no new pages for `--stall-timeout-minutes`
- Action: Abort crawl with non-zero exit code

### Error Thresholds

- `--error-threshold-timeout N`: Abort if N timeout errors
- `--error-threshold-http N`: Abort if N HTTP errors
- Prevents runaway crawls that repeatedly fail

### Adaptive Workers

When `--enable-adaptive-workers` is set:
- Reduces worker count on sustained errors
- Min workers: `--min-workers` (default: 1)
- Max reductions: `--max-worker-reductions` (default: 2)
- Strategy: Reduce by 1 each time threshold exceeded

### VPN Rotation

When `--enable-vpn-rotation` is set:
- Rotates VPN connection on stalls or errors
- Command: `--vpn-connect-command "vpn connect server"`
- Frequency: Every `--vpn-rotation-frequency-minutes`
- Max rotations: `--max-vpn-rotations`

**Use case**: Avoid IP bans during large crawls

---

## Development

### Running Locally

```bash
# Direct execution
cd src/archive_tool
python -m archive_tool.cli \
  --name test \
  --output-dir /tmp/test-crawl \
  https://example.com

# Via installed command
archive-tool --name test --output-dir /tmp/test https://example.com
```

### Testing

```bash
# Run archive_tool tests
pytest tests/test_archive_tool*.py

# Test state management
pytest tests/test_archive_state.py

# Test WARC discovery
pytest tests/test_warc_discovery.py
```

### Adding New Features

1. **Modify CLI** (`archive_tool/cli.py`):
   - Add new argument to `argparse`
   - Update `run_with_parsed_args()`

2. **Update contract** (`ha_backend/archive_contract.py`):
   - Add field to `ArchiveToolOptions` TypedDict
   - Update `validate_tool_options()`

3. **Update backend** (`ha_backend/jobs.py`):
   - Add CLI flag construction in `run_persistent_job()`

4. **Update job registry** (`ha_backend/job_registry.py`):
   - Add to `default_tool_options` if needed

5. **Add tests**:
   - `tests/test_archive_contract.py` - Config validation
   - `tests/test_jobs_persistent.py` - CLI construction
   - `tests/test_archive_tool_*.py` - archive_tool behavior

**See**: `src/archive_tool/docs/documentation.md` (Development) for details

---

## Troubleshooting

### Docker Issues

**Problem**: "Cannot connect to Docker daemon"

**Solution**:
```bash
sudo systemctl start docker
docker ps  # Verify
```

**Problem**: Permission denied accessing Docker socket

**Solution**:
```bash
sudo usermod -aG docker $USER
# Log out and back in
```

### State Issues

**Problem**: Crawl won't resume

**Solution**:
```bash
# Check state file
cat output_dir/.archive_state.json

# Force fresh start
archive-tool --overwrite ...
```

**Problem**: WARCs not found

**Solution**:
```bash
# Manually check
find output_dir -name "*.warc.gz"

# Verify state points to correct dirs
cat output_dir/.archive_state.json | jq '.temp_dirs'
```

### Monitoring Issues

**Problem**: Adaptive workers not triggering

**Check**:
1. `--enable-monitoring` is set
2. `--enable-adaptive-workers` is set
3. Errors exceed threshold
4. Not already at `--min-workers`

---

## Performance Tuning

### Worker Count

- **Default**: 1 worker (conservative)
- **Small sites**: 1-2 workers
- **Medium sites**: 2-4 workers
- **Large sites**: 4-8 workers (watch resource usage)

**Factors**:
- Server CPU/memory
- Network bandwidth
- Site's rate limiting
- Politeness requirements

### Memory Usage

Docker container memory (per worker):
- ~500MB base
- +200-500MB per worker
- +500MB-1GB for large sites

**Example**: 4 workers ≈ 2-4GB RAM

### Disk I/O

WARCs write continuously:
- 10-50MB/min for typical sites
- 100-500MB/min for large sites

**Ensure**:
- Fast disk (SSD recommended)
- Sufficient space (check `df -h` before starting)
- No I/O bottlenecks (`iostat -x 1`)

---

## Related Documentation

- **Full archive_tool docs**: `src/archive_tool/docs/documentation.md` (**Start here for details**)
- **Backend integration**: [../architecture.md#5-archive_tool-integration](../architecture.md#5-archive_tool-integration--job-runner-ha_backendjobspy)
- **Job execution**: [../architecture.md#52-run_persistent_job](../architecture.md#52-run_persistent_job--db-backed-jobs)
- **CLI commands**: [cli-commands.md](cli-commands.md)
- **Debugging crawls**: [../tutorials/debug-crawl.md](../tutorials/debug-crawl.md)
