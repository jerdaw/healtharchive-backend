# 2026-01-29: WARC Manifest Verification Command

**Status**: Implemented 2026-01-29
**Scope**: Crawling & indexing reliability (Phase 1 of WARC discovery consistency)

## Overview

Added a `verify-warc-manifest` CLI command to allow operators to verify that the WARC consolidation manifest accurately reflects files on disk. This addresses the identified gap where operators had no way to verify manifest integrity post-consolidation.

## Implementation Details

### 1. verify_warc_manifest() Function

**Location**: `src/ha_backend/archive_storage.py`

**Purpose**: Verify the WARC consolidation manifest against actual files on disk.

**Checks performed**:
1. Manifest exists and is valid JSON
2. All entries in manifest have corresponding files on disk
3. Size matches (if check_size=True)
4. SHA256 matches (if check_hash=True)
5. Detects orphaned WARCs in `warcs/` not in manifest (warning only)

**New dataclass**:
```python
@dataclass
class ManifestVerificationResult:
    valid: bool
    manifest_path: Path
    entries_total: int
    entries_verified: int
    missing: list[str]
    size_mismatches: list[tuple[str, int, int]]  # (name, expected, actual)
    hash_mismatches: list[tuple[str, str, str]]  # (name, expected, actual)
    orphaned: list[str]
    errors: list[str]
```

### 2. verify-warc-manifest CLI Command

**Location**: `src/ha_backend/cli.py`

**Usage**:
```bash
# Verify all WARCs match manifest (size check)
ha-backend verify-warc-manifest --id 42

# Check file presence only (fast)
ha-backend verify-warc-manifest --id 42 --level presence

# Full SHA256 verification (slow)
ha-backend verify-warc-manifest --id 42 --level hash

# JSON output format
ha-backend verify-warc-manifest --id 42 --json
```

**Output example**:
```
Job 42: Verifying /path/to/job/warcs/manifest.json
Level: size
Manifest entries: 15
Files verified: 15/15
Orphaned files: 0
Status: OK
```

**Exit codes**:
- 0: All checks passed
- 1: One or more checks failed (missing files, mismatches, errors)

### 3. Tests

**New test files**:
- `tests/test_cli_verify_manifest.py` (14 tests) - CLI command tests
- `tests/test_warc_discovery.py` (13 tests) - WARC discovery edge case tests

**Test coverage**:
- Valid manifest verification
- Missing manifest handling
- Missing WARC file detection
- Size mismatch detection
- Hash mismatch detection
- Orphaned WARC detection
- JSON output format
- All three verification levels (presence, size, hash)
- Edge cases: empty files, symlinks, subdirectories

## Files Changed

**Modified**:
- `src/ha_backend/archive_storage.py` - Added ManifestVerificationResult dataclass and verify_warc_manifest()
- `src/ha_backend/cli.py` - Added cmd_verify_warc_manifest() and argparse configuration

**Added**:
- `tests/test_cli_verify_manifest.py` - 14 tests for manifest verification CLI
- `tests/test_warc_discovery.py` - 13 tests for WARC discovery edge cases

## Verification

**CI Status**: ✅ All checks passing
- Format check: ✅
- Lint: ✅
- Type check: ✅
- Tests: 197 passed (added 27 new tests)

## Remaining Work (Deferred)

The following items from the original WARC discovery consistency plan remain in the backlog:

1. **Enhanced WarcDiscoveryResult dataclass** - Add structured result with source tracking
2. **Improve show-job WARC reporting** - Show discovery source (stable/temp)
3. **Update vps-crawl-status.sh** - Use CLI instead of inline Python
4. **Unified discovery method** - Reduce fragmentation between discovery entry points

These items are tracked in the parent plan:
- `docs/planning/implemented/2026-01-29-warc-discovery-consistency.md`

## Related Documentation

- Existing WARC verification: `docs/operations/playbooks/storage/warc-integrity-verification.md`
- Archive storage: `src/ha_backend/archive_storage.py`
- Agent/CLI reference: `AGENTS.md`
