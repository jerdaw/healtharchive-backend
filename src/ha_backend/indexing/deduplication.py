"""
Same-day snapshot deduplication.

Identifies and optionally marks snapshots that are same-day duplicates
(same normalized_url_group, same date, same content_hash, same source).

Default mode is dry-run; callers must explicitly opt in to apply changes.
All applied deduplication is audited in the snapshot_deduplications table
and is fully reversible.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session

from ha_backend.models import Snapshot, SnapshotDeduplication

logger = logging.getLogger("healtharchive.deduplication")


@dataclass
class DedupCandidate:
    """A single snapshot identified as a duplicate."""

    duplicate_id: int
    canonical_id: int
    url: str
    capture_date: str
    content_hash: str


@dataclass
class DedupResult:
    """Result of a deduplication operation."""

    candidates: list[DedupCandidate] = field(default_factory=list)
    applied: bool = False
    deduped_count: int = 0
    skipped_count: int = 0


def _capture_date_expr(col: Any) -> Any:
    """Date expression compatible with both SQLite and PostgreSQL."""
    from sqlalchemy import func as sqla_func

    # SQLite: date(col) returns 'YYYY-MM-DD'
    # PostgreSQL: DATE(col) returns date type
    # Both are comparable as strings/dates
    return sqla_func.date(col)


def find_same_day_duplicates(
    session: Session,
    *,
    job_id: int | None = None,
    source_id: int | None = None,
) -> list[DedupCandidate]:
    """
    Find same-day duplicate snapshots.

    A snapshot is a duplicate if another snapshot exists with:
    - Same normalized_url_group (or same URL if group is NULL)
    - Same date (DATE(capture_timestamp))
    - Same content_hash (must be non-NULL)
    - Same source_id

    The canonical snapshot is the one with the lowest ID (earliest indexed).

    Args:
        session: SQLAlchemy session
        job_id: If provided, only consider snapshots from this job
        source_id: If provided, only consider snapshots from this source

    Returns:
        List of DedupCandidate objects identifying duplicates
    """
    capture_day = _capture_date_expr(Snapshot.capture_timestamp).label("capture_day")
    group_key = func.coalesce(Snapshot.normalized_url_group, Snapshot.url).label("group_key")

    # Build base query filtering
    base_filter = [
        Snapshot.content_hash.isnot(None),
        Snapshot.content_hash != "",
        Snapshot.deduplicated.is_(False),
    ]
    if job_id is not None:
        base_filter.append(Snapshot.job_id == job_id)
    if source_id is not None:
        base_filter.append(Snapshot.source_id == source_id)

    # Find groups with duplicates: (source_id, group_key, capture_day, content_hash)
    # that have more than one snapshot
    dup_groups = (
        session.query(
            Snapshot.source_id,
            group_key,
            capture_day,
            Snapshot.content_hash,
            func.min(Snapshot.id).label("canonical_id"),
            func.count(Snapshot.id).label("cnt"),
        )
        .filter(*base_filter)
        .group_by(
            Snapshot.source_id,
            group_key,
            capture_day,
            Snapshot.content_hash,
        )
        .having(func.count(Snapshot.id) > 1)
        .subquery()
    )

    # Now find individual duplicate snapshots (id > canonical_id in each group)
    candidates: list[DedupCandidate] = []

    for row in session.query(
        dup_groups.c.source_id,
        dup_groups.c.group_key,
        dup_groups.c.capture_day,
        dup_groups.c.content_hash,
        dup_groups.c.canonical_id,
    ).all():
        src_id, grp_key, cap_day, c_hash, canonical_id = row

        # Find all duplicates in this group (everything except canonical)
        group_key_expr = func.coalesce(Snapshot.normalized_url_group, Snapshot.url)
        dup_snaps = (
            session.query(Snapshot.id, Snapshot.url)
            .filter(
                Snapshot.source_id == src_id,
                group_key_expr == grp_key,
                _capture_date_expr(Snapshot.capture_timestamp) == cap_day,
                Snapshot.content_hash == c_hash,
                Snapshot.deduplicated.is_(False),
                Snapshot.id != canonical_id,
            )
            .all()
        )

        for snap_id, snap_url in dup_snaps:
            candidates.append(
                DedupCandidate(
                    duplicate_id=snap_id,
                    canonical_id=canonical_id,
                    url=snap_url,
                    capture_date=str(cap_day),
                    content_hash=c_hash,
                )
            )

    return candidates


def deduplicate_snapshots(
    session: Session,
    candidates: list[DedupCandidate],
    *,
    dry_run: bool = True,
) -> DedupResult:
    """
    Mark duplicate snapshots as deduplicated.

    Args:
        session: SQLAlchemy session
        candidates: List of DedupCandidate to process
        dry_run: If True (default), only report what would be done

    Returns:
        DedupResult with counts and status
    """
    if dry_run:
        return DedupResult(
            candidates=candidates,
            applied=False,
            deduped_count=len(candidates),
            skipped_count=0,
        )

    now = datetime.now(timezone.utc)
    deduped_count = 0
    skipped_count = 0

    for candidate in candidates:
        snap = session.get(Snapshot, candidate.duplicate_id)
        if snap is None or snap.deduplicated:
            skipped_count += 1
            continue

        snap.deduplicated = True
        session.add(
            SnapshotDeduplication(
                snapshot_id=candidate.duplicate_id,
                canonical_snapshot_id=candidate.canonical_id,
                deduped_at=now,
                reason="same_day_same_hash",
            )
        )
        deduped_count += 1

        if deduped_count % 500 == 0:
            session.flush()
            logger.info("Deduplicated %d snapshots so far...", deduped_count)

    session.flush()
    logger.info(
        "Deduplication complete: %d marked, %d skipped",
        deduped_count,
        skipped_count,
    )

    return DedupResult(
        candidates=candidates,
        applied=True,
        deduped_count=deduped_count,
        skipped_count=skipped_count,
    )


def restore_deduped_snapshots(
    session: Session,
    *,
    job_id: int | None = None,
) -> int:
    """
    Restore previously deduplicated snapshots by clearing the deduplicated flag.

    Args:
        session: SQLAlchemy session
        job_id: If provided, only restore snapshots from this job

    Returns:
        Number of snapshots restored
    """
    query = session.query(Snapshot).filter(Snapshot.deduplicated.is_(True))
    if job_id is not None:
        query = query.filter(Snapshot.job_id == job_id)

    restored = 0
    for snap in query.all():
        snap.deduplicated = False
        restored += 1

        # Remove audit records
        audit_query = session.query(SnapshotDeduplication).filter(
            SnapshotDeduplication.snapshot_id == snap.id
        )
        audit_query.delete(synchronize_session="fetch")

    session.flush()
    logger.info("Restored %d deduplicated snapshots", restored)
    return restored
