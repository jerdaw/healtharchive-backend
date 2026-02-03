# ADR: Search ranking v3 with is_archived column and enhanced signals

**Date**: 2026-01-18

**Status**: Accepted

##  Context

Search quality is central to HealthArchive's value proposition - researchers need to discover relevant captures efficiently. The v2 ranking system (introduced in 2025) provided query-mode sensitive blending but had two key limitations:

1. **Archived content detection was heuristic-only** - We detected archived pages via title/snippet text patterns, causing false positives/negatives and making the signal unstable across content updates.

2. **Missing modern ranking signals** - No recency preference for broad queries, no exact title matching boost, and fixed BM25 weights limited relevance tuning.

The roadmap `docs/planning/implemented/2026-01-03-search-ranking-and-snippets-v3.md` defined a phased implementation plan to address these gaps while maintaining single-VPS constraints.

## Decision

We implemented search ranking **v3** with three major enhancements:

### 1. Stable Archived Detection via Database Column

**Change**: Added `Snapshot.is_archived` (nullable boolean) populated at index-time.

**Rationale**:
- Moves archived detection from query-time heuristics to index-time computation
- Database column is stable across snippet updates and query variations
- Nullable design allows graceful handling during migration (fallback to heuristics for NULL values)

**Implementation**:
- Alembic migration: `alembic/versions/0013_snapshot_is_archived.py`
- Detection logic: `src/ha_backend/indexing/text_extraction.py::detect_is_archived()`
- Ranking integration: `src/ha_backend/api/routes_public.py::build_archived_penalty()`

### 2. Enhanced Text Extraction for Better FTS and Snippets

**Changes**:
- ARIA role pruning (`role=navigation`, `role=banner`, etc.)
- Content root scoring (prefer `<main>`, `<article>` over generic `<div>`)
- Boilerplate phrase filtering ("Skip to content", cookies banners)
- Extended FTS content to ~4KB (up from ~280 char snippets)

**Rationale**:
- Removes navigation boilerplate that pollutes snippets
- Improves FTS match quality by indexing actual page content
- 4KB limit balances index size vs. content coverage

**Implementation**:
- Text extraction: `src/ha_backend/indexing/text_extraction.py`
- FTS integration: `src/ha_backend/search.py::build_search_vector()`
- Pipeline: `src/ha_backend/indexing/pipeline.py`

### 3. Additional Ranking Signals

**New signals in v3**:

1. **Recency boost** (broad/mixed queries only):
   - Formula: `coef * ln(1 + 365 / days_ago)`
   - Broad: 0.15, Mixed: 0.08, Specific: 0.0
   - Rationale: Recent content is more valuable for broad exploratory queries

2. **Title exact-match boost**:
   - Bonus when query appears as substring in title
   - Broad: +0.35, Mixed: +0.30, Specific: +0.25
   - Rationale: Stronger signal than token matching; indicates highly relevant pages

3. **BM25 weight tuning** (ts_rank weights):
   - Increased title weight (A): 1.0 → 1.2 for broad queries
   - Reduced URL weight (D): 0.1 → 0.05
   - Rationale: Titles are more reliable than URL tokens for relevance

**Implementation**:
- Config: `src/ha_backend/search_ranking.py`
- API wiring: `src/ha_backend/api/routes_public.py`

## Consequences

**Positive**:
- ✅ Stable archived detection reduces query-time variability
- ✅ Cleaner snippets improve user experience
- ✅ Recency boost helps with broad queries ("covid" prefers recent guidance)
- ✅ Title exact-match strongly signals relevance
- ✅ Database-backed `is_archived` enables analytics and filters

**Neutral**:
- ⚠️ Nullable `is_archived` requires migration planning (run Alembic + backfill on production)
- ⚠️ Evaluation required before making v3 default (`HA_SEARCH_RANKING_VERSION=v3`)

**Negative**:
- ❌ Slight index storage increase due to 4KB FTS content (acceptable given single-VPS storage capacity)

## Implementation Notes

- Opt-in via `ranking=v3` query parameter or `HA_SEARCH_RANKING_VERSION=v3` environment variable
- V2 remains default until evaluation completes
- All 234 tests pass (28 new tests for v3 functionality)
- Evaluation tooling updated: `scripts/search-eval-capture.sh` supports `--ranking v3`

## References

- Roadmap: `docs/planning/implemented/2026-01-03-search-ranking-and-snippets-v3.md`
- Migration: `alembic/versions/0013_snapshot_is_archived.py`
- Tests: `tests/test_text_extraction_v3.py`, `tests/test_ranking_v3_signals.py`
