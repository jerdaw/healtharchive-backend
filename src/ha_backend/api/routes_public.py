from __future__ import annotations

import time
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy import and_, case, func, inspect, or_
from sqlalchemy.orm import Session, joinedload, load_only
from threading import Lock

from ha_backend.db import get_session
from ha_backend.indexing.viewer import find_record_for_snapshot
from ha_backend.models import ArchiveJob, PageSignal, Snapshot, Source, Topic
from ha_backend.search import TS_CONFIG, build_search_vector
from ha_backend.runtime_metrics import observe_search_request

from .schemas import (
    SearchResponseSchema,
    SnapshotDetailSchema,
    SnapshotSummarySchema,
    SourceSummarySchema,
    TopicRefSchema,
)

router = APIRouter()

_TABLE_EXISTS_CACHE: dict[tuple[int, str], bool] = {}
_TABLE_EXISTS_LOCK = Lock()


def _has_table(db: Session, table_name: str) -> bool:
    bind = db.get_bind()
    cache_key = (id(bind), table_name)
    with _TABLE_EXISTS_LOCK:
        cached = _TABLE_EXISTS_CACHE.get(cache_key)
        if cached is not None:
            return cached

    try:
        exists = inspect(bind).has_table(table_name)
    except Exception:
        exists = False

    with _TABLE_EXISTS_LOCK:
        _TABLE_EXISTS_CACHE[cache_key] = exists
    return exists


class SearchSort(str, Enum):
    relevance = "relevance"
    newest = "newest"


class SearchView(str, Enum):
    snapshots = "snapshots"
    pages = "pages"


def _search_snapshots_inner(
    *,
    q: str | None,
    source: str | None,
    topic: str | None,
    sort: SearchSort | None,
    view: SearchView | None,
    includeNon2xx: bool,
    page: int,
    pageSize: int,
    db: Session,
) -> tuple[SearchResponseSchema, str]:
    """
    Implementation for the /api/search route.

    Returns:
        (response, mode) where mode is one of:
        - "newest"
        - "relevance_fts"
        - "relevance_fallback"
    """
    q_clean = q.strip() if q else None
    if q_clean == "":
        q_clean = None

    effective_sort = sort
    if effective_sort is None:
        effective_sort = SearchSort.relevance if q_clean else SearchSort.newest
    if effective_sort == SearchSort.relevance and not q_clean:
        effective_sort = SearchSort.newest

    effective_view = view or SearchView.snapshots

    dialect_name = db.get_bind().dialect.name
    use_postgres_fts = dialect_name == "postgresql"

    mode = "newest"
    if effective_sort == SearchSort.relevance and q_clean:
        mode = "relevance_fts" if use_postgres_fts else "relevance_fallback"

    query = db.query(Snapshot).join(Source)

    if source:
        query = query.filter(Source.code == source.lower())

    if topic:
        # Join the topics association once and filter by topic slug.
        query = query.join(Snapshot.topics).filter(Topic.slug == topic)

    if not includeNon2xx:
        query = query.filter(
            or_(
                Snapshot.status_code.is_(None),
                and_(
                    Snapshot.status_code >= 200,
                    Snapshot.status_code < 300,
                ),
            )
        )

    tsquery = None
    vector_expr = None

    if q_clean:
        if use_postgres_fts and effective_sort == SearchSort.relevance:
            # Postgres full-text search path (preferred in production).
            #
            # We store a tsvector in Snapshot.search_vector, but we also fall
            # back to computing a vector on-the-fly for any rows that have not
            # yet been backfilled.
            tsquery = func.websearch_to_tsquery(TS_CONFIG, q_clean)
            vector_expr = func.coalesce(
                Snapshot.search_vector,
                build_search_vector(Snapshot.title, Snapshot.snippet, Snapshot.url),
            )
            query = query.filter(vector_expr.op("@@")(tsquery))
        else:
            # DB-agnostic fallback: substring match across title/snippet/url.
            ilike_pattern = f"%{q_clean}%"
            query = query.filter(
                or_(
                    Snapshot.title.ilike(ilike_pattern),
                    Snapshot.snippet.ilike(ilike_pattern),
                    Snapshot.url.ilike(ilike_pattern),
                )
            )

    group_key = func.coalesce(Snapshot.normalized_url_group, Snapshot.url)
    if effective_view == SearchView.pages:
        total = query.with_entities(func.count(func.distinct(group_key))).scalar() or 0
    else:
        total = query.with_entities(func.count(Snapshot.id)).scalar() or 0

    offset = (page - 1) * pageSize

    status_quality = case(
        (Snapshot.status_code.is_(None), 0),
        (and_(Snapshot.status_code >= 200, Snapshot.status_code < 300), 2),
        (and_(Snapshot.status_code >= 300, Snapshot.status_code < 400), 1),
        else_=-1,
    )

    item_query = query
    if effective_view == SearchView.pages:
        row_number = func.row_number().over(
            partition_by=group_key,
            order_by=(Snapshot.capture_timestamp.desc(), Snapshot.id.desc()),
        ).label("rn")
        latest_ids_subq = query.with_entities(
            Snapshot.id.label("id"),
            row_number,
        ).subquery()

        item_query = (
            db.query(Snapshot)
            .join(latest_ids_subq, Snapshot.id == latest_ids_subq.c.id)
            .filter(latest_ids_subq.c.rn == 1)
        )

    use_authority = (
        effective_sort == SearchSort.relevance
        and q_clean is not None
        and _has_table(db, "page_signals")
    )

    inlink_count = None
    if use_authority:
        item_query = item_query.outerjoin(
            PageSignal, PageSignal.normalized_url_group == group_key
        )
        inlink_count = func.coalesce(PageSignal.inlink_count, 0)

    ordered = item_query
    if effective_sort == SearchSort.relevance and q_clean:
        if use_postgres_fts and tsquery is not None and vector_expr is not None:
            rank = func.ts_rank_cd(vector_expr, tsquery)

            title_phrase_boost = case(
                (Snapshot.title.ilike(f"%{q_clean}%"), 0.2),
                else_=0.0,
            )
            querystring_penalty = case(
                (Snapshot.url.like("%?%"), -0.1),
                else_=0.0,
            )
            tracking_penalty = case(
                (
                    or_(
                        Snapshot.url.ilike("%utm_%"),
                        Snapshot.url.ilike("%gclid=%"),
                        Snapshot.url.ilike("%fbclid=%"),
                    ),
                    -0.1,
                ),
                else_=0.0,
            )
            slash_count = func.length(Snapshot.url) - func.length(
                func.replace(Snapshot.url, "/", "")
            )
            depth_penalty = (-0.01) * slash_count

            authority_boost = 0.0
            if inlink_count is not None:
                authority_boost = 0.05 * func.ln(inlink_count + 1)

            rank_score = (
                rank
                + title_phrase_boost
                + querystring_penalty
                + tracking_penalty
                + depth_penalty
                + authority_boost
            )
            ordered = ordered.order_by(
                status_quality.desc(),
                rank_score.desc(),
                Snapshot.capture_timestamp.desc(),
                Snapshot.id.desc(),
            )
        else:
            ilike_pattern = f"%{q_clean}%"
            title_match_score = case((Snapshot.title.ilike(ilike_pattern), 5), else_=0)
            url_match_score = case((Snapshot.url.ilike(ilike_pattern), 2), else_=0)
            snippet_match_score = case(
                (Snapshot.snippet.ilike(ilike_pattern), 1), else_=0
            )
            match_score = title_match_score + url_match_score + snippet_match_score
            if inlink_count is not None:
                authority_tier = case(
                    (inlink_count >= 100, 3),
                    (inlink_count >= 20, 2),
                    (inlink_count >= 5, 1),
                    else_=0,
                )
                ordered = ordered.order_by(
                    status_quality.desc(),
                    match_score.desc(),
                    authority_tier.desc(),
                    Snapshot.capture_timestamp.desc(),
                    Snapshot.id.desc(),
                )
            else:
                ordered = ordered.order_by(
                    status_quality.desc(),
                    match_score.desc(),
                    Snapshot.capture_timestamp.desc(),
                    Snapshot.id.desc(),
                )
    else:
        ordered = ordered.order_by(
            status_quality.desc(),
            Snapshot.capture_timestamp.desc(),
            Snapshot.id.desc(),
        )

    items = (
        ordered.options(
            load_only(
                Snapshot.id,
                Snapshot.url,
                Snapshot.normalized_url_group,
                Snapshot.capture_timestamp,
                Snapshot.mime_type,
                Snapshot.status_code,
                Snapshot.title,
                Snapshot.snippet,
                Snapshot.language,
                Snapshot.warc_path,
                Snapshot.warc_record_id,
            ),
            joinedload(Snapshot.source),
            joinedload(Snapshot.topics),
        )
        .offset(offset)
        .limit(pageSize)
        .all()
    )

    results: List[SnapshotSummarySchema] = []

    for snap in items:
        source_obj = snap.source
        if source_obj is None:
            continue

        capture_date = (
            snap.capture_timestamp.date().isoformat()
            if isinstance(snap.capture_timestamp, datetime)
            else str(snap.capture_timestamp)
        )

        topic_refs = [
            TopicRefSchema(slug=t.slug, label=t.label)
            for t in (snap.topics or [])
        ]

        results.append(
            SnapshotSummarySchema(
                id=snap.id,
                title=snap.title,
                sourceCode=source_obj.code,
                sourceName=source_obj.name,
                language=snap.language,
                topics=topic_refs,
                captureDate=capture_date,
                originalUrl=snap.url,
                snippet=snap.snippet,
                rawSnapshotUrl=f"/api/snapshots/raw/{snap.id}",
            )
        )

    return (
        SearchResponseSchema(
            results=results,
            total=total,
            page=page,
            pageSize=pageSize,
        ),
        mode,
    )


def get_db() -> Session:
    """
    FastAPI dependency that yields a DB session.
    """
    with get_session() as session:
        yield session


@router.get("/health")
def health_check(db: Session = Depends(get_db)) -> JSONResponse:
    """
    Health endpoint with basic database and summary checks.
    """
    checks: Dict[str, Any] = {}
    status = "ok"

    # Database connectivity check
    try:
        # Lightweight query just to exercise the connection.
        db.query(Source.id).limit(1).first()
        checks["db"] = "ok"
    except Exception:
        checks["db"] = "error"
        status = "error"
        return JSONResponse(
            status_code=500,
            content={"status": status, "checks": checks},
        )

    # Job status counts
    job_rows = (
        db.query(ArchiveJob.status, func.count(ArchiveJob.id))
        .group_by(ArchiveJob.status)
        .all()
    )
    checks["jobs"] = {job_status: count for job_status, count in job_rows}

    # Snapshot totals
    total_snapshots = db.query(func.count(Snapshot.id)).scalar() or 0
    checks["snapshots"] = {"total": int(total_snapshots)}

    return JSONResponse(content={"status": status, "checks": checks})


@router.head("/health")
def health_check_head(db: Session = Depends(get_db)) -> Response:
    """
    HEAD variant of the health endpoint.

    Some external uptime monitors issue HEAD requests by default; this route
    mirrors the GET health check status code without returning a body.
    """
    resp = health_check(db=db)
    return Response(status_code=resp.status_code, media_type="application/json")


@router.get("/sources", response_model=List[SourceSummarySchema])
def list_sources(db: Session = Depends(get_db)) -> List[SourceSummarySchema]:
    """
    Return per-source summary statistics derived from Snapshot data.
    """
    snapshot_agg = (
        db.query(
            Snapshot.source_id.label("source_id"),
            func.count(Snapshot.id).label("record_count"),
            func.min(Snapshot.capture_timestamp).label("first_capture"),
            func.max(Snapshot.capture_timestamp).label("last_capture"),
        )
        .group_by(Snapshot.source_id)
        .subquery()
    )

    rows = (
        db.query(
            Source,
            snapshot_agg.c.record_count,
            snapshot_agg.c.first_capture,
            snapshot_agg.c.last_capture,
        )
        .join(snapshot_agg, snapshot_agg.c.source_id == Source.id)
        .order_by(Source.name)
        .all()
    )

    summaries: List[SourceSummarySchema] = []

    for source, record_count, first_capture, last_capture in rows:
        # Latest record id for this source
        latest_snapshot = (
            db.query(Snapshot.id)
            .filter(Snapshot.source_id == source.id)
            .order_by(Snapshot.capture_timestamp.desc(), Snapshot.id.desc())
            .first()
        )
        latest_record_id: Optional[int] = (
            latest_snapshot[0] if latest_snapshot else None
        )

        # Distinct topics (slug + label, if any)
        topic_rows = (
            db.query(Topic.slug, Topic.label)
            .join(Topic.snapshots)
            .filter(Snapshot.source_id == source.id)
            .distinct()
            .order_by(Topic.label)
            .all()
        )
        topics = [
            TopicRefSchema(slug=slug, label=label) for (slug, label) in topic_rows
        ]

        summaries.append(
            SourceSummarySchema(
                sourceCode=source.code,
                sourceName=source.name,
                recordCount=record_count or 0,
                firstCapture=(
                    first_capture.date().isoformat()
                    if isinstance(first_capture, datetime)
                    else str(first_capture)
                ),
                lastCapture=(
                    last_capture.date().isoformat()
                    if isinstance(last_capture, datetime)
                    else str(last_capture)
                ),
                topics=topics,
                latestRecordId=latest_record_id,
            )
        )

    return summaries


@router.get("/topics", response_model=List[TopicRefSchema])
def list_topics(db: Session = Depends(get_db)) -> List[TopicRefSchema]:
    """
    Return the canonical list of topics (slug + label), sorted by label.
    """
    topics = (
        db.query(Topic)
        .order_by(Topic.label)
        .all()
    )
    return [TopicRefSchema(slug=t.slug, label=t.label) for t in topics]


@router.get("/search", response_model=SearchResponseSchema)
def search_snapshots(
    q: Optional[str] = Query(default=None, min_length=1, max_length=256),
    source: Optional[str] = Query(
        default=None, min_length=1, max_length=16, pattern=r"^[a-z0-9-]+$"
    ),
    topic: Optional[str] = Query(
        default=None, min_length=1, max_length=64, pattern=r"^[a-z0-9-]+$"
    ),
    sort: Optional[SearchSort] = Query(default=None),
    view: Optional[SearchView] = Query(default=None),
    includeNon2xx: bool = Query(default=False),
    page: int = Query(default=1, ge=1),
    pageSize: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
) -> SearchResponseSchema:
    """
    Search snapshots by keyword, source, and/or topic with simple pagination.
    """
    start_time = time.perf_counter()
    mode = "newest"

    try:
        response, mode = _search_snapshots_inner(
            q=q,
            source=source,
            topic=topic,
            sort=sort,
            view=view,
            includeNon2xx=includeNon2xx,
            page=page,
            pageSize=pageSize,
            db=db,
        )
    except Exception:
        observe_search_request(
            duration_seconds=time.perf_counter() - start_time,
            mode=mode,
            ok=False,
        )
        raise

    observe_search_request(
        duration_seconds=time.perf_counter() - start_time,
        mode=mode,
        ok=True,
    )
    return response


@router.get("/snapshot/{snapshot_id}", response_model=SnapshotDetailSchema)
def get_snapshot_detail(
    snapshot_id: int,
    db: Session = Depends(get_db),
) -> SnapshotDetailSchema:
    """
    Return metadata for a single snapshot.
    """
    snap = (
        db.query(Snapshot)
        .options(
            load_only(
                Snapshot.id,
                Snapshot.url,
                Snapshot.capture_timestamp,
                Snapshot.mime_type,
                Snapshot.status_code,
                Snapshot.title,
                Snapshot.snippet,
                Snapshot.language,
            ),
            joinedload(Snapshot.source),
            joinedload(Snapshot.topics),
        )
        .filter(Snapshot.id == snapshot_id)
        .first()
    )

    if snap is None or snap.source is None:
        raise HTTPException(status_code=404, detail="Snapshot not found")

    capture_date = (
        snap.capture_timestamp.date().isoformat()
        if isinstance(snap.capture_timestamp, datetime)
        else str(snap.capture_timestamp)
    )

    topic_refs = [
        TopicRefSchema(slug=t.slug, label=t.label)
        for t in (snap.topics or [])
    ]

    return SnapshotDetailSchema(
        id=snap.id,
        title=snap.title,
        sourceCode=snap.source.code,
        sourceName=snap.source.name,
        language=snap.language,
        topics=topic_refs,
        captureDate=capture_date,
        originalUrl=snap.url,
        snippet=snap.snippet,
        rawSnapshotUrl=f"/api/snapshots/raw/{snap.id}",
        mimeType=snap.mime_type,
        statusCode=snap.status_code,
    )


@router.get("/snapshots/raw/{snapshot_id}", response_class=HTMLResponse)
def get_snapshot_raw(
    snapshot_id: int,
    db: Session = Depends(get_db),
) -> HTMLResponse:
    """
    Serve raw HTML content for a snapshot by reading the underlying WARC record.
    """
    snap = (
        db.query(Snapshot)
        .options(
            load_only(
                Snapshot.id,
                Snapshot.url,
                Snapshot.warc_path,
                Snapshot.warc_record_id,
            )
        )
        .filter(Snapshot.id == snapshot_id)
        .first()
    )
    if snap is None:
        raise HTTPException(status_code=404, detail="Snapshot not found")

    if not snap.warc_path:
        raise HTTPException(
            status_code=404, detail="No WARC path associated with this snapshot"
        )

    warc_path = Path(snap.warc_path)
    if not warc_path.is_file():
        raise HTTPException(
            status_code=404,
            detail="Underlying WARC file for this snapshot is missing",
        )

    record = find_record_for_snapshot(snap)
    if record is None:
        raise HTTPException(
            status_code=404,
            detail="Could not locate corresponding record in the WARC file",
        )

    try:
        html = record.body_bytes.decode("utf-8", errors="replace")
    except Exception:
        raise HTTPException(
            status_code=500,
            detail="Failed to decode archived HTML content",
        )

    return HTMLResponse(content=html, media_type="text/html")


__all__ = ["router"]
