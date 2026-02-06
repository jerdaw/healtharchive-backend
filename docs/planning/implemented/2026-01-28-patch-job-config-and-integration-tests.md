# 2026-01-28: patch-job-config Command and Integration Tests

**Status**: Implemented 2026-01-28
**Scope**: Phase 2 implementation from hardening backlog

## Overview

Implemented two improvements from the post-hardening backlog:
1. `patch-job-config` CLI command for modifying job tool_options without recreating jobs
2. Integration tests for `archive_tool/main.py` stage loop orchestration

## Implementation Details

### 1. patch-job-config Command

**Location**: `src/ha_backend/cli.py`

**Purpose**: Enable patching existing annual jobs with new config options (e.g., `skip_final_build`, `docker_shm_size`, `stall_timeout_minutes`) without recreating them.

**Features**:
- Type coercion: `true`/`false` → bool, numeric strings → int, others → str
- Dry-run by default (shows diff), `--apply` to save changes
- Status restrictions: Only `queued`, `retryable`, or `failed` jobs can be patched
- Validates against `ArchiveToolOptions` dataclass fields
- Validates tool_options dependencies (e.g., adaptive_workers requires monitoring)

**Usage**:
```bash
# Dry-run (shows changes without applying)
ha-backend patch-job-config --id 42 \
  --set-tool-option skip_final_build=true \
  --set-tool-option docker_shm_size=2g

# Apply changes
ha-backend patch-job-config --id 42 \
  --set-tool-option skip_final_build=true \
  --set-tool-option stall_timeout_minutes=60 \
  --apply
```

**Tests**: `tests/test_cli_patch_job.py` (15 tests)
- Dry-run and apply modes
- Type coercion (bool, int, str)
- Status validation
- Tool options validation
- Error handling

### 2. Integration Tests for archive_tool main.py

**Location**: `tests/test_archive_tool_main_integration.py`

**Purpose**: Test main.py orchestration logic without requiring real Docker containers.

**Coverage** (13 tests):
- **Existing ZIM handling**: Exit behavior with/without `--overwrite`
- **Docker start failures**: Exception handling, None returns
- **Docker availability**: Failure when Docker is unavailable
- **Dry-run mode**: No container starts in dry-run
- **Output directory**: Auto-creation of missing dirs
- **CrawlState**: State file creation, adaptation count persistence, temp dir tracking
- **Temp directory discovery**: `discover_temp_dirs()` utility
- **Worker count parsing**: Passthrough `--workers` arg parsing

**Testing approach**:
- Mocked Docker operations (no real containers needed)
- Tests focus on early exit conditions and state management
- Full stage loop tests with threading are complex (log drain thread) - deferred

## Files Changed

**Modified**:
- `src/ha_backend/cli.py` - Added `cmd_patch_job_config` and helpers
- `AGENTS.md` - Added patch-job-config to examples
- `docs/planning/roadmap.md` - Removed completed integration tests item

**Added**:
- `tests/test_cli_patch_job.py` - 15 tests for patch-job-config
- `tests/test_archive_tool_main_integration.py` - 13 integration tests

## Verification

**CI Status**: ✅ All checks passing
- Format check: ✅
- Lint: ✅
- Type check: ✅
- Tests: 183 passed (added 28 new tests)

**Manual verification**:
```bash
# Test patch-job-config help
ha-backend patch-job-config --help

# Test on a dev job (dry-run)
ha-backend patch-job-config --id 1 --set-tool-option skip_final_build=true
```

## Deferred Items (Phase 3)

These items remain in the backlog per the original plan:
- Same-day dedupe with provenance preservation
- WARC discovery consistency improvements (manifest verification)
- Canary replay job (local-only)
- Search authority signals tuning (requires measurement first)

## Related Documentation

- Implementation plan: This file
- Roadmap: `docs/planning/roadmap.md` (updated)
- Agent/CLI reference: `AGENTS.md` (updated)
- Archive tool internals: `src/archive_tool/docs/documentation.md`
