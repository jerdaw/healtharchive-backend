from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func
from sqlalchemy.orm import Session, load_only

from ha_backend.db import get_session
from ha_backend.models import ArchiveJob, Snapshot, Source

from .deps import require_admin
from .schemas_admin import (JobDetailSchema, JobListResponseSchema,
                            JobSnapshotSummarySchema, JobStatusCountsSchema,
                            JobSummarySchema)

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


__all__ = ["router"]
