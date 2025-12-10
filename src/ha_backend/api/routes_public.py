from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy import func, or_
from sqlalchemy.orm import Session, joinedload

from ha_backend.db import get_session
from ha_backend.indexing.viewer import find_record_for_snapshot
from ha_backend.models import ArchiveJob, Snapshot, Source, Topic

from .schemas import (
  SearchResponseSchema,
  SnapshotDetailSchema,
  SnapshotSummarySchema,
  SourceSummarySchema,
)


router = APIRouter()


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
    latest_record_id: Optional[int] = latest_snapshot[0] if latest_snapshot else None

    # Distinct topic labels (if any)
    topic_labels = (
      db.query(Topic.label)
      .join(Topic.snapshots)
      .filter(Snapshot.source_id == source.id)
      .distinct()
      .order_by(Topic.label)
      .all()
    )
    topics = [label for (label,) in topic_labels]

    summaries.append(
      SourceSummarySchema(
        sourceCode=source.code,
        sourceName=source.name,
        recordCount=record_count or 0,
        firstCapture=first_capture.date().isoformat()
        if isinstance(first_capture, datetime)
        else str(first_capture),
        lastCapture=last_capture.date().isoformat()
        if isinstance(last_capture, datetime)
        else str(last_capture),
        topics=topics,
        latestRecordId=latest_record_id,
      )
    )

  return summaries


@router.get("/search", response_model=SearchResponseSchema)
def search_snapshots(
  q: Optional[str] = Query(default=None),
  source: Optional[str] = Query(default=None),
  topic: Optional[str] = Query(default=None),
  page: int = Query(default=1, ge=1),
  pageSize: int = Query(default=20, ge=1, le=100),
  db: Session = Depends(get_db),
) -> SearchResponseSchema:
  """
  Search snapshots by keyword, source, and/or topic with simple pagination.
  """
  query = db.query(Snapshot).join(Source)

  if source:
    query = query.filter(Source.code == source.lower())

  if topic:
    query = query.join(Snapshot.topics).join(Topic).filter(Topic.slug == topic)

  if q:
    ilike_pattern = f"%{q}%"
    query = query.filter(
      or_(
        Snapshot.title.ilike(ilike_pattern),
        Snapshot.snippet.ilike(ilike_pattern),
        Snapshot.url.ilike(ilike_pattern),
      )
    )

  total = query.count()
  offset = (page - 1) * pageSize

  items = (
    query.options(joinedload(Snapshot.source), joinedload(Snapshot.topics))
    .order_by(Snapshot.capture_timestamp.desc(), Snapshot.id.desc())
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

    topics = [t.label for t in snap.topics] if snap.topics is not None else []

    results.append(
      SnapshotSummarySchema(
        id=snap.id,
        title=snap.title,
        sourceCode=source_obj.code,
        sourceName=source_obj.name,
        language=snap.language,
        topics=topics,
        captureDate=capture_date,
        originalUrl=snap.url,
        snippet=snap.snippet,
        rawSnapshotUrl=f"/api/snapshots/raw/{snap.id}",
      )
    )

  return SearchResponseSchema(
    results=results,
    total=total,
    page=page,
    pageSize=pageSize,
  )


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
    .options(joinedload(Snapshot.source), joinedload(Snapshot.topics))
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

  topics = [t.label for t in snap.topics] if snap.topics is not None else []

  return SnapshotDetailSchema(
    id=snap.id,
    title=snap.title,
    sourceCode=snap.source.code,
    sourceName=snap.source.name,
    language=snap.language,
    topics=topics,
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
  snap = db.get(Snapshot, snapshot_id)
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
