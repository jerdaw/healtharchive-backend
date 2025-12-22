from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, Optional

from archive_tool.utils import parse_last_stats_from_log
from ha_backend.models import ArchiveJob

logger = logging.getLogger("healtharchive.crawl_stats")


def _find_latest_combined_log(output_dir: Path) -> Optional[Path]:
    """
    Locate the most recent archive_*.combined.log file under a job's output
    directory.
    """
    if not output_dir.is_dir():
        return None

    candidates = list(output_dir.glob("archive_*.combined.log"))
    if not candidates:
        return None

    # Pick the newest by modification time.
    latest = max(candidates, key=lambda p: p.stat().st_mtime)
    return latest


def _find_log_for_job(job: ArchiveJob) -> Optional[Path]:
    """
    Best-effort lookup of the combined log file for a job.

    Preference order:
    1. job.combined_log_path, if set and exists.
    2. Latest archive_*.combined.log file under job.output_dir.
    """
    # 1) Use explicit combined_log_path if present and valid.
    if job.combined_log_path:
        path = Path(job.combined_log_path)
        if path.is_file():
            return path

    # 2) Fall back to globbing in the output_dir.
    if not job.output_dir:
        return None

    output_dir = Path(job.output_dir)
    return _find_latest_combined_log(output_dir)


def update_job_stats_from_logs(job: ArchiveJob) -> None:
    """
    Populate last_stats_json and page counters on an ArchiveJob by parsing the
    latest crawl statistics from archive_tool logs.

    This is a best-effort helper: failures are logged and ignored so they do
    not interfere with job status updates.
    """
    try:
        log_path = _find_log_for_job(job)
        if log_path is None:
            logger.debug("No combined log found for job %s; skipping stats sync.", job.id)
            return

        stats: Optional[Dict[str, Any]] = parse_last_stats_from_log(log_path)
        if not stats:
            logger.debug("parse_last_stats_from_log returned no stats for %s; skipping.", log_path)
            return

        job.last_stats_json = stats

        crawled = stats.get("crawled")
        total = stats.get("total")
        failed = stats.get("failed")

        if crawled is not None:
            job.pages_crawled = int(crawled)
        if total is not None:
            job.pages_total = int(total)
        if failed is not None:
            job.pages_failed = int(failed)

        # Remember which log we used so admin APIs can surface it.
        job.combined_log_path = str(log_path)
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning(
            "Failed to update stats from logs for job %s: %s", getattr(job, "id", "?"), exc
        )


__all__ = ["update_job_stats_from_logs"]
