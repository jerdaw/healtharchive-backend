# Test coverage: critical business logic (v1) — implementation plan

Status: **planned** (created 2026-01-17)

## Goal

Add comprehensive unit test coverage to the critical business logic modules that
currently lack direct testing:

- **`diffing.py`** — HTML diff algorithm, normalization, noise filtering
- **`changes.py`** — Snapshot change computation (backfill and incremental)
- **`archive_storage.py`** — WARC consolidation, manifests, storage statistics
- **`pages.py`** — Page grouping SQL generation (PostgreSQL/SQLite dialects)
- **`archive_tool/strategies.py`** — Adaptive crawl strategies and restart logic

These modules contain complex algorithms and conditional logic that should be
regression-tested to prevent subtle bugs during refactoring or feature work.

This plan is **sequential**: complete each module's tests before moving to the next.

## Why this is "next" (roadmap selection)

Test coverage improvements are high-leverage because:

- **Prevent regressions** — these modules are on critical paths (change detection,
  search, storage) where bugs have high user impact.
- **Enable confident refactoring** — Search v3 work may touch diffing and text extraction;
  tests provide a safety net.
- **Low risk** — adding tests doesn't change production behavior.
- **Fills documented gaps** — project analysis identified these specific modules.

## Docs setup (do first)

1) **Create this plan doc**
   - File: `docs/roadmaps/2026-01-17-test-coverage-critical-business-logic.md` (this document)

2) **Roadmaps index**
   - Update `docs/roadmaps/README.md` to list this plan under "Implementation plans (active)".

3) **No canonical docs changes required** — this is internal test coverage work.

---

## Scope, goals, constraints

### In-scope outcomes (what we will deliver)

- **Unit tests for `diffing.py`**:
  - HTML normalization (whitespace, case, attribute ordering)
  - Heading extraction
  - Banner/noise detection and filtering
  - Diff algorithm edge cases (empty, identical, completely different)
  - Bilingual content handling (en/fr)

- **Unit tests for `changes.py`**:
  - Backfill computation logic
  - Incremental change detection
  - Edge cases (first snapshot, gaps in timeline, duplicate timestamps)

- **Unit tests for `archive_storage.py`**:
  - WARC consolidation logic
  - Manifest generation and parsing
  - Storage statistics calculation
  - File deduplication detection

- **Unit tests for `pages.py`**:
  - SQL generation for PostgreSQL dialect
  - SQL generation for SQLite dialect
  - URL normalization in SQL context
  - Page grouping logic correctness

- **Unit tests for `archive_tool/strategies.py`**:
  - Restart strategy selection
  - Fallback mechanism triggers
  - Stall detection heuristics

### Non-goals (explicitly out of scope)

- Integration tests (existing API tests provide integration coverage).
- Performance benchmarks (defer to later if needed).
- Changes to production code (tests only, unless bugs are discovered).
- Full mutation testing (good practice but out of scope for this plan).

### Constraints to respect

- **Test isolation** — tests must not require external services (use mocks/fixtures).
- **SQLite compatibility** — page grouping tests must work with SQLite in CI.
- **Fast execution** — individual test files should complete in under 30 seconds.
- **Deterministic** — no flaky tests; use fixed seeds for any randomness.

---

## Current-state map (what exists today)

### Existing test coverage (related)

- `tests/test_api_*.py` — API integration tests (91 tests, good coverage)
- `tests/test_search_fuzzy.py` — Fuzzy search unit tests
- `tests/test_search_ranking.py` — Ranking algorithm tests
- `tests/test_indexing_warc_verify.py` — WARC verification tests

### Modules lacking direct tests

| Module | Lines | Complexity | Current Tests |
|--------|-------|------------|---------------|
| `diffing.py` | ~400 | High (algorithms) | None |
| `changes.py` | ~200 | Medium | None |
| `archive_storage.py` | ~350 | Medium | None |
| `pages.py` | ~150 | Medium (SQL) | None |
| `archive_tool/strategies.py` | ~250 | High | None |

### Test infrastructure

- Framework: pytest
- Database: SQLite in-memory for tests (`HEALTHARCHIVE_DATABASE_URL`)
- Fixtures: `conftest.py` provides DB session, test client, etc.
- Mocking: `unittest.mock` or `pytest-mock`

---

## Definition of Done (DoD) + acceptance criteria

### Test coverage targets

For each module:
- Minimum 80% line coverage for the module
- All public functions have at least one test
- Edge cases documented in test names (e.g., `test_diff_empty_inputs`)
- No skipped or xfail tests without documented rationale

### Quality requirements

- All tests pass in CI (`make ci`)
- Tests are deterministic (no flaky failures)
- Test execution time < 30 seconds per file
- Tests are readable and serve as documentation

---

## Phase 1 — Test infrastructure setup

**Objective:** Ensure test fixtures and utilities are ready for the new test files.

### 1.1 Review existing test infrastructure

Examine `tests/conftest.py` for reusable fixtures:
- Database session management
- Test data factories
- Cleanup patterns

### 1.2 Create shared fixtures for new tests (if needed)

Consider creating in `tests/conftest.py` or a new `tests/fixtures/` directory:
- HTML fixture factory (for diffing tests)
- Snapshot factory with configurable fields
- Mock WARC file generator

### 1.3 Establish test file naming convention

Follow existing pattern:
- `tests/test_diffing.py`
- `tests/test_changes.py`
- `tests/test_archive_storage.py`
- `tests/test_pages.py`
- `tests/test_archive_tool_strategies.py`

**Deliverables:**
- Any new shared fixtures created
- Test file stubs created with proper imports

**Exit criteria:** Test infrastructure ready; `make test` still passes.

---

## Phase 2 — Tests for `diffing.py`

**Objective:** Comprehensive unit tests for the HTML diff and normalization module.

### 2.1 Understand the module

Read `src/ha_backend/diffing.py` and document:
- Public functions and their contracts
- Normalization steps applied
- Noise filtering heuristics
- Diff output format

### 2.2 Test categories

**Normalization tests:**
```python
def test_normalize_whitespace_collapses_multiple_spaces():
    """Multiple whitespace characters should collapse to single space."""

def test_normalize_whitespace_preserves_newlines_in_pre():
    """Whitespace in <pre> blocks should be preserved."""

def test_normalize_case_lowercases_tags():
    """HTML tags should be case-normalized."""

def test_normalize_attributes_sorted():
    """HTML attributes should be sorted for stable comparison."""
```

**Heading extraction tests:**
```python
def test_extract_headings_h1_through_h6():
    """All heading levels should be extracted."""

def test_extract_headings_preserves_hierarchy():
    """Heading hierarchy should be maintained."""

def test_extract_headings_empty_document():
    """Empty document should return empty heading list."""
```

**Banner/noise detection tests:**
```python
def test_detect_archived_banner_english():
    """English archived page banner should be detected."""

def test_detect_archived_banner_french():
    """French archived page banner should be detected."""

def test_detect_cookie_consent_banner():
    """Cookie consent text should be detected as noise."""

def test_noise_filter_removes_navigation():
    """Navigation boilerplate should be filtered."""
```

**Diff algorithm tests:**
```python
def test_diff_identical_documents():
    """Identical documents should produce empty diff."""

def test_diff_empty_inputs():
    """Empty inputs should be handled gracefully."""

def test_diff_completely_different():
    """Completely different documents should show full diff."""

def test_diff_preserves_context():
    """Diff output should include context lines."""

def test_diff_bilingual_content():
    """Mixed English/French content should diff correctly."""
```

### 2.3 Create HTML fixtures

Create representative HTML fixtures in `tests/fixtures/html/`:
- `simple_page.html` — minimal valid page
- `canada_ca_page.html` — typical Canada.ca structure
- `archived_page_en.html` — page with English archived banner
- `archived_page_fr.html` — page with French archived banner
- `navigation_heavy.html` — page with lots of nav boilerplate

### 2.4 Implement tests

Create `tests/test_diffing.py` with all test categories.

**Deliverables:**
- `tests/test_diffing.py` with 15-25 tests
- HTML fixtures for test cases
- All tests passing

**Exit criteria:** `diffing.py` has >80% line coverage; `make ci` passes.

---

## Phase 3 — Tests for `changes.py`

**Objective:** Unit tests for snapshot change computation logic.

### 3.1 Understand the module

Read `src/ha_backend/changes.py` and document:
- Backfill vs incremental computation
- How changes are detected between snapshots
- Database queries used
- Edge case handling

### 3.2 Test categories

**Backfill tests:**
```python
def test_backfill_computes_changes_for_all_pairs():
    """Backfill should create change records for all adjacent snapshots."""

def test_backfill_respects_max_events_limit():
    """Backfill should stop after max_events."""

def test_backfill_skips_already_computed():
    """Backfill should not recompute existing changes."""
```

**Incremental tests:**
```python
def test_incremental_processes_recent_snapshots():
    """Incremental should only process snapshots within time window."""

def test_incremental_uses_correct_time_window():
    """Default 30-day window should be applied."""
```

**Edge case tests:**
```python
def test_change_first_snapshot_no_previous():
    """First snapshot for a URL should not create a change record."""

def test_change_gaps_in_timeline():
    """Gaps in snapshot timeline should be handled correctly."""

def test_change_duplicate_timestamps():
    """Duplicate timestamps should not cause errors."""

def test_change_same_content_no_change():
    """Identical content should produce 'no change' result."""
```

### 3.3 Create test fixtures

Use database fixtures to create snapshot pairs with known differences:
- Pair with text changes
- Pair with structural changes
- Pair with no changes
- Pair with language change

### 3.4 Implement tests

Create `tests/test_changes.py` with all test categories.

**Deliverables:**
- `tests/test_changes.py` with 12-18 tests
- Database fixtures for snapshot pairs
- All tests passing

**Exit criteria:** `changes.py` has >80% line coverage; `make ci` passes.

---

## Phase 4 — Tests for `archive_storage.py`

**Objective:** Unit tests for WARC consolidation and storage management.

### 4.1 Understand the module

Read `src/ha_backend/archive_storage.py` and document:
- WARC consolidation logic
- Manifest file format and parsing
- Storage statistics calculation
- Deduplication detection

### 4.2 Test categories

**WARC consolidation tests:**
```python
def test_consolidate_merges_warc_files():
    """Multiple small WARCs should consolidate into larger files."""

def test_consolidate_preserves_record_integrity():
    """Consolidation should not corrupt WARC records."""

def test_consolidate_updates_manifest():
    """Manifest should be updated after consolidation."""
```

**Manifest tests:**
```python
def test_manifest_generation():
    """Manifest should list all WARCs with correct metadata."""

def test_manifest_parsing():
    """Manifest file should parse correctly."""

def test_manifest_checksum_validation():
    """Manifest checksums should validate correctly."""
```

**Storage statistics tests:**
```python
def test_storage_stats_calculates_total_size():
    """Total storage size should be calculated correctly."""

def test_storage_stats_counts_files():
    """File counts should be accurate."""

def test_storage_stats_handles_missing_directory():
    """Missing directory should be handled gracefully."""
```

**Deduplication tests:**
```python
def test_detect_duplicate_warcs():
    """Duplicate WARCs should be detected by content hash."""

def test_dedup_preserves_original():
    """Deduplication should preserve the original file."""
```

### 4.3 Create test fixtures

Create temporary directory structures with mock WARC files:
- Use `tmp_path` pytest fixture
- Create minimal valid WARC files for testing
- Create manifest files for parsing tests

### 4.4 Implement tests

Create `tests/test_archive_storage.py` with all test categories.

**Deliverables:**
- `tests/test_archive_storage.py` with 12-16 tests
- Temporary file fixtures
- All tests passing

**Exit criteria:** `archive_storage.py` has >80% line coverage; `make ci` passes.

---

## Phase 5 — Tests for `pages.py`

**Objective:** Unit tests for SQL generation and page grouping logic.

### 5.1 Understand the module

Read `src/ha_backend/pages.py` and document:
- PostgreSQL-specific SQL constructs
- SQLite-specific SQL constructs
- URL normalization in SQL
- Page grouping criteria

### 5.2 Test categories

**SQL dialect tests:**
```python
def test_sql_generation_postgresql():
    """PostgreSQL dialect should generate valid SQL."""

def test_sql_generation_sqlite():
    """SQLite dialect should generate valid SQL."""

def test_sql_dialect_detection():
    """Correct dialect should be detected from connection."""
```

**URL normalization tests:**
```python
def test_url_normalize_removes_trailing_slash():
    """Trailing slashes should be normalized."""

def test_url_normalize_lowercases_host():
    """Hostname should be lowercased."""

def test_url_normalize_removes_fragments():
    """URL fragments should be removed for grouping."""

def test_url_normalize_sorts_query_params():
    """Query parameters should be sorted for stable grouping."""
```

**Page grouping tests:**
```python
def test_group_pages_by_normalized_url():
    """Snapshots with same normalized URL should group together."""

def test_group_pages_respects_source():
    """Grouping should respect source boundaries."""

def test_group_pages_ordering():
    """Groups should be ordered by specified criteria."""
```

### 5.3 Test both dialects

Ensure tests run with both SQLite (CI default) and can verify PostgreSQL logic
through query string inspection (without requiring a real PostgreSQL instance).

### 5.4 Implement tests

Create `tests/test_pages.py` with all test categories.

**Deliverables:**
- `tests/test_pages.py` with 12-15 tests
- Tests for both SQL dialects
- All tests passing

**Exit criteria:** `pages.py` has >80% line coverage; `make ci` passes.

---

## Phase 6 — Tests for `archive_tool/strategies.py`

**Objective:** Unit tests for adaptive crawl strategies and restart logic.

### 6.1 Understand the module

Read `src/archive_tool/strategies.py` and document:
- Strategy selection criteria
- Restart triggers and conditions
- Fallback mechanisms
- Stall detection heuristics

### 6.2 Test categories

**Strategy selection tests:**
```python
def test_strategy_selection_fresh_crawl():
    """Fresh crawl should select appropriate strategy."""

def test_strategy_selection_resume():
    """Resume should select continuation strategy."""

def test_strategy_selection_after_failure():
    """After failure should select recovery strategy."""
```

**Restart logic tests:**
```python
def test_restart_trigger_on_stall():
    """Stall detection should trigger restart."""

def test_restart_respects_max_retries():
    """Restart should not exceed max retries."""

def test_restart_preserves_progress():
    """Restart should preserve crawl progress."""
```

**Fallback tests:**
```python
def test_fallback_reduces_workers():
    """Fallback should reduce worker count."""

def test_fallback_adjusts_timeouts():
    """Fallback should adjust timeout settings."""

def test_fallback_chain_order():
    """Fallbacks should apply in correct order."""
```

**Stall detection tests:**
```python
def test_stall_detection_no_progress():
    """No progress for threshold time should detect stall."""

def test_stall_detection_slow_progress():
    """Slow but progressing crawl should not trigger stall."""

def test_stall_detection_log_parsing():
    """Stall detection should parse crawler logs correctly."""
```

### 6.3 Mock external dependencies

Use mocks for:
- Docker process interaction
- File system state
- Time-based triggers

### 6.4 Implement tests

Create `tests/test_archive_tool_strategies.py` with all test categories.

**Deliverables:**
- `tests/test_archive_tool_strategies.py` with 12-18 tests
- Mocks for external dependencies
- All tests passing

**Exit criteria:** `strategies.py` has >80% line coverage; `make ci` passes.

---

## Phase 7 — Coverage verification and documentation

**Objective:** Verify overall coverage improvement and document the test suite.

### 7.1 Run coverage report

```bash
pytest --cov=src/ha_backend --cov=src/archive_tool --cov-report=html
```

Verify that targeted modules have >80% coverage.

### 7.2 Document test suite

Add comments to each new test file explaining:
- What the module does
- Test categories and their purpose
- How to run tests in isolation

### 7.3 Update AGENTS.md (if applicable)

If test patterns or conventions changed, update `AGENTS.md` testing section.

### 7.4 Archive this plan

Move to `docs/roadmaps/implemented/` when complete.

**Deliverables:**
- Coverage report showing improvement
- Test documentation complete
- Plan archived

**Exit criteria:** All target modules at >80% coverage; `make ci` passes.

---

## Risk register (pre-mortem)

- **Risk:** Tests reveal bugs in production code.
  - **Mitigation:** Fix bugs as separate commits; document in test comments.
- **Risk:** Tests are slow due to complex fixtures.
  - **Mitigation:** Use minimal fixtures; mock expensive operations.
- **Risk:** SQLite/PostgreSQL behavior differences cause test failures.
  - **Mitigation:** Test SQL generation separately from execution where needed.
- **Risk:** Mocking makes tests brittle.
  - **Mitigation:** Mock at boundaries, not internals; prefer real objects when cheap.

---

## Appendix: Test execution commands

```bash
# Run all tests
make test

# Run specific test file
pytest tests/test_diffing.py -v

# Run with coverage
pytest --cov=src/ha_backend/diffing tests/test_diffing.py

# Run tests matching pattern
pytest -k "diff" -v

# Run tests with verbose output
pytest tests/test_changes.py -v --tb=short
```
