from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from archive_tool.utils import parse_last_stats_from_log
from ha_backend.models import ArchiveJob

logger = logging.getLogger("healtharchive.crawl_stats")

CrawlStatusLogContext = "crawlStatus"
CrawlStatusLogMessage = "Crawl statistics"


def _parse_utc_timestamp(raw: str | None) -> datetime | None:
    if not raw:
        return None
    s = str(raw).strip()
    if not s:
        return None
    # Common zimit JSON uses RFC3339 with trailing "Z".
    if s.endswith("Z"):
        s = f"{s[:-1]}+00:00"
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


@dataclass(frozen=True)
class CrawlStatusEvent:
    timestamp_utc: datetime
    crawled: int
    total: int
    pending: int | None
    failed: int | None


@dataclass(frozen=True)
class CrawlLogProgress:
    log_path: Path
    last_status: CrawlStatusEvent
    last_crawled_change_timestamp_utc: datetime

    def last_progress_age_seconds(self, *, now_utc: datetime | None = None) -> float:
        now = now_utc or datetime.now(timezone.utc)
        return max(0.0, (now - self.last_crawled_change_timestamp_utc).total_seconds())


def parse_crawl_status_events_from_log_tail(
    log_path: Path, *, max_bytes: int = 1024 * 1024
) -> List[CrawlStatusEvent]:
    """
    Best-effort parse of crawlStatus events from the tail of an archive_tool combined log.

    Intended for lightweight monitoring/metrics without reading the full log.
    """
    if not log_path or not log_path.is_file():
        return []

    try:
        with open(log_path, "rb") as f:
            f.seek(max(0, log_path.stat().st_size - max_bytes))
            tail = f.read().decode("utf-8", errors="replace")
    except Exception as exc:  # pragma: no cover - defensive
        logger.debug("Failed to read crawlStatus tail from %s: %s", log_path, exc)
        return []

    events: List[CrawlStatusEvent] = []
    for line in tail.splitlines():
        if CrawlStatusLogContext not in line:
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if payload.get("context") != CrawlStatusLogContext:
            continue
        if payload.get("message") != CrawlStatusLogMessage:
            continue
        details = payload.get("details")
        if not isinstance(details, dict):
            continue

        crawled = details.get("crawled")
        total = details.get("total")
        if crawled is None or total is None:
            continue

        ts = _parse_utc_timestamp(payload.get("timestamp"))
        if ts is None:
            continue

        events.append(
            CrawlStatusEvent(
                timestamp_utc=ts,
                crawled=int(crawled),
                total=int(total),
                pending=int(details["pending"]) if details.get("pending") is not None else None,
                failed=int(details["failed"]) if details.get("failed") is not None else None,
            )
        )

    return events


def parse_crawl_log_progress(
    log_path: Path, *, max_bytes: int = 1024 * 1024
) -> CrawlLogProgress | None:
    """
    Summarize crawl progress from the tail of a combined log.

    Returns the most recent crawlStatus event plus the timestamp of the most
    recent *crawled count change* (useful for stall detection).
    """
    events = parse_crawl_status_events_from_log_tail(log_path, max_bytes=max_bytes)
    if not events:
        return None

    last_event = events[-1]
    last_crawled = last_event.crawled
    last_change_timestamp = last_event.timestamp_utc

    # Walk backwards until we find a different crawled count; then the change
    # happened at the next event forward.
    for i in range(len(events) - 2, -1, -1):
        if events[i].crawled != last_crawled:
            last_change_timestamp = events[i + 1].timestamp_utc
            break

    return CrawlLogProgress(
        log_path=log_path,
        last_status=last_event,
        last_crawled_change_timestamp_utc=last_change_timestamp,
    )


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


__all__ = [
    "CrawlLogProgress",
    "CrawlStatusEvent",
    "parse_crawl_log_progress",
    "parse_crawl_status_events_from_log_tail",
    "update_job_stats_from_logs",
]
