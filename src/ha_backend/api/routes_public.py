from __future__ import annotations

import time
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy import and_, case, func, inspect, or_
from sqlalchemy.orm import Session, joinedload, load_only
from threading import Lock

from ha_backend.db import get_session
from ha_backend.indexing.viewer import find_record_for_snapshot
from ha_backend.models import ArchiveJob, PageSignal, Snapshot, SnapshotOutlink, Source, Topic
from ha_backend.search import TS_CONFIG, build_search_vector
from ha_backend.search_ranking import (
    QueryMode,
    RankingVersion,
    classify_query_mode,
    get_ranking_config,
    get_ranking_version,
    tokenize_query,
)
from ha_backend.runtime_metrics import observe_search_request

from .schemas import (
    ArchiveStatsSchema,
    SearchResponseSchema,
    SnapshotDetailSchema,
    SnapshotSummarySchema,
    SourceSummarySchema,
    TopicRefSchema,
)

router = APIRouter()

_TABLE_EXISTS_CACHE: dict[tuple[int, str], bool] = {}
_TABLE_EXISTS_LOCK = Lock()

_COLUMN_EXISTS_CACHE: dict[tuple[int, str, str], bool] = {}
_COLUMN_EXISTS_LOCK = Lock()


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


def _has_column(db: Session, table_name: str, column_name: str) -> bool:
    bind = db.get_bind()
    cache_key = (id(bind), table_name, column_name)
    with _COLUMN_EXISTS_LOCK:
        cached = _COLUMN_EXISTS_CACHE.get(cache_key)
        if cached is not None:
            return cached

    try:
        cols = inspect(bind).get_columns(table_name)
        exists = any(c.get("name") == column_name for c in cols)
    except Exception:
        exists = False

    with _COLUMN_EXISTS_LOCK:
        _COLUMN_EXISTS_CACHE[cache_key] = exists
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
    ranking: str | None,
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

    ranking_version = get_ranking_version(ranking)
    # For v2 we use different blends depending on query "intent".
    query_mode = None
    query_tokens: list[str] = []
    ranking_cfg = None
    if ranking_version == RankingVersion.v2 and q_clean:
        query_mode = classify_query_mode(q_clean)
        ranking_cfg = get_ranking_config(mode=query_mode)
        query_tokens = tokenize_query(q_clean)

    mode = "newest"
    if effective_sort == SearchSort.relevance and q_clean:
        mode = "relevance_fts" if use_postgres_fts else "relevance_fallback"
        if ranking_version == RankingVersion.v2:
            mode = f"{mode}_v2"

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

    use_page_signals = (
        effective_sort == SearchSort.relevance
        and q_clean is not None
        and _has_table(db, "page_signals")
    )
    use_authority = use_page_signals

    has_outlink_table = _has_table(db, "snapshot_outlinks")
    has_ps_outlink_count = use_page_signals and _has_column(
        db, "page_signals", "outlink_count"
    )
    has_ps_pagerank = use_page_signals and _has_column(db, "page_signals", "pagerank")

    use_hubness = (
        ranking_version == RankingVersion.v2
        and ranking_cfg is not None
        and query_mode == QueryMode.broad
        and effective_sort == SearchSort.relevance
        and q_clean is not None
        and has_outlink_table
    )

    inlink_count = None
    if use_authority:
        inlink_count = func.coalesce(PageSignal.inlink_count, 0)

    use_pagerank = (
        ranking_version == RankingVersion.v2
        and ranking_cfg is not None
        and query_mode == QueryMode.broad
        and effective_sort == SearchSort.relevance
        and q_clean is not None
        and has_ps_pagerank
    )

    outlink_count = None
    if use_hubness:
        correlated_outlinks = (
            db.query(func.count(func.distinct(SnapshotOutlink.to_normalized_url_group)))
            .filter(SnapshotOutlink.snapshot_id == Snapshot.id)
            .filter(SnapshotOutlink.to_normalized_url_group != group_key)
            .correlate(Snapshot)
            .scalar_subquery()
        )
        correlated_outlinks = func.coalesce(correlated_outlinks, 0)

        if has_ps_outlink_count:
            # Use the precomputed per-page outlink_count when available, but fall back
            # to a correlated per-snapshot count if the page_signals row is missing.
            outlink_count = func.coalesce(PageSignal.outlink_count, correlated_outlinks)
        else:
            outlink_count = correlated_outlinks

    pagerank_value = None
    if use_pagerank:
        pagerank_value = func.coalesce(PageSignal.pagerank, 0.0)

    def build_authority_expr() -> Any:
        if inlink_count is None:
            return 0.0
        if ranking_version == RankingVersion.v2 and ranking_cfg is not None:
            # Postgres and SQLite both support ln() inconsistently; keep ln-based
            # authority only for Postgres, and use tiering elsewhere.
            if use_postgres_fts:
                return float(ranking_cfg.authority_coef) * func.ln(inlink_count + 1)
            authority_tier = case(
                (inlink_count >= 100, 3),
                (inlink_count >= 20, 2),
                (inlink_count >= 5, 1),
                else_=0,
            )
            return authority_tier
        # v1 behavior
        if use_postgres_fts:
            return 0.05 * func.ln(inlink_count + 1)
        authority_tier = case(
            (inlink_count >= 100, 3),
            (inlink_count >= 20, 2),
            (inlink_count >= 5, 1),
            else_=0,
        )
        return authority_tier

    def build_hubness_expr() -> Any:
        if outlink_count is None or ranking_cfg is None or not use_hubness:
            return 0.0
        if use_postgres_fts:
            if ranking_cfg.hubness_coef == 0:
                return 0.0
            return float(ranking_cfg.hubness_coef) * func.ln(outlink_count + 1)

        hubness_tier = case(
            (outlink_count >= 100, 3),
            (outlink_count >= 20, 2),
            (outlink_count >= 5, 1),
            else_=0,
        )
        return float(ranking_cfg.hubness_coef) * hubness_tier

    def build_pagerank_expr() -> Any:
        if pagerank_value is None or ranking_cfg is None or not use_pagerank:
            return 0.0
        if ranking_cfg.pagerank_coef == 0:
            return 0.0
        if use_postgres_fts:
            return float(ranking_cfg.pagerank_coef) * func.ln(pagerank_value + 1)
        return float(ranking_cfg.pagerank_coef) * pagerank_value

    def build_depth_penalty(url_expr: Any) -> Any:
        slash_count = func.length(url_expr) - func.length(func.replace(url_expr, "/", ""))
        if ranking_version == RankingVersion.v2 and ranking_cfg is not None:
            return float(ranking_cfg.depth_coef) * slash_count
        return (-0.01) * slash_count

    def build_archived_penalty() -> Any:
        if ranking_version != RankingVersion.v2 or ranking_cfg is None:
            return 0.0
        if ranking_cfg.archived_penalty == 0:
            return 0.0

        # Canada.ca often marks pages as archived via title prefixes *or* a banner
        # in the rendered HTML that ends up in our snippet extraction.
        snippet_text = func.coalesce(Snapshot.snippet, "")
        archived_match = or_(
            Snapshot.title.ilike("archived%"),
            Snapshot.title.ilike("archive %"),
            snippet_text.ilike("%we have archived this page%"),
            snippet_text.ilike("%this page has been archived%"),
            snippet_text.ilike("%nous avons archivé cette page%"),
            snippet_text.ilike("%cette page a été archivée%"),
        )
        return case((archived_match, float(ranking_cfg.archived_penalty)), else_=0.0)

    def build_title_boost() -> Any:
        if not q_clean:
            return 0.0
        if ranking_version != RankingVersion.v2 or not query_tokens or ranking_cfg is None:
            return case(
                (Snapshot.title.ilike(f"%{q_clean}%"), 0.2),
                else_=0.0,
            )
        token_match_exprs = [Snapshot.title.ilike(f"%{t}%") for t in query_tokens]
        any_match = or_(*token_match_exprs)
        all_match = and_(*token_match_exprs) if len(token_match_exprs) > 1 else any_match
        return case(
            (all_match, float(ranking_cfg.title_all_tokens_boost)),
            (any_match, float(ranking_cfg.title_any_token_boost)),
            else_=0.0,
        )

    def build_querystring_penalty(url_expr: Any) -> Any:
        return case(
            (url_expr.like("%?%"), -0.1),
            else_=0.0,
        )

    def build_tracking_penalty(url_expr: Any) -> Any:
        return case(
            (
                or_(
                    url_expr.ilike("%utm_%"),
                    url_expr.ilike("%gclid=%"),
                    url_expr.ilike("%fbclid=%"),
                ),
                -0.1,
            ),
            else_=0.0,
        )

    def build_snapshot_score() -> Any:
        if effective_sort != SearchSort.relevance or not q_clean:
            return None
        if use_postgres_fts and tsquery is not None and vector_expr is not None:
            if (
                ranking_version == RankingVersion.v2
                and query_mode is not None
                and query_mode != QueryMode.specific
            ):
                rank = func.ts_rank_cd(vector_expr, tsquery, 32)
            else:
                rank = func.ts_rank_cd(vector_expr, tsquery)
            depth_basis = (
                group_key
                if (ranking_version == RankingVersion.v2 and ranking_cfg is not None)
                else Snapshot.url
            )
            url_penalty_basis = (
                group_key
                if (
                    ranking_version == RankingVersion.v2
                    and ranking_cfg is not None
                    and effective_view == SearchView.pages
                )
                else Snapshot.url
            )
            depth_penalty = build_depth_penalty(depth_basis)
            score = (
                rank
                + build_title_boost()
                + build_archived_penalty()
                + build_querystring_penalty(url_penalty_basis)
                + build_tracking_penalty(url_penalty_basis)
                + depth_penalty
            )
            if use_authority and inlink_count is not None:
                score = score + build_authority_expr()
            if use_hubness and outlink_count is not None:
                score = score + build_hubness_expr()
            if use_pagerank and pagerank_value is not None:
                score = score + build_pagerank_expr()
            return score

        # DB-agnostic fallback: score by field match presence.
        ilike_pattern = f"%{q_clean}%"
        title_match_score = case((Snapshot.title.ilike(ilike_pattern), 5), else_=0)
        url_match_score = case((Snapshot.url.ilike(ilike_pattern), 2), else_=0)
        snippet_match_score = case((Snapshot.snippet.ilike(ilike_pattern), 1), else_=0)
        score = title_match_score + url_match_score + snippet_match_score

        if ranking_version == RankingVersion.v2 and ranking_cfg is not None:
            score = score + build_archived_penalty() + build_depth_penalty(group_key)
            if use_authority and inlink_count is not None:
                score = score + build_authority_expr()
            if use_hubness and outlink_count is not None:
                score = score + build_hubness_expr()
            if use_pagerank and pagerank_value is not None:
                score = score + build_pagerank_expr()
        else:
            if use_authority and inlink_count is not None:
                score = score + build_authority_expr()
        return score

    snapshot_score = build_snapshot_score()

    def build_item_query_for_pages_v1() -> Any:
        row_number = func.row_number().over(
            partition_by=group_key,
            order_by=(Snapshot.capture_timestamp.desc(), Snapshot.id.desc()),
        ).label("rn")
        latest_ids_subq = query.with_entities(
            Snapshot.id.label("id"),
            row_number,
        ).subquery()
        return (
            db.query(Snapshot)
            .join(latest_ids_subq, Snapshot.id == latest_ids_subq.c.id)
            .filter(latest_ids_subq.c.rn == 1)
        )

    def build_item_query_for_pages_v2() -> Any:
        if snapshot_score is None:
            return build_item_query_for_pages_v1()

        row_number = func.row_number().over(
            partition_by=group_key,
            order_by=(Snapshot.capture_timestamp.desc(), Snapshot.id.desc()),
        ).label("rn")
        candidates_query = query
        if use_page_signals:
            candidates_query = candidates_query.outerjoin(
                PageSignal, PageSignal.normalized_url_group == group_key
            )

        candidates_subq = (
            candidates_query
            .with_entities(
                Snapshot.id.label("id"),
                group_key.label("group_key"),
                Snapshot.capture_timestamp.label("capture_timestamp"),
                row_number,
                snapshot_score.label("snapshot_score"),
            )
            .subquery()
        )

        group_scores_subq = (
            db.query(
                candidates_subq.c.group_key.label("group_key"),
                func.max(candidates_subq.c.snapshot_score).label("group_score"),
            )
            .group_by(candidates_subq.c.group_key)
            .subquery()
        )

        latest_ids_subq = (
            db.query(
                candidates_subq.c.id.label("id"),
                candidates_subq.c.group_key.label("group_key"),
                candidates_subq.c.rn.label("rn"),
            )
            .subquery()
        )

        return (
            db.query(Snapshot)
            .join(latest_ids_subq, Snapshot.id == latest_ids_subq.c.id)
            .join(group_scores_subq, group_scores_subq.c.group_key == latest_ids_subq.c.group_key)
            .filter(latest_ids_subq.c.rn == 1)
            .order_by(
                status_quality.desc(),
                group_scores_subq.c.group_score.desc(),
                Snapshot.capture_timestamp.desc(),
                Snapshot.id.desc(),
            )
        )

    ordered = query
    if effective_view == SearchView.pages:
        if (
            ranking_version == RankingVersion.v2
            and effective_sort == SearchSort.relevance
            and q_clean
        ):
            ordered = build_item_query_for_pages_v2()
        else:
            item_query = build_item_query_for_pages_v1()
            if use_page_signals:
                item_query = item_query.outerjoin(
                    PageSignal, PageSignal.normalized_url_group == group_key
                )

            if effective_sort == SearchSort.relevance and q_clean:
                rank_score = snapshot_score if snapshot_score is not None else 0.0
                ordered = item_query.order_by(
                    status_quality.desc(),
                    rank_score.desc(),
                    Snapshot.capture_timestamp.desc(),
                    Snapshot.id.desc(),
                )
            else:
                ordered = item_query.order_by(
                    status_quality.desc(),
                    Snapshot.capture_timestamp.desc(),
                    Snapshot.id.desc(),
                )
    else:
        item_query = query
        if use_page_signals:
            item_query = item_query.outerjoin(
                PageSignal, PageSignal.normalized_url_group == group_key
            )
        if effective_sort == SearchSort.relevance and q_clean:
            rank_score = snapshot_score if snapshot_score is not None else 0.0
            ordered = item_query.order_by(
                status_quality.desc(),
                rank_score.desc(),
                Snapshot.capture_timestamp.desc(),
                Snapshot.id.desc(),
            )
        else:
            ordered = item_query.order_by(
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
                originalUrl=(
                    snap.normalized_url_group
                    if (effective_view == SearchView.pages and snap.normalized_url_group)
                    else snap.url
                ),
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


@router.get("/stats", response_model=ArchiveStatsSchema)
def get_archive_stats(response: Response, db: Session = Depends(get_db)) -> ArchiveStatsSchema:
    """
    Public archive stats used by the frontend (homepage snapshot metrics).

    Keep this lightweight and cacheable; it should not leak admin-only details.
    """

    # 5 minutes on shared caches; short max-age for clients.
    response.headers["Cache-Control"] = "public, max-age=60, s-maxage=300"

    snapshots_total = int(db.query(func.count(Snapshot.id)).scalar() or 0)

    pages_total = int(
        db.query(
            func.count(
                func.distinct(func.coalesce(Snapshot.normalized_url_group, Snapshot.url))
            )
        ).scalar()
        or 0
    )

    sources_total = int(
        db.query(func.count(func.distinct(Snapshot.source_id)))
        .filter(Snapshot.source_id.isnot(None))
        .scalar()
        or 0
    )

    latest_capture_ts = db.query(func.max(Snapshot.capture_timestamp)).scalar()
    latest_capture_date = (
        latest_capture_ts.date().isoformat() if latest_capture_ts else None
    )

    return ArchiveStatsSchema(
        snapshotsTotal=snapshots_total,
        pagesTotal=pages_total,
        sourcesTotal=sources_total,
        latestCaptureDate=latest_capture_date,
    )


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
    ranking: Optional[str] = Query(
        default=None,
        description="Ranking version override (v1|v2). Default is controlled by HA_SEARCH_RANKING_VERSION.",
        pattern=r"^(v1|v2)$",
    ),
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
            ranking=ranking,
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
