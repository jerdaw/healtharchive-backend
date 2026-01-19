# Runbook: CrawlRestartBudgetLow

**Alert Name:** `CrawlRestartBudgetLow`
**Severity:** Warning
**Trigger:** `healtharchive_crawl_running_job_container_restarts_done > 15` (limit is 20).

## Description
The annual crawl job is restarting its zimit container frequently. Annual jobs are configured with `max_container_restarts=20` to survive occasional timeouts or memory leaks. Reaching 15 restarts means the job is consuming its budget faster than expected and risks failing completely.

## Impact
- If restarts hit 20, the job enters `failed` state and crawling stops.
- This protects the infrastructure from infinite loops but might leave the crawl incomplete.

## Diagnosis
1.  **Check Restart Rate**:
    Is the job restarting every few minutes (thrashing) or once every few hours?
    ```bash
    # Check restart timestamps
    /opt/healtharchive-backend/scripts/vps-crawl-status.sh
    ```

2.  **Review Crash Reasons**:
    Check the combined log for the reason *before* the restart.
    ```bash
    tail -n 500 /srv/healtharchive/jobs/<source>/archive_*.combined.log
    ```
    - **TimeoutErrors**: Site is too slow.
    - **HTTP 5xx**: Site is overloaded.
    - **OOM / Killed**: Zimit is running out of RAM.

## Mitigation
1.  **Increase Budget (If progress is good)**:
    If the job is making good progress (thousands of pages) and just hitting occasional glitches, you can manually increase the budget in the database to keep it going.

    ```bash
    ha-backend db-shell
    # UPDATE archive_jobs SET tool_options = jsonb_set(tool_options, '{max_container_restarts}', '30') WHERE id=6;
    ```
    Then restart the worker to pick up the new config:
    ```bash
    sudo systemctl restart healtharchive-worker.service
    ```

2.  **Pause Job (If thrashing)**:
    If restarts are happening rapidly with no progress, pause the job to save resources.
    ```bash
    ha-backend db-shell
    # UPDATE archive_jobs SET status='paused' WHERE id=6;
    ```
