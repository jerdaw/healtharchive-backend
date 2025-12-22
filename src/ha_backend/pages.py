from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Sequence, cast

from sqlalchemy import and_, case, func, or_
from sqlalchemy.orm import Session

from ha_backend.models import Page, Snapshot


@dataclass(frozen=True)
class PagesRebuildResult:
    upserted_groups: int
    deleted_groups: int


def _strip_query_fragment_expr(url_expr: Any, dialect_name: str) -> Any:
    if dialect_name == "postgresql":
        return func.regexp_replace(url_expr, r"[?#].*$", "")
    if dialect_name == "sqlite":
        q_pos = func.instr(url_expr, "?")
        hash_pos = func.instr(url_expr, "#")
        cut_pos = case(
            (and_(q_pos > 0, hash_pos > 0), func.min(q_pos, hash_pos)),
            (q_pos > 0, q_pos),
            (hash_pos > 0, hash_pos),
            else_=0,
        )
        return case(
            (cut_pos > 0, func.substr(url_expr, 1, cut_pos - 1)),
            else_=url_expr,
        )
    return url_expr


def build_snapshot_page_group_key(*, dialect_name: str) -> Any:
    """
    Return a SQLAlchemy expression for the canonical page grouping key used for
    view=pages, matching /api/search behavior.
    """
    return func.coalesce(
        Snapshot.normalized_url_group,
        _strip_query_fragment_expr(Snapshot.url, dialect_name),
    )


def _dialect_insert(session: Session):
    dialect = session.get_bind().dialect.name
    if dialect == "postgresql":
        from sqlalchemy.dialects.postgresql import insert as pg_insert

        return pg_insert
    if dialect == "sqlite":
        from sqlalchemy.dialects.sqlite import insert as sqlite_insert

        return sqlite_insert
    from sqlalchemy import insert as generic_insert

    return generic_insert


def rebuild_pages(
    session: Session,
    *,
    source_id: int | None = None,
    job_id: int | None = None,
    groups: Sequence[str] | None = None,
    delete_missing: bool = False,
) -> PagesRebuildResult:
    """
    Rebuild (upsert) Page rows from Snapshot rows.

    This is metadata-only and never touches WARC content. It can be run:
    - globally (no filters),
    - per source,
    - per job,
    - or for a specific set of page group keys.
    """
    dialect_name = session.get_bind().dialect.name

    groups_list = [g for g in groups if g] if groups is not None else None
    if groups_list is not None:
        # Avoid exceeding SQLite's default parameter limit (often 999), and keep
        # Postgres queries from growing enormous when jobs contain many page
        # groups.
        if dialect_name == "sqlite":
            chunk_size = 500
        else:
            chunk_size = 20000

        if len(groups_list) > chunk_size:
            total_upserted = 0
            total_deleted = 0
            for i in range(0, len(groups_list), chunk_size):
                chunk = groups_list[i : i + chunk_size]
                chunk_result = rebuild_pages(
                    session,
                    source_id=source_id,
                    job_id=job_id,
                    groups=chunk,
                    delete_missing=delete_missing,
                )
                total_upserted += chunk_result.upserted_groups
                total_deleted += chunk_result.deleted_groups

            return PagesRebuildResult(
                upserted_groups=total_upserted,
                deleted_groups=total_deleted,
            )

    group_key = build_snapshot_page_group_key(dialect_name=dialect_name)

    filters: list[Any] = [Snapshot.source_id.isnot(None)]
    if source_id is not None:
        filters.append(Snapshot.source_id == source_id)
    if job_id is not None:
        filters.append(Snapshot.job_id == job_id)
    if groups_list is not None:
        filters.append(group_key.in_(groups_list))

    ok_filter = or_(
        Snapshot.status_code.is_(None),
        and_(Snapshot.status_code >= 200, Snapshot.status_code < 300),
    )

    agg_subq = (
        session.query(
            Snapshot.source_id.label("source_id"),
            group_key.label("group_key"),
            func.min(Snapshot.capture_timestamp).label("first_capture_timestamp"),
            func.max(Snapshot.capture_timestamp).label("last_capture_timestamp"),
            func.count(Snapshot.id).label("snapshot_count"),
        )
        .filter(*filters)
        .group_by(Snapshot.source_id, group_key)
        .subquery()
    )

    latest_rn = (
        func.row_number()
        .over(
            partition_by=(Snapshot.source_id, group_key),
            order_by=(Snapshot.capture_timestamp.desc(), Snapshot.id.desc()),
        )
        .label("rn")
    )
    latest_subq = (
        session.query(
            Snapshot.source_id.label("source_id"),
            group_key.label("group_key"),
            Snapshot.id.label("snapshot_id"),
            latest_rn,
        )
        .filter(*filters)
        .subquery()
    )
    latest_one_subq = (
        session.query(
            latest_subq.c.source_id.label("source_id"),
            latest_subq.c.group_key.label("group_key"),
            latest_subq.c.snapshot_id.label("latest_snapshot_id"),
        )
        .filter(latest_subq.c.rn == 1)
        .subquery()
    )

    latest_ok_rn = (
        func.row_number()
        .over(
            partition_by=(Snapshot.source_id, group_key),
            order_by=(Snapshot.capture_timestamp.desc(), Snapshot.id.desc()),
        )
        .label("rn")
    )
    latest_ok_subq = (
        session.query(
            Snapshot.source_id.label("source_id"),
            group_key.label("group_key"),
            Snapshot.id.label("snapshot_id"),
            latest_ok_rn,
        )
        .filter(*filters)
        .filter(ok_filter)
        .subquery()
    )
    latest_ok_one_subq = (
        session.query(
            latest_ok_subq.c.source_id.label("source_id"),
            latest_ok_subq.c.group_key.label("group_key"),
            latest_ok_subq.c.snapshot_id.label("latest_ok_snapshot_id"),
        )
        .filter(latest_ok_subq.c.rn == 1)
        .subquery()
    )

    select_stmt = (
        session.query(
            agg_subq.c.source_id.label("source_id"),
            agg_subq.c.group_key.label("normalized_url_group"),
            agg_subq.c.first_capture_timestamp,
            agg_subq.c.last_capture_timestamp,
            agg_subq.c.snapshot_count,
            latest_one_subq.c.latest_snapshot_id,
            latest_ok_one_subq.c.latest_ok_snapshot_id,
        )
        .join(
            latest_one_subq,
            and_(
                latest_one_subq.c.source_id == agg_subq.c.source_id,
                latest_one_subq.c.group_key == agg_subq.c.group_key,
            ),
        )
        .outerjoin(
            latest_ok_one_subq,
            and_(
                latest_ok_one_subq.c.source_id == agg_subq.c.source_id,
                latest_ok_one_subq.c.group_key == agg_subq.c.group_key,
            ),
        )
    )

    insert_cols = [
        "source_id",
        "normalized_url_group",
        "first_capture_timestamp",
        "last_capture_timestamp",
        "snapshot_count",
        "latest_snapshot_id",
        "latest_ok_snapshot_id",
    ]

    dialect_insert = _dialect_insert(session)
    insert_stmt = dialect_insert(Page.__table__).from_select(insert_cols, select_stmt)

    # ON CONFLICT works for Postgres and SQLite. For other dialects (unsupported
    # in prod), we fall back to best-effort inserts.
    upserted_groups = 0
    if dialect_name in {"postgresql", "sqlite"}:
        upsert_stmt = insert_stmt.on_conflict_do_update(
            index_elements=["source_id", "normalized_url_group"],
            set_={
                "first_capture_timestamp": insert_stmt.excluded.first_capture_timestamp,
                "last_capture_timestamp": insert_stmt.excluded.last_capture_timestamp,
                "snapshot_count": insert_stmt.excluded.snapshot_count,
                "latest_snapshot_id": insert_stmt.excluded.latest_snapshot_id,
                "latest_ok_snapshot_id": insert_stmt.excluded.latest_ok_snapshot_id,
                "updated_at": func.now(),
            },
        )
        exec_result = cast(Any, session.execute(upsert_stmt))
        upserted_groups = int(exec_result.rowcount or 0)
    else:
        exec_result = cast(Any, session.execute(insert_stmt))
        upserted_groups = int(exec_result.rowcount or 0)

    deleted_groups = 0
    if delete_missing and source_id is not None:
        present_subq = session.query(agg_subq.c.group_key).subquery()
        delete_query = session.query(Page).filter(Page.source_id == source_id)
        if groups_list is not None:
            delete_query = delete_query.filter(Page.normalized_url_group.in_(groups_list))

        deleted_groups = int(
            delete_query.filter(
                ~Page.normalized_url_group.in_(session.query(present_subq.c.group_key))
            ).delete(synchronize_session=False)
            or 0
        )

    return PagesRebuildResult(
        upserted_groups=upserted_groups,
        deleted_groups=deleted_groups,
    )


def discover_job_page_groups(session: Session, *, job_id: int) -> list[str]:
    """
    Return distinct page group keys for snapshots belonging to a given job.
    """
    dialect_name = session.get_bind().dialect.name
    group_key = build_snapshot_page_group_key(dialect_name=dialect_name)
    rows: Iterable[tuple[str]] = (
        session.query(group_key)
        .filter(Snapshot.job_id == job_id)
        .filter(group_key.isnot(None))
        .distinct()
        .all()
    )
    return [g for (g,) in rows if g]
