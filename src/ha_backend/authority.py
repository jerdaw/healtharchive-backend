from __future__ import annotations

import logging
from collections.abc import Sequence

from sqlalchemy import func
from sqlalchemy.orm import Session

from ha_backend.models import PageSignal, Snapshot, SnapshotOutlink

logger = logging.getLogger("healtharchive.authority")


def recompute_page_signals(
    session: Session,
    *,
    groups: Sequence[str] | None = None,
) -> int:
    """
    Recompute PageSignal.inlink_count values.

    If groups is None, rebuilds the entire table (deletes existing rows).
    If groups is provided, only updates those groups (and removes rows that no
    longer have any inlinks).

    Returns the number of PageSignal rows inserted/updated/deleted.
    """
    normalized_groups = None
    if groups is not None:
        normalized_groups = sorted({g for g in groups if g})
        if not normalized_groups:
            return 0

    from_group = func.coalesce(Snapshot.normalized_url_group, Snapshot.url)

    count_query = (
        session.query(
            SnapshotOutlink.to_normalized_url_group.label("group"),
            func.count(func.distinct(from_group)).label("inlinks"),
        )
        .join(Snapshot, Snapshot.id == SnapshotOutlink.snapshot_id)
        .filter(SnapshotOutlink.to_normalized_url_group != from_group)
        .group_by(SnapshotOutlink.to_normalized_url_group)
    )
    if normalized_groups is not None:
        count_query = count_query.filter(
            SnapshotOutlink.to_normalized_url_group.in_(normalized_groups)
        )

    counts = {group: int(inlinks or 0) for group, inlinks in count_query.all()}

    if normalized_groups is None:
        deleted = session.query(PageSignal).delete(synchronize_session=False) or 0
        inserted = 0

        rows = [
            PageSignal(normalized_url_group=group, inlink_count=inlinks)
            for group, inlinks in counts.items()
            if inlinks > 0
        ]
        if rows:
            session.add_all(rows)
            inserted = len(rows)

        return int(deleted) + inserted

    existing = (
        session.query(PageSignal)
        .filter(PageSignal.normalized_url_group.in_(normalized_groups))
        .all()
    )
    existing_by_group = {row.normalized_url_group: row for row in existing}

    touched = 0
    for group in normalized_groups:
        inlinks = counts.get(group, 0)
        existing_row = existing_by_group.get(group)

        if inlinks <= 0:
            if existing_row is not None:
                session.delete(existing_row)
                touched += 1
            continue

        if existing_row is None:
            session.add(PageSignal(normalized_url_group=group, inlink_count=inlinks))
            touched += 1
        else:
            existing_row.inlink_count = inlinks
            touched += 1

    return touched


__all__ = ["recompute_page_signals"]
