from __future__ import annotations

from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse
from sqlalchemy import func
from sqlalchemy.orm import Session

from ha_backend.config import get_cors_origins
from ha_backend.db import get_session
from ha_backend.logging_config import configure_logging
from ha_backend.models import ArchiveJob, Snapshot, Source

from .deps import require_admin
from .routes_admin import router as admin_router
from .routes_public import router as public_router

configure_logging()

app = FastAPI(
    title="HealthArchive Backend API",
    version="0.1.0",
)

@app.middleware("http")
async def security_headers_middleware(request: Request, call_next):
    """
    Inject a small set of security-related headers on all HTTP responses.

    Note: we implement this as function-based middleware (rather than
    BaseHTTPMiddleware) to avoid known edge cases in Starlette's
    BaseHTTPMiddleware with TestClient/anyio.
    """
    response = await call_next(request)
    headers = response.headers
    headers.setdefault("X-Content-Type-Options", "nosniff")
    headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    # Keep X-Frame-Options for most responses, but allow the raw snapshot
    # endpoint to be embedded in the frontend iframe. The raw snapshot
    # route is a controlled HTML replay endpoint and is additionally
    # sandboxed on the frontend side.
    if not request.url.path.startswith("/api/snapshots/raw/"):
        headers.setdefault("X-Frame-Options", "SAMEORIGIN")
    headers.setdefault(
        "Permissions-Policy",
        "geolocation=(), microphone=(), camera=()",
    )
    return response


app.add_middleware(
    CORSMiddleware,
    allow_origins=get_cors_origins(),
    allow_credentials=False,
    allow_methods=["GET", "HEAD", "OPTIONS"],
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
        lines.append(f'healtharchive_snapshots_total{{source="{code}"}} {int(count)}')

    # Page-level crawl metrics (derived from ArchiveJob pages_* fields).
    page_totals = db.query(
        func.coalesce(func.sum(ArchiveJob.pages_crawled), 0),
        func.coalesce(func.sum(ArchiveJob.pages_failed), 0),
    ).one()
    total_pages_crawled, total_pages_failed = page_totals

    lines.append(
        "# HELP healtharchive_jobs_pages_crawled_total Total pages crawled across all jobs"
    )
    lines.append("# TYPE healtharchive_jobs_pages_crawled_total gauge")
    lines.append(f"healtharchive_jobs_pages_crawled_total {int(total_pages_crawled)}")

    lines.append(
        "# HELP healtharchive_jobs_pages_failed_total Total pages that failed to crawl across all jobs"
    )
    lines.append("# TYPE healtharchive_jobs_pages_failed_total gauge")
    lines.append(f"healtharchive_jobs_pages_failed_total {int(total_pages_failed)}")

    per_source_pages = (
        db.query(
            Source.code,
            func.coalesce(func.sum(ArchiveJob.pages_crawled), 0),
            func.coalesce(func.sum(ArchiveJob.pages_failed), 0),
        )
        .join(ArchiveJob, ArchiveJob.source_id == Source.id)
        .group_by(Source.code)
        .all()
    )
    for code, crawled_count, failed_count in per_source_pages:
        lines.append(
            f'healtharchive_jobs_pages_crawled_total{{source="{code}"}} {int(crawled_count)}'
        )
        lines.append(
            f'healtharchive_jobs_pages_failed_total{{source="{code}"}} {int(failed_count)}'
        )

    body = "\n".join(lines) + "\n"
    return PlainTextResponse(content=body)


app.include_router(public_router, prefix="/api")
app.include_router(admin_router, prefix="/api")

__all__ = ["app"]
