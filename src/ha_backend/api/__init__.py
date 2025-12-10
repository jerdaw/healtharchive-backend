from __future__ import annotations

from ha_backend.logging_config import configure_logging
from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse
from sqlalchemy import func
from sqlalchemy.orm import Session

from ha_backend.config import get_cors_origins
from ha_backend.db import get_session
from ha_backend.models import ArchiveJob, Snapshot, Source

from .deps import require_admin
from .routes_admin import router as admin_router
from .routes_public import router as public_router


configure_logging()

app = FastAPI(
  title="HealthArchive Backend API",
  version="0.1.0",
)

app.add_middleware(
  CORSMiddleware,
  allow_origins=get_cors_origins(),
  allow_credentials=False,
  allow_methods=["*"],
  allow_headers=["*"],
)


def _metrics_get_db() -> Session:
  with get_session() as session:
    yield session


@app.get("/metrics", response_class=PlainTextResponse)
async def metrics(
  db: Session = Depends(_metrics_get_db),
  _: None = Depends(require_admin),
) -> PlainTextResponse:
  """
  Prometheus-style metrics endpoint summarising jobs and snapshots.
  """
  lines = []

  # Job counts by status
  job_rows = (
    db.query(ArchiveJob.status, func.count(ArchiveJob.id))
    .group_by(ArchiveJob.status)
    .all()
  )
  lines.append("# HELP healtharchive_jobs_total Number of archive jobs by status")
  lines.append("# TYPE healtharchive_jobs_total gauge")
  for status, count in job_rows:
    lines.append(f'healtharchive_jobs_total{{status="{status}"}} {int(count)}')

  # Job counts by cleanup status
  cleanup_rows = (
    db.query(ArchiveJob.cleanup_status, func.count(ArchiveJob.id))
    .group_by(ArchiveJob.cleanup_status)
    .all()
  )
  lines.append(
    "# HELP healtharchive_jobs_cleanup_status_total Number of archive jobs by cleanup_status"
  )
  lines.append("# TYPE healtharchive_jobs_cleanup_status_total gauge")
  for cleanup_status, count in cleanup_rows:
    lines.append(
      f'healtharchive_jobs_cleanup_status_total{{cleanup_status="{cleanup_status}"}} {int(count)}'
    )

  # Snapshot totals (global and per source)
  total_snapshots = db.query(func.count(Snapshot.id)).scalar() or 0
  lines.append("# HELP healtharchive_snapshots_total Number of snapshots")
  lines.append("# TYPE healtharchive_snapshots_total gauge")
  lines.append(f"healtharchive_snapshots_total {int(total_snapshots)}")

  per_source_rows = (
    db.query(Source.code, func.count(Snapshot.id))
    .join(Snapshot, Snapshot.source_id == Source.id)
    .group_by(Source.code)
    .all()
  )
  for code, count in per_source_rows:
    lines.append(
      f'healtharchive_snapshots_total{{source="{code}"}} {int(count)}'
    )

  body = "\n".join(lines) + "\n"
  return PlainTextResponse(content=body)


app.include_router(public_router, prefix="/api")
app.include_router(admin_router, prefix="/api")

__all__ = ["app"]
