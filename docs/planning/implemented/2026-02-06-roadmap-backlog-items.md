# Roadmap Backlog Items Implementation

**Date**: 2026-02-06
**Status**: Completed
**Implementation time**: ~20 hours (estimated)

## Summary

Implemented four deferred roadmap items from `docs/planning/roadmap.md` to improve operational reliability, monitoring, and storage efficiency:

1. **WARC Discovery Consistency (Phases 2-4)** — Unified discovery methods
2. **Annual Crawl Guardrails** — Prevents sev1 disk-full incidents
3. **Canary Replay Job** — Improves monitoring signal quality
4. **Same-day Dedupe** — Storage optimization with full reversibility

## Implementation Details

### 1. WARC Discovery Consistency (Phases 2-4)

**Problem**: Fragmented WARC discovery methods (CLI, scripts, indexing) could diverge.

**Solution**:
- Added `WarcDiscoveryResult` dataclass with metadata (source, manifest_valid, count)
- Enhanced `show-job` with `--warc-details` flag (shows source, size, recent WARCs)
- Added `list-warcs` command for script integration (`--recent N`, `--json`)
- Synced `scripts/vps-crawl-status.sh` to use unified `ha-backend list-warcs`

**Files**:
- `src/ha_backend/indexing/warc_discovery.py` — New dataclass and function
- `src/ha_backend/cli.py` — Enhanced commands
- `scripts/vps-crawl-status.sh` — Replaced inline Python
- `tests/test_warc_discovery.py` — 4 new tests (17 total)

**Verification**: All discovery methods now use same logic; operator scripts can't drift.

---

### 2. Annual Crawl Guardrails

**Problem**: Annual jobs could fill root disk if auto-tiering fails silently ([2026-02-04 incident](../operations/incidents/2026-02-04-annual-crawl-output-dirs-on-root-disk.md)).

**Solution**:
- Added filesystem device detection (`_get_filesystem_device`, `_is_on_root_device`)
- Guardrail in `_tier_annual_job_if_needed()`: refuses to start crawl if output_dir still on `/dev/sda1`
- Improved tiering script error messages (detects Postgres connection failures early)
- Created post-reboot validation runbook

**Files**:
- `src/ha_backend/worker/main.py` — Device detection + guardrail
- `scripts/vps-annual-output-tiering.py` — Better error messages
- `docs/operations/playbooks/validation/post-reboot-tiering-verify.md` — New runbook
- `tests/test_worker_tiering.py` — 13 new tests

**Safety**: Worker crashes with clear error instead of silently filling root disk.

**Verification**: Tests confirm guardrail raises on root device, passes on non-root.

---

### 3. Canary Replay Job

**Problem**: Smoke tests hit production jobs (may be tiered); couldn't distinguish "pywb broken" from "storage tiering broken".

**Solution**:
- Added `hc_canary` job config (2 pages, local-only, never tiered)
- Added `create-canary-job` CLI command (idempotent)
- Updated smoke test to include canary + emit dedicated metric
- Documented metric interpretation in playbook

**Files**:
- `src/ha_backend/job_registry.py` — Canary config
- `src/ha_backend/cli.py` — `create-canary-job` command
- `ops/automation/replay-smoke.toml` — Added `hc_canary`
- `scripts/vps-replay-smoke-textfile.py` — `healtharchive_replay_smoke_canary_ok` metric
- `docs/operations/playbooks/validation/replay-smoke-tests.md` — Updated docs

**Monitoring improvement**: Operators can now distinguish failure modes:
- `canary_ok=1, prod_ok=0` → Storage tiering issue
- `canary_ok=0, prod_ok=0` → pywb service issue

---

### 4. Same-day Dedupe

**Problem**: Same-day duplicate captures (same URL, same content_hash) waste storage.

**Solution**:
- Added `Snapshot.deduplicated` column (indexed, default False)
- Added `SnapshotDeduplication` audit table (snapshot_id, canonical_snapshot_id, deduped_at, reason)
- Created deduplication module with dry-run default
- Added CLI commands: `dedupe-snapshots` (dry-run default), `restore-deduped-snapshots`
- Updated search API to filter deduplicated snapshots (unless `includeDuplicates=true`)
- Added optional auto-dedupe during indexing (`HEALTHARCHIVE_AUTO_DEDUPE=true`)

**Files**:
- `src/ha_backend/models.py` — Schema additions
- `src/ha_backend/indexing/deduplication.py` — Dedup logic
- `src/ha_backend/cli.py` — CLI commands
- `src/ha_backend/indexing/pipeline.py` — Optional auto-dedupe
- `src/ha_backend/api/routes_public.py` — API filtering
- `tests/test_deduplication.py` — 10 new tests

**Safety**: Dry-run default, fully reversible via audit log, excludes from search by default.

**Verification**: Tests cover all scenarios (same day/hash, different days, restore).

---

## Test Coverage

**New tests**: 40 tests across 3 new test files
- WARC discovery: 4 new tests (17 total in file)
- Worker tiering: 13 tests
- Deduplication: 10 tests

**CI status**: All 238 existing + new tests passing

---

## Migration Notes

### Database Migration Required

The `Snapshot.deduplicated` column and `SnapshotDeduplication` table require a migration:

```sql
-- Add deduplicated column (default False, indexed)
ALTER TABLE snapshots
  ADD COLUMN deduplicated BOOLEAN NOT NULL DEFAULT 0;
CREATE INDEX ix_snapshots_deduplicated ON snapshots(deduplicated);

-- Create audit table
CREATE TABLE snapshot_deduplications (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  snapshot_id INTEGER NOT NULL,
  canonical_snapshot_id INTEGER NOT NULL,
  deduped_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  reason VARCHAR(100) NOT NULL DEFAULT 'same_day_same_hash',
  FOREIGN KEY (snapshot_id) REFERENCES snapshots(id),
  FOREIGN KEY (canonical_snapshot_id) REFERENCES snapshots(id)
);
CREATE INDEX ix_snapshot_deduplications_snapshot_id
  ON snapshot_deduplications(snapshot_id);
CREATE INDEX ix_snapshot_deduplications_canonical_snapshot_id
  ON snapshot_deduplications(canonical_snapshot_id);
```

Use Alembic for production migration:
```bash
# Generate migration
alembic revision --autogenerate -m "Add snapshot deduplication"

# Review migration
# Edit alembic/versions/XXXX_add_snapshot_deduplication.py if needed

# Apply migration
alembic upgrade head
```

### Operator Actions

1. **Canary setup** (optional but recommended):
   ```bash
   ha-backend create-canary-job
   ```

2. **Deduplication** (optional):
   ```bash
   # Dry-run to see what would be deduped
   ha-backend dedupe-snapshots --id <job_id>

   # Apply deduplication
   ha-backend dedupe-snapshots --id <job_id> --apply
   ```

3. **Auto-dedupe** (optional):
   Add to `/etc/healtharchive/env.production`:
   ```bash
   HEALTHARCHIVE_AUTO_DEDUPE=true
   ```

---

## Related Documents

- Roadmap: `../roadmap.md` (updated to remove completed items)
- WARC discovery plan: `2026-01-29-warc-discovery-consistency.md`
- Annual disk incident: `../operations/incidents/2026-02-04-annual-crawl-output-dirs-on-root-disk.md`
- Search quality: `../operations/search-quality.md`
- Growth constraints: `../operations/growth-constraints.md`

---

## Lessons Learned

1. **Guardrails are critical**: Silent failures (auto-tiering) can cause sev1 incidents. Explicit guardrails with clear error messages prevent silent disasters.

2. **Monitoring baselines matter**: Canary jobs isolate failure modes (pywb vs storage), reducing MTTR significantly.

3. **Dry-run defaults reduce risk**: Same-day dedupe defaults to dry-run; operators can verify before applying.

4. **Unified discovery prevents drift**: Scripts and CLI using same code path eliminates "works in CLI but not in scripts" bugs.

5. **Test coverage pays dividends**: 40 new tests caught edge cases early (NULL hashes, device detection failures, idempotency).
