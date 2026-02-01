# Test Coverage: Critical Business Logic (Implemented 2026-01-18)

**Status:** Implemented | **Scope:** Comprehensive unit test coverage for 5 critical modules that lacked direct testing.

## Outcomes

- **`diffing.py`** — Tests for HTML normalization, heading extraction, banner/noise detection, diff algorithm edge cases
- **`changes.py`** — Tests for backfill computation, incremental change detection, edge cases (first snapshot, gaps, duplicates)
- **`archive_storage.py`** — Tests for WARC consolidation, manifest generation/parsing, storage statistics, deduplication
- **`pages.py`** — Tests for PostgreSQL/SQLite SQL generation, URL normalization, page grouping logic
- **`archive_tool/strategies.py`** — Tests for adaptive crawl strategy selection, restart logic, fallback mechanisms, stall detection

## Test Files Created

- `tests/test_diffing.py` — 15-25 tests, HTML fixtures
- `tests/test_changes.py` — 12-18 tests, database fixtures
- `tests/test_archive_storage.py` — 12-16 tests, temp file fixtures
- `tests/test_pages.py` — 12-15 tests, both SQL dialects
- `tests/test_archive_tool_strategies.py` — 12-18 tests, mocked dependencies

## Coverage Targets

- All modules >80% line coverage
- All public functions have at least one test
- Tests are deterministic (no flaky failures)
- Test execution time <30 seconds per file

## Test Commands

```bash
# Run all tests
make test

# Run specific test file
pytest tests/test_diffing.py -v

# Run with coverage
pytest --cov=src/ha_backend/diffing tests/test_diffing.py
```

## Historical Context

7-phase sequential implementation (640+ lines) with detailed test categories, fixture design, and coverage verification. Preserved in git history.
