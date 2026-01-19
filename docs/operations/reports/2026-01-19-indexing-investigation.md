# Investigation Report: Indexing Delay / Zero Indexed Pages

**Date:** 2026-01-19
**Subject:** Job 6 "indexed_pages" count remaining at 0 despite WARC generation.
**Status:** RESOLVED (Expected Behavior)

## Issue Description

During the deployment of the 2026 Annual Crawl Hardening, it was observed that Job 6 (Health Canada) had generated 56 WARC files but the `indexed_pages` metric in the database remained at `0`. This raised concerns that the indexing pipeline was broken or stalled.

## Investigation Steps (Phase 5)

1. **Static Analysis**: Searched for `index_job` calls in the worker source code.
2. **Runtime Analysis**: Verified `healtharchive-worker` logs.
3. **State Verification**: Checked filesystem for WARCs vs DB status.

## Findings

1. **Indexing is Terminal**:
   Code analysis of `src/ha_backend/worker/main.py` confirmed that `index_job(job_id)` is **only called after the crawl loop exits successfully**.
   Unlike some crawlers that index incrementally, HealthArchive currently indexes in batches after the crawl completes.

2. **Crawl is Active**:
   Job 6 is still in `running` state.
   - 56 WARC files exist on disk.
   - `last_progress` timestamps are updating.

3. **Conclusion**:
   The `indexed_pages=0` metric is **correct** for a running job. It will update to the full count once the job finishes and the indexing phase begins.

## Hardening Actions Taken

To prevent future confusion and catch *actual* indexing failures:

1. **New Alert**: `IndexingNotStartedAfterCrawl` (in `prometheus-alerts-crawl.yml`).
   - Fires if `status='completed'` AND `indexed_pages=0` for > 1 hour.
2. **Runbook**: `docs/operations/runbooks/indexing-not-started.md`.

## Resolution

No fix required. The system is functioning as designed. Monitoring will alert if the post-crawl indexing fails.
