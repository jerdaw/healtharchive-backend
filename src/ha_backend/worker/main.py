from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import or_
from sqlalchemy.orm import Session

from ha_backend.db import get_session
from ha_backend.indexing import index_job
from ha_backend.jobs import run_persistent_job
from ha_backend.models import ArchiveJob, Source

logger = logging.getLogger("healtharchive.worker")

MAX_CRAWL_RETRIES = 2
DEFAULT_POLL_INTERVAL = 30
INFRA_ERROR_RETRY_COOLDOWN_MINUTES = 10


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

    # Select a job that needs crawling.
    now_utc = datetime.now(timezone.utc)
    with get_session() as session:
        job = _select_next_crawl_job(session, now_utc=now_utc)
        if job is None:
            return False
        job_id = job.id
        source = job.source
        logger.info(
            "Worker picked job %s for source %s (%s) with status %s and retry_count %s",
            job_id,
            source.code if source else "unknown",
            source.name if source else "unknown",
            job.status,
            job.retry_count,
        )

    # Run the crawl phase using the existing helper, which manages its own sessions.
    crawl_rc = run_persistent_job(job_id)

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
