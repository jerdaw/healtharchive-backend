# 2026-01-29: WARC Discovery Consistency Improvements

**Status**: Partially Implemented (Phase 1 done, Phases 2-4 deferred)
**Scope**: Crawling & indexing reliability (from backlog)

## Implementation Progress

- **Phase 1**: ✅ Implemented - `verify-warc-manifest` CLI command
  - See: `docs/planning/implemented/2026-01-29-warc-manifest-verification.md`
- **Phase 2**: Deferred - WarcDiscoveryResult dataclass
- **Phase 3**: Deferred - Improved manifest error handling
- **Phase 4**: Deferred - vps-crawl-status.sh sync
- **Phase 5**: ✅ Implemented - Tests for edge cases

## Overview

Ensure WARC discovery and "WARC files" reporting are consistent across:
- Operator status output (`ha-backend show-job`, `scripts/vps-crawl-status.sh`)
- Indexing pipeline discovery (`ha_backend/indexing/warc_discovery.py`)
- Cleanup semantics (`ha-backend cleanup-job`)

## Current State Analysis

### WARC Discovery Entry Points

| Location | Method | Purpose |
|----------|--------|---------|
| `warc_discovery.discover_warcs_for_job()` | Stable `warcs/` first, then temp fallback | Indexing, show-job |
| `warc_discovery.discover_temp_warcs_for_job()` | CrawlState temp dirs only | Legacy discovery |
| `archive_tool.utils.find_all_warc_files()` | Glob in temp dirs + collections/ | Low-level discovery |
| `archive_storage._iter_stable_warc_paths()` | Glob in `warcs/` dir | Stable WARC enumeration |
| `cli.cmd_cleanup_job()` | CrawlState + glob merge | Cleanup temp identification |
| `vps-crawl-status.sh` | Inline Python rglob | Operator status (recent WARCs) |

### Existing Manifest System

The manifest at `<output_dir>/warcs/manifest.json` already includes:
- `source_path`: Original temp WARC location
- `stable_name`: Normalized name (`warc-NNNNNN.warc.gz`)
- `sha256`: Hash of file contents
- `size_bytes`: File size
- `link_type`: "hardlink" or "copy"

### Identified Inconsistencies

1. **No manifest verification command**: Operators cannot verify WARC integrity post-consolidation
2. **Count discrepancies**: `job.warc_file_count` (DB) vs. live discovery can differ during long crawls
3. **Discovery method fragmentation**: Three different ways to find WARCs depending on context
4. **vps-crawl-status.sh bypass**: Script uses inline Python, bypassing all Python discovery logic
5. **Missing edge case handling**: What happens if manifest exists but WARCs are missing?

## Implementation Plan

### Phase 1: Add `verify-warc-manifest` CLI Command

**Purpose**: Allow operators to verify WARC integrity against the manifest.

**Location**: `src/ha_backend/cli.py`

**Command design**:
```bash
# Verify all WARCs match manifest
ha-backend verify-warc-manifest --id 42

# Output formats
ha-backend verify-warc-manifest --id 42 --json
ha-backend verify-warc-manifest --id 42 --quiet  # Exit code only

# Verification levels
ha-backend verify-warc-manifest --id 42 --level presence  # Files exist (fast)
ha-backend verify-warc-manifest --id 42 --level size      # Size matches (default)
ha-backend verify-warc-manifest --id 42 --level hash      # SHA256 matches (slow)
```

**Checks performed**:
1. Manifest exists and is valid JSON
2. All entries in manifest have corresponding files on disk
3. Size matches (if level >= size)
4. SHA256 matches (if level == hash)
5. No orphaned WARCs in `warcs/` not in manifest (warning only)

**Output**:
```
Job 42: Verifying warcs/manifest.json
Level: size
Manifest entries: 15
Files verified: 15/15
Orphaned files: 0
Status: OK

# On failure:
Job 42: Verifying warcs/manifest.json
Level: size
Manifest entries: 15
Files verified: 13/15
MISSING: warcs/warc-000007.warc.gz (expected 12345678 bytes)
SIZE_MISMATCH: warcs/warc-000012.warc.gz (expected 9876543, found 9876000)
Orphaned files: 1 (warcs/warc-000016.warc.gz not in manifest)
Status: FAILED
```

**Exit codes**:
- 0: All checks passed
- 1: One or more checks failed
- 2: Manifest missing or invalid

### Phase 2: Unify WARC Discovery Logic

**Goal**: Single source of truth for "how many WARCs does this job have?"

**Changes to `warc_discovery.py`**:

1. Add `WarcDiscoveryResult` dataclass:
```python
@dataclass
class WarcDiscoveryResult:
    source: str  # "stable", "temp", "fallback"
    warc_paths: list[Path]
    manifest_valid: bool | None  # None if no manifest
    warnings: list[str]
```

2. Add `discover_warcs_for_job_extended()` function that returns the full result with diagnostics.

3. Keep `discover_warcs_for_job()` as simple wrapper returning just `list[Path]`.

**Changes to `cli.py` (show-job)**:

Update to use extended discovery and show source:
```
WARC files:     15 discovered (source: stable, manifest: valid)
WARC files:     8 discovered (source: temp, consolidation pending)
```

### Phase 3: Improve Manifest Error Handling

**Changes to `archive_storage.py`**:

1. Add `verify_manifest()` function:
```python
@dataclass
class ManifestVerificationResult:
    valid: bool
    entries_checked: int
    missing: list[str]
    size_mismatches: list[tuple[str, int, int]]  # (name, expected, actual)
    hash_mismatches: list[tuple[str, str, str]]  # (name, expected, actual)
    orphaned: list[str]
    errors: list[str]
```

2. Add `ManifestError` exception class for structured error handling.

3. Update `consolidate_warcs()` to log warnings for existing orphaned files.

### Phase 4: Sync VPS Script with Python Discovery

**Changes to `scripts/vps-crawl-status.sh`**:

Replace inline Python block with call to new CLI command:
```bash
# Instead of inline Python rglob
"${HA_BIN}" show-job --id "${JOB_ID}" --warc-details
```

**Add `--warc-details` flag to show-job**:
- Lists 5 most recent WARCs by mtime
- Shows total size
- Shows discovery source

### Phase 5: Add Tests for Edge Cases

**New tests in `tests/test_warc_discovery.py`**:

1. Stable dir exists but is empty
2. Manifest exists but WARCs are missing
3. Manifest is malformed JSON
4. Orphaned WARCs not in manifest
5. Size mismatch detection
6. Hash mismatch detection (with --level hash)
7. Mixed temp + stable discovery
8. Race condition: WARC deleted during discovery

**New tests in `tests/test_cli_verify_manifest.py`**:

1. verify-warc-manifest with valid manifest
2. verify-warc-manifest with missing files
3. verify-warc-manifest with size mismatch
4. verify-warc-manifest --level hash
5. verify-warc-manifest --json output format
6. verify-warc-manifest on job with no manifest (pre-consolidation)

## Files to Modify

**Modified**:
- `src/ha_backend/cli.py` - Add verify-warc-manifest command, enhance show-job
- `src/ha_backend/archive_storage.py` - Add verification functions
- `src/ha_backend/indexing/warc_discovery.py` - Add extended discovery result
- `scripts/vps-crawl-status.sh` - Use CLI instead of inline Python
- `CLAUDE.md` - Add new command to CLI examples

**Added**:
- `tests/test_warc_discovery.py` - WARC discovery edge case tests
- `tests/test_cli_verify_manifest.py` - Manifest verification CLI tests

## Verification Plan

1. `make ci` passes
2. `ha-backend verify-warc-manifest --help` shows usage
3. Test on job with consolidated WARCs (valid manifest)
4. Test on job without consolidation (no manifest)
5. Test with artificially corrupted manifest/WARCs

## Implementation Order

| Step | Description | Effort |
|------|-------------|--------|
| 1 | Add `verify_manifest()` in archive_storage.py | 30 min |
| 2 | Add `verify-warc-manifest` CLI command | 45 min |
| 3 | Add tests for CLI command | 30 min |
| 4 | Add `WarcDiscoveryResult` dataclass | 15 min |
| 5 | Enhance show-job with discovery source | 20 min |
| 6 | Add edge case tests for warc_discovery | 30 min |
| 7 | Update vps-crawl-status.sh | 15 min |
| 8 | Update docs (CLAUDE.md) | 10 min |

Total estimated: ~3.5 hours

## Deferred (Not in Scope)

- Automatic manifest repair (would need policy decisions)
- WARC content validation beyond gzip integrity (covered by verify-warcs command)
- Same-day dedupe (separate backlog item)

## Related Documentation

- Existing WARC verification: `docs/operations/playbooks/storage/warc-integrity-verification.md`
- Cleanup semantics: `src/ha_backend/cli.py:cmd_cleanup_job`
- Archive storage: `src/ha_backend/archive_storage.py`
