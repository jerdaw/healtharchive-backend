from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import Float, Integer, and_, case, cast, func, inspect, literal, or_
from sqlalchemy.orm import Session, joinedload, load_only

from ha_backend.db import get_session
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

from .deps import require_admin
from .schemas_admin import (JobDetailSchema, JobListResponseSchema,
                            JobSnapshotSummarySchema, JobStatusCountsSchema,
                            JobSummarySchema, SearchDebugItemSchema,
                            SearchDebugResponseSchema)
from .routes_public import SearchSort, SearchView

router = APIRouter(
    prefix="/admin",
    tags=["admin"],
    dependencies=[Depends(require_admin)],
)


def get_db() -> Session:
    """
    FastAPI dependency that yields a DB session for admin routes.
    """
    with get_session() as session:
        yield session


@router.get("/jobs", response_model=JobListResponseSchema)
def list_jobs(
    source: Optional[str] = Query(default=None),
    status: Optional[str] = Query(default=None),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
) -> JobListResponseSchema:
    """
    List archive jobs with optional filtering by source code and status.
    """
    query = db.query(ArchiveJob, Source).join(
        Source, ArchiveJob.source_id == Source.id, isouter=True
    )

    if source:
        query = query.filter(Source.code == source.lower())

    if status:
        query = query.filter(ArchiveJob.status == status)

    total = query.count()

    rows = (
        query.order_by(ArchiveJob.created_at.desc()).offset(offset).limit(limit).all()
    )

    items: List[JobSummarySchema] = []
    for job, src in rows:
        src_code = src.code if src is not None else ""
        src_name = src.name if src is not None else ""

        items.append(
            JobSummarySchema(
                id=job.id,
                sourceCode=src_code,
                sourceName=src_name,
                name=job.name,
                status=job.status,
                retryCount=job.retry_count,
                createdAt=job.created_at,
                queuedAt=job.queued_at,
                startedAt=job.started_at,
                finishedAt=job.finished_at,
                cleanupStatus=job.cleanup_status,
                cleanedAt=job.cleaned_at,
                crawlerExitCode=job.crawler_exit_code,
                crawlerStatus=job.crawler_status,
                warcFileCount=job.warc_file_count,
                indexedPageCount=job.indexed_page_count,
            )
        )

    return JobListResponseSchema(
        items=items,
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/jobs/status-counts", response_model=JobStatusCountsSchema)
def job_status_counts(
    db: Session = Depends(get_db),
) -> JobStatusCountsSchema:
    """
    Return counts of jobs grouped by status.
    """
    rows = (
        db.query(ArchiveJob.status, func.count(ArchiveJob.id))
        .group_by(ArchiveJob.status)
        .all()
    )

    counts = {status: count for status, count in rows}
    return JobStatusCountsSchema(counts=counts)


@router.get("/jobs/{job_id}", response_model=JobDetailSchema)
def get_job_detail(
    job_id: int,
    db: Session = Depends(get_db),
) -> JobDetailSchema:
    """
    Return detailed information about a single job.
    """
    row = (
        db.query(ArchiveJob, Source)
        .join(Source, ArchiveJob.source_id == Source.id, isouter=True)
        .filter(ArchiveJob.id == job_id)
        .first()
    )

    if row is None:
        raise HTTPException(status_code=404, detail="Job not found")

    job, src = row
    src_code = src.code if src is not None else ""
    src_name = src.name if src is not None else ""

    return JobDetailSchema(
        id=job.id,
        sourceCode=src_code,
        sourceName=src_name,
        name=job.name,
        status=job.status,
        retryCount=job.retry_count,
        createdAt=job.created_at,
        queuedAt=job.queued_at,
        startedAt=job.started_at,
        finishedAt=job.finished_at,
        cleanupStatus=job.cleanup_status,
        cleanedAt=job.cleaned_at,
        outputDir=job.output_dir,
        crawlerExitCode=job.crawler_exit_code,
        crawlerStatus=job.crawler_status,
        crawlerStage=job.crawler_stage,
        warcFileCount=job.warc_file_count,
        indexedPageCount=job.indexed_page_count,
        pagesCrawled=job.pages_crawled,
        pagesTotal=job.pages_total,
        pagesFailed=job.pages_failed,
        finalZimPath=job.final_zim_path,
        combinedLogPath=job.combined_log_path,
        stateFilePath=job.state_file_path,
        config=job.config,
        lastStats=job.last_stats_json,
    )


@router.get(
    "/jobs/{job_id}/snapshots",
    response_model=List[JobSnapshotSummarySchema],
)
def list_job_snapshots(
    job_id: int,
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
) -> List[JobSnapshotSummarySchema]:
    """
    List snapshots associated with a given job.
    """
    snapshots = (
        db.query(Snapshot)
        .options(
            load_only(
                Snapshot.id,
                Snapshot.url,
                Snapshot.capture_timestamp,
                Snapshot.status_code,
                Snapshot.language,
                Snapshot.title,
            )
        )
        .filter(Snapshot.job_id == job_id)
        .order_by(Snapshot.capture_timestamp.desc(), Snapshot.id.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )

    return [
        JobSnapshotSummarySchema(
            id=snap.id,
            url=snap.url,
            captureTimestamp=snap.capture_timestamp,
            statusCode=snap.status_code,
            language=snap.language,
            title=snap.title,
        )
        for snap in snapshots
    ]


def _has_table(db: Session, table_name: str) -> bool:
    try:
        return inspect(db.get_bind()).has_table(table_name)
    except Exception:
        return False


def _has_column(db: Session, table_name: str, column_name: str) -> bool:
    try:
        cols = inspect(db.get_bind()).get_columns(table_name)
        return any(c.get("name") == column_name for c in cols)
    except Exception:
        return False


@router.get("/search-debug", response_model=SearchDebugResponseSchema)
def search_debug(
    q: Optional[str] = Query(default=None, min_length=1, max_length=256),
    source: Optional[str] = Query(default=None, min_length=1, max_length=16),
    topic: Optional[str] = Query(default=None, min_length=1, max_length=64),
    sort: Optional[SearchSort] = Query(default=None),
    view: Optional[SearchView] = Query(default=None),
    includeNon2xx: bool = Query(default=False),
    page: int = Query(default=1, ge=1),
    pageSize: int = Query(default=20, ge=1, le=100),
    ranking: Optional[str] = Query(default=None, pattern=r"^(v1|v2)$"),
    db: Session = Depends(get_db),
) -> SearchDebugResponseSchema:
    """
    Admin-only debug endpoint for search relevance.

    Returns the same ordering as /api/search but with a score breakdown.
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
    query_mode = None
    query_tokens: list[str] = []
    ranking_cfg = None
    if q_clean and ranking_version == RankingVersion.v2:
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
        query = query.join(Snapshot.topics).filter(Topic.slug == topic)

    if not includeNon2xx:
        query = query.filter(
            or_(
                Snapshot.status_code.is_(None),
                and_(Snapshot.status_code >= 200, Snapshot.status_code < 300),
            )
        )

    tsquery = None
    vector_expr = None
    if q_clean:
        if use_postgres_fts and effective_sort == SearchSort.relevance:
            tsquery = func.websearch_to_tsquery(TS_CONFIG, q_clean)
            vector_expr = func.coalesce(
                Snapshot.search_vector,
                build_search_vector(Snapshot.title, Snapshot.snippet, Snapshot.url),
            )
            query = query.filter(vector_expr.op("@@")(tsquery))
        else:
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

    has_page_signals = (
        effective_sort == SearchSort.relevance and q_clean is not None and _has_table(db, "page_signals")
    )
    has_snapshot_outlinks = _has_table(db, "snapshot_outlinks")
    has_ps_outlink_count = has_page_signals and _has_column(db, "page_signals", "outlink_count")
    has_ps_pagerank = has_page_signals and _has_column(db, "page_signals", "pagerank")

    use_authority = has_page_signals
    use_hubness = (
        ranking_version == RankingVersion.v2
        and ranking_cfg is not None
        and query_mode == QueryMode.broad
        and has_snapshot_outlinks
    )
    use_pagerank = (
        ranking_version == RankingVersion.v2
        and ranking_cfg is not None
        and query_mode == QueryMode.broad
        and has_ps_pagerank
    )

    inlink_count_expr = func.coalesce(PageSignal.inlink_count, 0) if has_page_signals else None
    outlink_count_expr = None
    if use_hubness:
        correlated = (
            db.query(func.count(func.distinct(SnapshotOutlink.to_normalized_url_group)))
            .filter(SnapshotOutlink.snapshot_id == Snapshot.id)
            .filter(SnapshotOutlink.to_normalized_url_group != group_key)
            .correlate(Snapshot)
            .scalar_subquery()
        )
        correlated = func.coalesce(correlated, 0)
        if has_ps_outlink_count:
            outlink_count_expr = func.coalesce(PageSignal.outlink_count, correlated)
        else:
            outlink_count_expr = correlated

    pagerank_expr = func.coalesce(PageSignal.pagerank, 0.0) if use_pagerank else None

    def build_rank_text() -> object:
        if not (q_clean and use_postgres_fts and tsquery is not None and vector_expr is not None):
            return None
        if ranking_version == RankingVersion.v2 and query_mode is not None and query_mode != QueryMode.specific:
            return func.ts_rank_cd(vector_expr, tsquery, 32)
        return func.ts_rank_cd(vector_expr, tsquery)

    def build_title_boost() -> object:
        if not q_clean:
            return 0.0
        if ranking_version != RankingVersion.v2 or not query_tokens or ranking_cfg is None:
            return case((Snapshot.title.ilike(f"%{q_clean}%"), 0.2), else_=0.0)
        token_exprs = [Snapshot.title.ilike(f"%{t}%") for t in query_tokens]
        any_match = or_(*token_exprs)
        all_match = and_(*token_exprs) if len(token_exprs) > 1 else any_match
        return case(
            (all_match, float(ranking_cfg.title_all_tokens_boost)),
            (any_match, float(ranking_cfg.title_any_token_boost)),
            else_=0.0,
        )

    def build_archived_penalty() -> object:
        if ranking_version == RankingVersion.v2 and ranking_cfg is not None and ranking_cfg.archived_penalty != 0:
            return case(
                (Snapshot.title.ilike("archived%"), float(ranking_cfg.archived_penalty)),
                else_=0.0,
            )
        return 0.0

    def build_query_penalty() -> object:
        return case((Snapshot.url.like("%?%"), -0.1), else_=0.0)

    def build_tracking_penalty() -> object:
        return case(
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

    def build_depth_penalty() -> object:
        basis = group_key if (ranking_version == RankingVersion.v2 and ranking_cfg is not None) else Snapshot.url
        slash_count = func.length(basis) - func.length(func.replace(basis, "/", ""))
        if ranking_version == RankingVersion.v2 and ranking_cfg is not None:
            return float(ranking_cfg.depth_coef) * slash_count
        return (-0.01) * slash_count

    def build_authority_boost() -> object:
        if not use_authority or inlink_count_expr is None:
            return 0.0
        if ranking_version == RankingVersion.v2 and ranking_cfg is not None:
            if use_postgres_fts:
                return float(ranking_cfg.authority_coef) * func.ln(inlink_count_expr + 1)
            tier = case(
                (inlink_count_expr >= 100, 3),
                (inlink_count_expr >= 20, 2),
                (inlink_count_expr >= 5, 1),
                else_=0,
            )
            return tier
        if use_postgres_fts:
            return 0.05 * func.ln(inlink_count_expr + 1)
        tier = case(
            (inlink_count_expr >= 100, 3),
            (inlink_count_expr >= 20, 2),
            (inlink_count_expr >= 5, 1),
            else_=0,
        )
        return tier

    def build_hubness_boost() -> object:
        if not use_hubness or outlink_count_expr is None or ranking_cfg is None:
            return 0.0
        if use_postgres_fts:
            return float(ranking_cfg.hubness_coef) * func.ln(outlink_count_expr + 1)
        tier = case(
            (outlink_count_expr >= 100, 3),
            (outlink_count_expr >= 20, 2),
            (outlink_count_expr >= 5, 1),
            else_=0,
        )
        return float(ranking_cfg.hubness_coef) * tier

    def build_pagerank_boost() -> object:
        if not use_pagerank or pagerank_expr is None or ranking_cfg is None:
            return 0.0
        if use_postgres_fts:
            return float(ranking_cfg.pagerank_coef) * func.ln(pagerank_expr + 1)
        return float(ranking_cfg.pagerank_coef) * pagerank_expr

    rank_text = build_rank_text()
    title_boost = build_title_boost().label("title_boost")
    archived_penalty = build_archived_penalty().label("archived_penalty")
    query_penalty = build_query_penalty().label("query_penalty")
    tracking_penalty = build_tracking_penalty().label("tracking_penalty")
    depth_penalty = build_depth_penalty().label("depth_penalty")
    authority_boost = build_authority_boost().label("authority_boost")
    hubness_boost = build_hubness_boost().label("hubness_boost")
    pagerank_boost = build_pagerank_boost().label("pagerank_boost")

    rank_text_labeled = (
        rank_text.label("rank_text") if rank_text is not None else None
    )

    score_terms = [
        title_boost,
        archived_penalty,
        query_penalty,
        tracking_penalty,
        depth_penalty,
        authority_boost,
        hubness_boost,
        pagerank_boost,
    ]
    if rank_text is not None:
        score_terms.insert(0, rank_text)

    snapshot_score = None
    if effective_sort == SearchSort.relevance and q_clean:
        snapshot_score = score_terms[0]
        for term in score_terms[1:]:
            snapshot_score = snapshot_score + term
        snapshot_score = snapshot_score.label("total_score")

    base_query = query
    if has_page_signals:
        base_query = base_query.outerjoin(PageSignal, PageSignal.normalized_url_group == group_key)

    def rows_to_items(rows) -> List[SearchDebugItemSchema]:
        items: List[SearchDebugItemSchema] = []
        for row in rows:
            snap: Snapshot = row[0]
            source_code = getattr(row, "source_code", None)
            source_name = getattr(row, "source_name", None)
            if source_code is None or source_name is None:
                # Fallback: may trigger lazy-load (fine for admin/debug).
                if snap.source is not None:
                    source_code = snap.source.code
                    source_name = snap.source.name
                else:
                    source_code = ""
                    source_name = ""
            items.append(
                SearchDebugItemSchema(
                    id=snap.id,
                    title=snap.title,
                    sourceCode=str(source_code),
                    sourceName=str(source_name),
                    language=snap.language,
                    captureTimestamp=snap.capture_timestamp,
                    statusCode=snap.status_code,
                    originalUrl=snap.url,
                    normalizedUrlGroup=snap.normalized_url_group,
                    inlinkCount=int(getattr(row, "inlink_count", None) or 0) if has_page_signals else None,
                    outlinkCount=int(getattr(row, "outlink_count", None) or 0) if (has_page_signals and has_ps_outlink_count) else None,
                    pagerank=float(getattr(row, "pagerank", None) or 0.0) if (has_page_signals and has_ps_pagerank) else None,
                    rankText=float(getattr(row, "rank_text", None)) if getattr(row, "rank_text", None) is not None else None,
                    titleBoost=float(getattr(row, "title_boost", 0.0) or 0.0),
                    archivedPenalty=float(getattr(row, "archived_penalty", 0.0) or 0.0),
                    queryPenalty=float(getattr(row, "query_penalty", 0.0) or 0.0),
                    trackingPenalty=float(getattr(row, "tracking_penalty", 0.0) or 0.0),
                    depthPenalty=float(getattr(row, "depth_penalty", 0.0) or 0.0),
                    authorityBoost=float(getattr(row, "authority_boost", 0.0) or 0.0),
                    hubnessBoost=float(getattr(row, "hubness_boost", 0.0) or 0.0),
                    pagerankBoost=float(getattr(row, "pagerank_boost", 0.0) or 0.0),
                    totalScore=float(getattr(row, "total_score", None)) if getattr(row, "total_score", None) is not None else None,
                    groupScore=float(getattr(row, "group_score", None)) if getattr(row, "group_score", None) is not None else None,
                    bestSnapshotId=int(getattr(row, "best_snapshot_id", None)) if getattr(row, "best_snapshot_id", None) is not None else None,
                )
            )
        return items

    ordered_rows = []
    if effective_view == SearchView.snapshots:
        ent = [Snapshot]
        ent.extend(
            [
                Source.code.label("source_code"),
                Source.name.label("source_name"),
            ]
        )
        if rank_text_labeled is not None:
            ent.append(rank_text_labeled)
        ent.extend(
            [
                title_boost,
                archived_penalty,
                query_penalty,
                tracking_penalty,
                depth_penalty,
                authority_boost,
                hubness_boost,
                pagerank_boost,
            ]
        )
        if snapshot_score is not None:
            ent.append(snapshot_score)

        if has_page_signals:
            ent.extend(
                [
                    PageSignal.inlink_count.label("inlink_count"),
                ]
            )
            if has_ps_outlink_count:
                ent.append(PageSignal.outlink_count.label("outlink_count"))
            if has_ps_pagerank:
                ent.append(PageSignal.pagerank.label("pagerank"))

        ordered = base_query.with_entities(*ent)
        if snapshot_score is not None:
            ordered = ordered.order_by(
                status_quality.desc(),
                snapshot_score.desc(),
                Snapshot.capture_timestamp.desc(),
                Snapshot.id.desc(),
            )
        else:
            ordered = ordered.order_by(
                status_quality.desc(),
                Snapshot.capture_timestamp.desc(),
                Snapshot.id.desc(),
            )
        ordered_rows = (
            ordered.options(load_only(Snapshot.id, Snapshot.source_id, Snapshot.url, Snapshot.normalized_url_group, Snapshot.capture_timestamp, Snapshot.status_code, Snapshot.title, Snapshot.snippet, Snapshot.language))
            .offset(offset)
            .limit(pageSize)
            .all()
        )
    else:
        # pages: compute latest snapshot per group; v2 orders by group_score (best snapshot in group).
        rn_latest = func.row_number().over(
            partition_by=group_key,
            order_by=(Snapshot.capture_timestamp.desc(), Snapshot.id.desc()),
        ).label("rn_latest")

        if snapshot_score is None:
            # No query: treat as newest pages
            candidates = base_query.with_entities(
                Snapshot.id.label("id"),
                group_key.label("group_key"),
                rn_latest,
            ).subquery()
            latest = db.query(Snapshot).join(candidates, Snapshot.id == candidates.c.id).filter(candidates.c.rn_latest == 1)
            latest = latest.order_by(status_quality.desc(), Snapshot.capture_timestamp.desc(), Snapshot.id.desc())
            snaps = latest.offset(offset).limit(pageSize).all()
            ordered_rows = [(s,) for s in snaps]
        else:
            rn_best = func.row_number().over(
                partition_by=group_key,
                order_by=(snapshot_score.desc(), Snapshot.capture_timestamp.desc(), Snapshot.id.desc()),
            ).label("rn_best")

            candidates_subq = base_query.with_entities(
                Snapshot.id.label("id"),
                group_key.label("group_key"),
                Snapshot.capture_timestamp.label("capture_timestamp"),
                rn_latest,
                rn_best,
                snapshot_score.label("snapshot_score"),
                (rank_text_labeled if rank_text_labeled is not None else cast(literal(None), Float)).label("rank_text"),
                title_boost,
                archived_penalty,
                query_penalty,
                tracking_penalty,
                depth_penalty,
                authority_boost,
                hubness_boost,
                pagerank_boost,
                PageSignal.inlink_count.label("inlink_count") if has_page_signals else cast(literal(None), Integer).label("inlink_count"),
                PageSignal.outlink_count.label("outlink_count") if has_ps_outlink_count else cast(literal(None), Integer).label("outlink_count"),
                PageSignal.pagerank.label("pagerank") if has_ps_pagerank else cast(literal(None), Float).label("pagerank"),
            ).subquery()

            latest_subq = (
                db.query(candidates_subq)
                .filter(candidates_subq.c.rn_latest == 1)
                .subquery()
            )
            best_subq = (
                db.query(
                    candidates_subq.c.group_key.label("group_key"),
                    candidates_subq.c.id.label("best_snapshot_id"),
                    candidates_subq.c.snapshot_score.label("group_score"),
                )
                .filter(candidates_subq.c.rn_best == 1)
                .subquery()
            )

            ordered = (
                db.query(Snapshot)
                .join(latest_subq, Snapshot.id == latest_subq.c.id)
                .join(best_subq, best_subq.c.group_key == latest_subq.c.group_key)
                .with_entities(
                    Snapshot,
                    Source.code.label("source_code"),
                    Source.name.label("source_name"),
                    latest_subq.c.rank_text.label("rank_text"),
                    latest_subq.c.title_boost,
                    latest_subq.c.archived_penalty,
                    latest_subq.c.query_penalty,
                    latest_subq.c.tracking_penalty,
                    latest_subq.c.depth_penalty,
                    latest_subq.c.authority_boost,
                    latest_subq.c.hubness_boost,
                    latest_subq.c.pagerank_boost,
                    latest_subq.c.snapshot_score.label("total_score"),
                    latest_subq.c.inlink_count,
                    latest_subq.c.outlink_count,
                    latest_subq.c.pagerank,
                    best_subq.c.group_score,
                    best_subq.c.best_snapshot_id,
                )
                .join(Source, Snapshot.source_id == Source.id, isouter=True)
                .order_by(
                    status_quality.desc(),
                    best_subq.c.group_score.desc() if ranking_version == RankingVersion.v2 else latest_subq.c.snapshot_score.desc(),
                    Snapshot.capture_timestamp.desc(),
                    Snapshot.id.desc(),
                )
            )

            ordered_rows = ordered.offset(offset).limit(pageSize).all()

    results = rows_to_items(ordered_rows)
    return SearchDebugResponseSchema(
        results=results,
        total=int(total),
        page=page,
        pageSize=pageSize,
        dialect=dialect_name,
        mode=mode,
        view=effective_view.value,
        sort=effective_sort.value,
        rankingVersion=ranking_version.value,
        queryMode=query_mode.value if query_mode is not None else None,
        usedPageSignals=bool(has_page_signals),
        usedSnapshotOutlinks=bool(has_snapshot_outlinks),
        usedPagerank=bool(use_pagerank),
    )


__all__ = ["router"]
