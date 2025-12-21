from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import logging
from typing import Dict, Iterable, List, Optional, Sequence

from sqlalchemy import func
from sqlalchemy.orm import Session

from ha_backend.config import get_change_tracking_enabled
from ha_backend.diffing import (
    DIFF_VERSION,
    NORMALIZATION_VERSION,
    compute_diff,
    normalize_html_for_diff,
)
from ha_backend.indexing.viewer import find_record_for_snapshot
from ha_backend.models import ArchiveJob, Snapshot, SnapshotChange, Source
from ha_backend.url_normalization import normalize_url_for_grouping

logger = logging.getLogger(__name__)

CHANGE_TYPE_UPDATED = "updated"
CHANGE_TYPE_UNCHANGED = "unchanged"
CHANGE_TYPE_NEW_PAGE = "new_page"
CHANGE_TYPE_ERROR = "error"

CHANGE_TYPES = [
    CHANGE_TYPE_UPDATED,
    CHANGE_TYPE_UNCHANGED,
    CHANGE_TYPE_NEW_PAGE,
    CHANGE_TYPE_ERROR,
]


@dataclass
class ChangeComputeResult:
    created: int
    skipped: int
    errors: int


def _today_utc() -> datetime:
    return datetime.now(timezone.utc)


def _is_html_snapshot(snapshot: Snapshot) -> bool:
    if not snapshot.mime_type:
        return False
    return snapshot.mime_type.lower().startswith("text/html")


def _load_snapshot_html(snapshot: Snapshot) -> str:
    record = find_record_for_snapshot(snapshot)
    if record is None:
        raise ValueError("WARC record not found for snapshot.")
    try:
        return record.body_bytes.decode("utf-8", errors="replace")
    except Exception as exc:
        raise ValueError(f"Failed to decode HTML: {exc}") from exc


def _summarize_change(
    *,
    change_type: str,
    added_sections: int | None = None,
    removed_sections: int | None = None,
    changed_sections: int | None = None,
    added_lines: int | None = None,
    removed_lines: int | None = None,
    high_noise: bool = False,
) -> str:
    if change_type == CHANGE_TYPE_NEW_PAGE:
        return "First archived capture for this page."
    if change_type == CHANGE_TYPE_UNCHANGED:
        return "No text changes detected (identical content hash)."
    if change_type == CHANGE_TYPE_ERROR:
        return "Diff unavailable due to a processing error."

    parts: List[str] = []
    if changed_sections:
        parts.append(f"{changed_sections} sections changed")
    if added_sections:
        parts.append(f"{added_sections} added")
    if removed_sections:
        parts.append(f"{removed_sections} removed")

    if not parts and (added_lines or removed_lines):
        parts.append(
            f"{added_lines or 0} lines added; {removed_lines or 0} removed"
        )

    if not parts:
        parts.append("Archived text updated")

    summary = "; ".join(parts)
    if high_noise:
        summary = f"{summary} (high-noise change)"
    return summary


def _compute_section_stats(
    doc_a: Optional[dict[str, str]],
    doc_b: Optional[dict[str, str]],
) -> tuple[int, int, int]:
    if not doc_a and not doc_b:
        return 0, 0, 0

    sections_a = set(doc_a.keys()) if doc_a else set()
    sections_b = set(doc_b.keys()) if doc_b else set()

    added = len(sections_b - sections_a)
    removed = len(sections_a - sections_b)

    common = sections_a & sections_b
    changed = 0
    for title in common:
        if (doc_a or {}).get(title, "") != (doc_b or {}).get(title, ""):
            changed += 1

    return added, removed, changed


def _build_change_event(
    *,
    to_snapshot: Snapshot,
    from_snapshot: Optional[Snapshot],
    change_type: str,
    summary: str,
    diff_html: Optional[str] = None,
    diff_truncated: bool = False,
    added_sections: Optional[int] = None,
    removed_sections: Optional[int] = None,
    changed_sections: Optional[int] = None,
    added_lines: Optional[int] = None,
    removed_lines: Optional[int] = None,
    change_ratio: Optional[float] = None,
    high_noise: bool = False,
    error_message: Optional[str] = None,
    computed_by: str = "cli",
) -> SnapshotChange:
    return SnapshotChange(
        source_id=to_snapshot.source_id,
        normalized_url_group=to_snapshot.normalized_url_group
        or normalize_url_for_grouping(to_snapshot.url),
        from_snapshot_id=from_snapshot.id if from_snapshot else None,
        to_snapshot_id=to_snapshot.id,
        from_job_id=from_snapshot.job_id if from_snapshot else None,
        to_job_id=to_snapshot.job_id,
        from_capture_timestamp=from_snapshot.capture_timestamp if from_snapshot else None,
        to_capture_timestamp=to_snapshot.capture_timestamp,
        change_type=change_type,
        summary=summary,
        diff_format="html" if diff_html else None,
        diff_html=diff_html,
        diff_truncated=diff_truncated,
        added_sections=added_sections,
        removed_sections=removed_sections,
        changed_sections=changed_sections,
        added_lines=added_lines,
        removed_lines=removed_lines,
        change_ratio=change_ratio,
        high_noise=high_noise,
        diff_version=DIFF_VERSION if diff_html is not None else None,
        normalization_version=(
            NORMALIZATION_VERSION if diff_html is not None else None
        ),
        computed_at=_today_utc(),
        computed_by=computed_by,
        error_message=error_message,
    )


def compute_change_for_snapshot_pair(
    to_snapshot: Snapshot,
    from_snapshot: Optional[Snapshot],
    *,
    computed_by: str = "cli",
) -> SnapshotChange:
    if from_snapshot is None:
        summary = _summarize_change(change_type=CHANGE_TYPE_NEW_PAGE)
        return _build_change_event(
            to_snapshot=to_snapshot,
            from_snapshot=None,
            change_type=CHANGE_TYPE_NEW_PAGE,
            summary=summary,
            computed_by=computed_by,
        )

    if (
        to_snapshot.content_hash
        and from_snapshot.content_hash
        and to_snapshot.content_hash == from_snapshot.content_hash
    ):
        summary = _summarize_change(change_type=CHANGE_TYPE_UNCHANGED)
        return _build_change_event(
            to_snapshot=to_snapshot,
            from_snapshot=from_snapshot,
            change_type=CHANGE_TYPE_UNCHANGED,
            summary=summary,
            computed_by=computed_by,
        )

    if not (_is_html_snapshot(to_snapshot) and _is_html_snapshot(from_snapshot)):
        summary = "Content updated (diff unavailable for non-HTML capture)."
        return _build_change_event(
            to_snapshot=to_snapshot,
            from_snapshot=from_snapshot,
            change_type=CHANGE_TYPE_UPDATED,
            summary=summary,
            high_noise=True,
            computed_by=computed_by,
        )

    try:
        html_a = _load_snapshot_html(from_snapshot)
        html_b = _load_snapshot_html(to_snapshot)
        doc_a = normalize_html_for_diff(html_a)
        doc_b = normalize_html_for_diff(html_b)
        diff = compute_diff(doc_a, doc_b)

        section_map_a = {title: text for title, text in doc_a.sections}
        section_map_b = {title: text for title, text in doc_b.sections}
        added_sections, removed_sections, changed_sections = _compute_section_stats(
            section_map_a, section_map_b
        )

        high_noise = diff.change_ratio >= 0.6 or (
            len(doc_b.lines) > 0
            and (diff.added_lines + diff.removed_lines) / max(len(doc_b.lines), 1) > 0.7
        )

        summary = _summarize_change(
            change_type=CHANGE_TYPE_UPDATED,
            added_sections=added_sections,
            removed_sections=removed_sections,
            changed_sections=changed_sections,
            added_lines=diff.added_lines,
            removed_lines=diff.removed_lines,
            high_noise=high_noise,
        )

        return _build_change_event(
            to_snapshot=to_snapshot,
            from_snapshot=from_snapshot,
            change_type=CHANGE_TYPE_UPDATED,
            summary=summary,
            diff_html=diff.diff_html,
            diff_truncated=diff.diff_truncated,
            added_sections=added_sections,
            removed_sections=removed_sections,
            changed_sections=changed_sections,
            added_lines=diff.added_lines,
            removed_lines=diff.removed_lines,
            change_ratio=diff.change_ratio,
            high_noise=high_noise,
            computed_by=computed_by,
        )
    except Exception as exc:
        summary = _summarize_change(change_type=CHANGE_TYPE_ERROR)
        return _build_change_event(
            to_snapshot=to_snapshot,
            from_snapshot=from_snapshot,
            change_type=CHANGE_TYPE_ERROR,
            summary=summary,
            high_noise=True,
            error_message=str(exc),
            computed_by=computed_by,
        )


def compute_changes_backfill(
    db: Session,
    *,
    source_code: Optional[str] = None,
    max_events: int = 200,
    dry_run: bool = False,
) -> ChangeComputeResult:
    if not get_change_tracking_enabled():
        return ChangeComputeResult(created=0, skipped=0, errors=0)

    existing_to_ids = {
        row[0]
        for row in db.query(SnapshotChange.to_snapshot_id).all()
        if row[0] is not None
    }

    query = db.query(Snapshot).filter(Snapshot.normalized_url_group.isnot(None))
    if source_code:
        source = db.query(Source).filter(Source.code == source_code).first()
        if not source:
            raise ValueError("Source not found.")
        query = query.filter(Snapshot.source_id == source.id)

    query = query.order_by(
        Snapshot.source_id.asc(),
        Snapshot.normalized_url_group.asc(),
        Snapshot.capture_timestamp.asc(),
        Snapshot.id.asc(),
    )

    created = 0
    skipped = 0
    errors = 0
    last_group_key: tuple[int | None, str | None] | None = None
    last_snapshot: Optional[Snapshot] = None

    for snap in query.yield_per(500):
        group_key = (snap.source_id, snap.normalized_url_group)
        if group_key != last_group_key:
            last_snapshot = None
            last_group_key = group_key

        if snap.id in existing_to_ids:
            skipped += 1
            last_snapshot = snap
            continue

        event = compute_change_for_snapshot_pair(
            to_snapshot=snap,
            from_snapshot=last_snapshot,
            computed_by="backfill",
        )

        if not dry_run:
            db.add(event)
            db.flush()
            existing_to_ids.add(snap.id)

        created += 1
        last_snapshot = snap

        if created >= max_events:
            break

    if not dry_run:
        db.commit()

    return ChangeComputeResult(created=created, skipped=skipped, errors=errors)


def compute_changes_since(
    db: Session,
    *,
    since_days: int,
    source_code: Optional[str] = None,
    max_events: int = 200,
    dry_run: bool = False,
) -> ChangeComputeResult:
    if not get_change_tracking_enabled():
        return ChangeComputeResult(created=0, skipped=0, errors=0)

    since_ts = _today_utc() - timedelta(days=since_days)

    query = (
        db.query(Snapshot)
        .filter(Snapshot.normalized_url_group.isnot(None))
        .filter(Snapshot.capture_timestamp >= since_ts)
    )
    if source_code:
        source = db.query(Source).filter(Source.code == source_code).first()
        if not source:
            raise ValueError("Source not found.")
        query = query.filter(Snapshot.source_id == source.id)

    query = query.order_by(
        Snapshot.capture_timestamp.asc(),
        Snapshot.id.asc(),
    )

    created = 0
    skipped = 0
    errors = 0

    for snap in query.yield_per(200):
        exists = (
            db.query(SnapshotChange.id)
            .filter(SnapshotChange.to_snapshot_id == snap.id)
            .first()
        )
        if exists:
            skipped += 1
            continue

        prev = (
            db.query(Snapshot)
            .filter(Snapshot.source_id == snap.source_id)
            .filter(Snapshot.normalized_url_group == snap.normalized_url_group)
            .filter(Snapshot.capture_timestamp < snap.capture_timestamp)
            .order_by(Snapshot.capture_timestamp.desc(), Snapshot.id.desc())
            .first()
        )

        event = compute_change_for_snapshot_pair(
            to_snapshot=snap,
            from_snapshot=prev,
            computed_by="incremental",
        )

        if not dry_run:
            db.add(event)
            db.flush()

        created += 1

        if created >= max_events:
            break

    if not dry_run:
        db.commit()

    return ChangeComputeResult(created=created, skipped=skipped, errors=errors)


def get_latest_job_ids_by_source(
    db: Session,
    *,
    source_id: Optional[int] = None,
) -> Dict[int, int]:
    job_agg = (
        db.query(
            Snapshot.job_id.label("job_id"),
            Snapshot.source_id.label("source_id"),
            func.max(Snapshot.capture_timestamp).label("last_capture"),
        )
        .filter(Snapshot.job_id.isnot(None))
        .group_by(Snapshot.job_id, Snapshot.source_id)
        .subquery()
    )

    rows = (
        db.query(
            job_agg.c.source_id,
            job_agg.c.job_id,
            job_agg.c.last_capture,
        )
        .join(ArchiveJob, ArchiveJob.id == job_agg.c.job_id)
        .filter(ArchiveJob.status == "indexed")
        .order_by(job_agg.c.last_capture.desc(), job_agg.c.job_id.desc())
        .all()
    )

    latest: Dict[int, int] = {}
    for source_id_row, job_id, _last_capture in rows:
        if source_id is not None and source_id_row != source_id:
            continue
        if source_id_row is None or job_id is None:
            continue
        if source_id_row not in latest:
            latest[source_id_row] = int(job_id)

    return latest


__all__ = [
    "CHANGE_TYPE_UPDATED",
    "CHANGE_TYPE_UNCHANGED",
    "CHANGE_TYPE_NEW_PAGE",
    "CHANGE_TYPE_ERROR",
    "CHANGE_TYPES",
    "ChangeComputeResult",
    "compute_change_for_snapshot_pair",
    "compute_changes_backfill",
    "compute_changes_since",
    "get_latest_job_ids_by_source",
]
