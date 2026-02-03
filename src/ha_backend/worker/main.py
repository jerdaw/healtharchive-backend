from __future__ import annotations

import logging
import os
import subprocess
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from sqlalchemy import or_
from sqlalchemy.orm import Session

from ha_backend.db import get_session
from ha_backend.indexing import index_job
from ha_backend.jobs import JobAlreadyRunningError, run_persistent_job
from ha_backend.models import ArchiveJob, Source

"""
ha_backend.worker.main - Job worker loop

Picks queued/retryable jobs, runs archive_tool crawls, and triggers indexing.
Implements retry logic with cooldowns for infrastructure errors and disk
pressure protection.

Key thresholds defined here:
    - DISK_HEADROOM_THRESHOLD_PERCENT (85%): Skip crawls if disk usage exceeds
    - INFRA_ERROR_RETRY_COOLDOWN_MINUTES (10): Cooldown after infra errors
    - MAX_CRAWL_RETRIES (2): Maximum retry attempts per job

See also:
    - docs/operations/thresholds-and-tuning.md for operational guidance
    - docs/architecture.md for worker lifecycle details
"""

logger = logging.getLogger("healtharchive.worker")

# Worker retry and threshold constants
MAX_CRAWL_RETRIES = 2
DEFAULT_POLL_INTERVAL = 30
INFRA_ERROR_RETRY_COOLDOWN_MINUTES = 10

# Disk headroom threshold: skip crawl if disk usage exceeds this percentage
DISK_HEADROOM_THRESHOLD_PERCENT = 85
DISK_HEADROOM_CHECK_PATH = "/srv/healtharchive/jobs"

# Subprocess timeouts and logging
FINDMNT_TIMEOUT_SEC = 5  # Timeout for mountpoint check via findmnt
AUTO_TIERING_TIMEOUT_SEC = 120  # Timeout for auto-tiering script execution
STDERR_LOG_TRUNCATE_LENGTH = 500  # Truncate stderr output in logs to this length


def _is_mountpoint(path: Path) -> bool:
    """Check if path is a mountpoint using findmnt."""
    try:
        result = subprocess.run(
            ["findmnt", "-T", str(path), "-o", "TARGET", "-n"],
            check=False,
            capture_output=True,
            text=True,
            timeout=FINDMNT_TIMEOUT_SEC,
        )
        return result.returncode == 0 and result.stdout.strip() == str(path)
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False


def _check_disk_headroom() -> tuple[bool, int]:
    """
    Check if there's enough disk headroom to start a crawl.

    Returns:
        Tuple of (has_headroom, usage_percent).
        has_headroom is True if disk usage is below threshold.
    """
    check_path = DISK_HEADROOM_CHECK_PATH
    try:
        stat = os.statvfs(check_path)
        total = stat.f_blocks * stat.f_frsize
        free = stat.f_bavail * stat.f_frsize
        if total == 0:
            return True, 0  # Can't determine, allow crawl
        usage_percent = int(100 * (total - free) / total)
        has_headroom = usage_percent < DISK_HEADROOM_THRESHOLD_PERCENT
        return has_headroom, usage_percent
    except OSError as e:
        logger.warning("Could not check disk headroom at %s: %s", check_path, e)
        return True, 0  # On error, allow crawl to proceed


def _tier_annual_job_if_needed(job: ArchiveJob) -> None:
    """
    Automatically tier annual campaign jobs to storagebox before they start crawling.

    This prevents disk pressure from large annual jobs consuming local disk.
    Only tiers if the job is an annual campaign and not already tiered.
    """
    config = job.config or {}
    if config.get("campaign_kind") != "annual":
        return  # Not an annual job, skip tiering

    output_dir = Path(job.output_dir)
    if not output_dir.exists():
        logger.debug("Job %s output_dir does not exist yet, skipping pre-tier check", job.id)
        return

    # Check if already tiered (is a mountpoint)
    if _is_mountpoint(output_dir):
        logger.debug("Job %s already tiered (is mountpoint), skipping", job.id)
        return

    campaign_year = config.get("campaign_year")
    if not campaign_year:
        logger.warning("Job %s is annual but missing campaign_year, cannot tier", job.id)
        return

    logger.info(
        "Auto-tiering annual job %s (year=%s) to storagebox before crawl starts",
        job.id,
        campaign_year,
    )

    try:
        # Run the tiering script
        result = subprocess.run(
            [
                "/opt/healtharchive-backend/.venv/bin/python3",
                "/opt/healtharchive-backend/scripts/vps-annual-output-tiering.py",
                "--year",
                str(campaign_year),
                "--apply",
                "--repair-stale-mounts",
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=AUTO_TIERING_TIMEOUT_SEC,
        )

        if result.returncode != 0:
            logger.error(
                "Auto-tiering failed for job %s: RC=%s, stderr=%s",
                job.id,
                result.returncode,
                result.stderr[:STDERR_LOG_TRUNCATE_LENGTH],
            )
        else:
            logger.info("Auto-tiering completed successfully for job %s", job.id)
    except subprocess.TimeoutExpired:
        logger.error(
            "Auto-tiering timed out for job %s after %ds", job.id, AUTO_TIERING_TIMEOUT_SEC
        )
    except Exception as e:
        logger.error("Auto-tiering exception for job %s: %s", job.id, e)


def _select_next_crawl_job(session: Session, *, now_utc: datetime) -> Optional[ArchiveJob]:
    """
    Select the next job that needs a crawl phase.

    To avoid alert storms and tight retry loops when infrastructure is unhealthy
    (e.g. Errno 107 stale SSHFS mountpoints), we temporarily skip jobs that most
    recently ended in crawler_status=infra_error.
    """
    infra_error_cutoff = now_utc - timedelta(minutes=INFRA_ERROR_RETRY_COOLDOWN_MINUTES)
    return (
        session.query(ArchiveJob)
        .join(Source)
        .filter(
            ArchiveJob.status.in_(["queued", "retryable"]),
            or_(
                ArchiveJob.crawler_status.is_(None),
                ArchiveJob.crawler_status != "infra_error",
                ArchiveJob.updated_at <= infra_error_cutoff,
            ),
        )
        .order_by(ArchiveJob.queued_at.asc().nullsfirst(), ArchiveJob.created_at.asc())
        .first()
    )


def _process_single_job() -> bool:
    """
    Attempt to process a single job.

    Returns:
        True if a job was processed (crawl and/or index), False if no work was found.
    """
    job_id: Optional[int] = None

    # Pre-flight: check disk headroom before selecting a job.
    # This prevents starting crawls when disk is already under pressure.
    has_headroom, disk_percent = _check_disk_headroom()
    if not has_headroom:
        logger.warning(
            "Disk usage at %d%% exceeds threshold (%d%%); skipping crawl to prevent disk-full failures.",
            disk_percent,
            DISK_HEADROOM_THRESHOLD_PERCENT,
        )
        return False

    # Select a job that needs crawling.
    now_utc = datetime.now(timezone.utc)
    with get_session() as session:
        job = _select_next_crawl_job(session, now_utc=now_utc)
        if job is None:
            return False
        job_id = job.id
        source = job.source
        logger.info(
            "Worker picked job %s for source %s (%s) with status %s and retry_count %s (disk: %d%%)",
            job_id,
            source.code if source else "unknown",
            source.name if source else "unknown",
            job.status,
            job.retry_count,
            disk_percent,
        )

        # Auto-tier annual jobs to storagebox before crawl starts (prevents disk pressure)
        _tier_annual_job_if_needed(job)

    # Run the crawl phase using the existing helper, which manages its own sessions.
    try:
        crawl_rc = run_persistent_job(job_id)
    except JobAlreadyRunningError as exc:
        logger.warning(
            "Job %s appears to already be running (lock held at %s); syncing DB status to 'running' and skipping.",
            job_id,
            exc.lock_path,
        )
        with get_session() as session:
            job = session.get(ArchiveJob, job_id)
            if job is not None and job.status != "running":
                job.status = "running"
                if job.started_at is None:
                    job.started_at = datetime.now(timezone.utc)
                job.finished_at = None
        return True

    # Post-crawl handling: update retry semantics.
    with get_session() as session:
        job = session.get(ArchiveJob, job_id)
        if job is None:
            logger.error("Job %s vanished from database after crawl.", job_id)
            return True

        if job.crawler_status == "infra_error":
            if job.status != "retryable":
                job.status = "retryable"
            logger.warning(
                "Crawl for job %s failed due to infra error (RC=%s). Not consuming retry budget (retry_count=%s).",
                job_id,
                crawl_rc,
                job.retry_count,
            )
            return True

        if job.crawler_status == "infra_error_config":
            logger.error(
                "Crawl for job %s failed due to configuration/runtime error (RC=%s). Leaving status as %s.",
                job_id,
                crawl_rc,
                job.status,
            )
            return True

        if crawl_rc != 0 or job.status == "failed":
            # Crawl failed; decide whether to mark as retryable.
            if job.retry_count < MAX_CRAWL_RETRIES:
                job.retry_count += 1
                job.status = "retryable"
                logger.warning(
                    "Crawl for job %s failed (RC=%s). Marking as retryable (retry_count=%s).",
                    job_id,
                    crawl_rc,
                    job.retry_count,
                )
            else:
                logger.error(
                    "Crawl for job %s failed (RC=%s) and max retries reached; leaving status as %s.",
                    job_id,
                    crawl_rc,
                    job.status,
                )
            return True

        # If we reach here, crawl succeeded and job.status should be 'completed'.
        logger.info("Crawl for job %s completed successfully; starting indexing.", job_id)

    # Run indexing phase.
    index_rc = index_job(job_id)
    if index_rc != 0:
        logger.error("Indexing for job %s failed with RC=%s.", job_id, index_rc)
    else:
        logger.info("Indexing for job %s completed successfully.", job_id)

    return True


def run_worker_loop(poll_interval: int = DEFAULT_POLL_INTERVAL, run_once: bool = False) -> None:
    """
    Main worker loop.

    Args:
        poll_interval: Seconds to sleep between polls when no work is found.
        run_once: If True, perform a single iteration and return.
    """
    logger.info("Worker starting (poll_interval=%s, run_once=%s).", poll_interval, run_once)

    try:
        while True:
            processed = False
            try:
                processed = _process_single_job()
            except Exception as exc:  # pragma: no cover - defensive
                logger.error("Unexpected error in worker iteration: %s", exc)

            if run_once:
                break

            if not processed:
                logger.info("No queued jobs found; sleeping for %s seconds.", poll_interval)
                time.sleep(poll_interval)
    except KeyboardInterrupt:  # pragma: no cover - manual interruption
        logger.info("Worker interrupted by user; shutting down.")
