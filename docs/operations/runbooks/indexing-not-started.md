# Runbook: IndexingNotStartedAfterCrawl

**Alert Name:** `IndexingNotStartedAfterCrawl`
**Severity:** Critical
**Trigger:** `status="completed"` AND `indexed_pages == 0` for > 1 hour.

## Description

A crawl job has successfully completed (reached the "completed" state with RC 0), but the `indexed_pages` count remains at 0 for more than an hour. This suggests the indexing pipeline—which should run immediately after crawl completion—failed to start or crash-looped silently.

## Background

The `healtharchive-worker` process runs jobs in two phases:

1. **Crawl Phase**: Runs `archive-tool` container.
2. **Index Phase**: Runs `index_job()` Python function.

If phase 1 finishes but phase 2 crashes or fails to commit, this alert fires.

## Diagnosis

1. **Check Indexing Logs**:
   Search for the transition from crawl to index in the worker logs.

   ```bash
   sudo journalctl -u healtharchive-worker.service --since "4 hours ago" | grep -i "indexing"
   ```

   Look for:
   - `Starting indexing for job <ID>` (Good)
   - `Indexing for job <ID> failed: ...` (Bad)

2. **Verify WARC Existence**:
   Confirm the WARCs physically exist.

   ```bash
   /opt/healtharchive-backend/scripts/vps-crawl-status.sh --job-id <ID>
   ```

3. **Check Job Status**:
   Is the job status actually `completed` or `index_failed`?

   ```bash
   ha-backend show-job <ID>
   ```

## Mitigation

1. **Manual Re-indexing**:
   If the pipeline failed transiently (e.g. DB lock), you can reset the job status to trigger re-indexing.
   **Warning**: This *restarts the logic loop*. Ensure the crawl is truly done.

   ```bash
   ha-backend reindex-job <ID>
   ```

   *(Note: Check if `reindex-job` CLI command exists, otherwise use python shell)*:

   ```python
   from ha_backend.indexing import index_job
   index_job(<ID>)
   ```
