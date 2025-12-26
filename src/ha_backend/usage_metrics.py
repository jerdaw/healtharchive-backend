from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from typing import Dict, List

from sqlalchemy import func
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from ha_backend.config import (
    get_usage_metrics_enabled,
    get_usage_metrics_window_days,
)
from ha_backend.models import UsageMetric

logger = logging.getLogger(__name__)

EVENT_SEARCH_REQUEST = "search_request"
EVENT_SNAPSHOT_DETAIL = "snapshot_detail"
EVENT_SNAPSHOT_RAW = "snapshot_raw"
EVENT_REPORT_SUBMITTED = "report_submitted"

EVENT_CHANGES_LIST = "changes_list"
EVENT_COMPARE_VIEW = "compare_view"
EVENT_COMPARE_LIVE_VIEW = "compare_live_view"
EVENT_TIMELINE_VIEW = "timeline_view"
EVENT_EXPORTS_DOWNLOAD_SNAPSHOTS = "exports_download_snapshots"
EVENT_EXPORTS_DOWNLOAD_CHANGES = "exports_download_changes"

EVENTS = [
    EVENT_SEARCH_REQUEST,
    EVENT_SNAPSHOT_DETAIL,
    EVENT_SNAPSHOT_RAW,
    EVENT_REPORT_SUBMITTED,
    EVENT_CHANGES_LIST,
    EVENT_COMPARE_VIEW,
    EVENT_COMPARE_LIVE_VIEW,
    EVENT_TIMELINE_VIEW,
    EVENT_EXPORTS_DOWNLOAD_SNAPSHOTS,
    EVENT_EXPORTS_DOWNLOAD_CHANGES,
]


def _today_utc() -> date:
    return datetime.now(timezone.utc).date()


def record_usage_event(db: Session, event: str) -> None:
    """
    Record a single usage event into daily aggregates.

    This is best-effort and should never break request handling.
    """
    if not get_usage_metrics_enabled():
        return

    if event not in EVENTS:
        return

    metric_date = _today_utc()

    try:
        if db.bind is not None and db.bind.dialect.name == "postgresql":
            stmt = (
                insert(UsageMetric)
                .values(metric_date=metric_date, event=event, count=1)
                .on_conflict_do_update(
                    index_elements=["metric_date", "event"],
                    set_={
                        "count": UsageMetric.count + 1,
                        "updated_at": func.now(),
                    },
                )
            )
            db.execute(stmt)
        else:
            row = (
                db.query(UsageMetric)
                .filter(
                    UsageMetric.metric_date == metric_date,
                    UsageMetric.event == event,
                )
                .first()
            )
            if row:
                row.count += 1
            else:
                db.add(
                    UsageMetric(
                        metric_date=metric_date,
                        event=event,
                        count=1,
                    )
                )

        db.commit()
    except Exception:
        db.rollback()
        logger.warning("Failed to record usage metric", exc_info=True)


def build_usage_summary(
    db: Session, window_days: int | None = None
) -> tuple[date, date, Dict[str, int], List[Dict[str, int | str]]]:
    """
    Return usage metrics for a rolling window.

    Returns (start_date, end_date, totals, daily_rows).
    """
    if window_days is None:
        window_days = get_usage_metrics_window_days()

    end_date = _today_utc()
    start_date = end_date - timedelta(days=window_days - 1)

    rows = (
        db.query(UsageMetric)
        .filter(UsageMetric.metric_date >= start_date)
        .filter(UsageMetric.metric_date <= end_date)
        .all()
    )

    totals: Dict[str, int] = {event: 0 for event in EVENTS}
    daily_map: Dict[date, Dict[str, int]] = {}

    for row in rows:
        totals[row.event] = totals.get(row.event, 0) + int(row.count or 0)
        daily = daily_map.setdefault(
            row.metric_date,
            {event: 0 for event in EVENTS},
        )
        daily[row.event] = int(row.count or 0)

    daily_rows: List[Dict[str, int | str]] = []
    current = start_date
    while current <= end_date:
        daily = daily_map.get(current, {event: 0 for event in EVENTS})
        daily_rows.append(
            {
                "date": current.isoformat(),
                **daily,
            }
        )
        current += timedelta(days=1)

    return start_date, end_date, totals, daily_rows


__all__ = [
    "EVENT_SEARCH_REQUEST",
    "EVENT_SNAPSHOT_DETAIL",
    "EVENT_SNAPSHOT_RAW",
    "EVENT_REPORT_SUBMITTED",
    "EVENT_CHANGES_LIST",
    "EVENT_COMPARE_VIEW",
    "EVENT_COMPARE_LIVE_VIEW",
    "EVENT_TIMELINE_VIEW",
    "EVENT_EXPORTS_DOWNLOAD_SNAPSHOTS",
    "EVENT_EXPORTS_DOWNLOAD_CHANGES",
    "EVENTS",
    "record_usage_event",
    "build_usage_summary",
]
