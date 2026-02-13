from __future__ import annotations

from datetime import timezone
from typing import Iterator

from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse
from slowapi.errors import RateLimitExceeded
from sqlalchemy import func, inspect
from sqlalchemy.orm import Session

from ha_backend.config import (
    get_cors_origins,
    get_csp_enabled,
    get_hsts_enabled,
    get_hsts_max_age,
    get_max_query_string_length,
    get_max_request_body_size,
    get_pages_fastpath_enabled,
)
from ha_backend.db import get_session
from ha_backend.logging_config import configure_logging
from ha_backend.models import ArchiveJob, Page, Snapshot, Source
from ha_backend.rate_limiting import limiter
from ha_backend.request_context import generate_request_id, set_request_id
from ha_backend.runtime_metrics import render_search_metrics_prometheus

from .deps import require_admin
from .routes_admin import router as admin_router
from .routes_public import router as public_router

configure_logging()

# API version for X-API-Version header (semantic versioning)
API_VERSION = "1"

app = FastAPI(
    title="HealthArchive Backend API",
    version="0.1.0",
)

# Register rate limiter with the app
app.state.limiter = limiter


# Custom rate limit exception handler that satisfies type checking
async def rate_limit_handler(request: Request, exc: Exception) -> JSONResponse:
    """Handle rate limit exceeded errors with proper response format."""
    if isinstance(exc, RateLimitExceeded):
        return JSONResponse(
            status_code=429,
            content={"error": "Rate limit exceeded", "detail": str(exc.detail)},
            headers=getattr(exc, "headers", {}),
        )
    # Fallback for unexpected exceptions
    return JSONResponse(
        status_code=429,
        content={"error": "Rate limit exceeded"},
    )


app.add_exception_handler(RateLimitExceeded, rate_limit_handler)


@app.middleware("http")
async def request_id_middleware(request: Request, call_next):
    """
    Generate and inject a unique request ID for correlation tracking.

    Honors incoming X-Request-Id headers for pass-through, or generates
    a new UUIDv4 if none is provided. The request ID is:
    - Set in request context for logging
    - Returned as X-Request-Id response header
    """
    # Use incoming request ID if provided, otherwise generate new one
    request_id = request.headers.get("X-Request-Id") or generate_request_id()
    set_request_id(request_id)

    response = await call_next(request)
    response.headers["X-Request-Id"] = request_id
    return response


@app.middleware("http")
async def request_size_limit_middleware(request: Request, call_next):
    """
    Enforce request size limits to prevent abuse.

    Checks:
    - Query string length (return 414 URI Too Long if exceeded)
    - Request body size (return 413 Payload Too Large if exceeded)

    Size limits are configurable via environment variables:
    - HEALTHARCHIVE_MAX_QUERY_STRING_LENGTH (default: 8KB)
    - HEALTHARCHIVE_MAX_REQUEST_BODY_SIZE (default: 1MB)
    """
    # Check query string length
    max_query_len = get_max_query_string_length()
    query_string = request.url.query or ""
    if len(query_string) > max_query_len:
        return JSONResponse(
            status_code=414,
            content={
                "error": "URI Too Long",
                "detail": f"Query string exceeds maximum length of {max_query_len} characters",
            },
        )

    # Check request body size (if Content-Length header is present)
    max_body_size = get_max_request_body_size()
    content_length = request.headers.get("content-length")
    if content_length:
        try:
            body_size = int(content_length)
            if body_size > max_body_size:
                return JSONResponse(
                    status_code=413,
                    content={
                        "error": "Payload Too Large",
                        "detail": f"Request body exceeds maximum size of {max_body_size} bytes",
                    },
                )
        except ValueError:
            pass  # Invalid Content-Length header, let the framework handle it

    response = await call_next(request)
    return response


@app.middleware("http")
async def api_version_middleware(request: Request, call_next):
    """
    Inject API version header on all responses.

    This allows clients to detect API version and handle compatibility.
    Version is semantic: major version changes indicate breaking changes.
    """
    response = await call_next(request)
    response.headers["X-API-Version"] = API_VERSION
    return response


@app.middleware("http")
async def security_headers_middleware(request: Request, call_next):
    """
    Inject security-related headers on all HTTP responses.

    Includes: X-Content-Type-Options, Referrer-Policy, X-Frame-Options,
    Permissions-Policy, Content-Security-Policy, and Strict-Transport-Security.

    Note: we implement this as function-based middleware (rather than
    BaseHTTPMiddleware) to avoid known edge cases in Starlette's
    BaseHTTPMiddleware with TestClient/anyio.
    """
    response = await call_next(request)
    headers = response.headers

    # Basic security headers
    headers.setdefault("X-Content-Type-Options", "nosniff")
    headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")

    # X-Frame-Options: Allow raw snapshot endpoint to be iframed by frontend
    # The raw snapshot route is a controlled HTML replay endpoint and is
    # additionally sandboxed on the frontend side.
    is_raw_snapshot = request.url.path.startswith("/api/snapshots/raw/")
    if not is_raw_snapshot:
        headers.setdefault("X-Frame-Options", "SAMEORIGIN")

    # Permissions-Policy: Disable sensitive browser features
    headers.setdefault(
        "Permissions-Policy",
        "geolocation=(), microphone=(), camera=()",
    )

    # Content-Security-Policy: Prevent XSS and injection attacks
    if get_csp_enabled():
        if is_raw_snapshot:
            # Archived HTML replay needs permissive CSP for inline scripts/styles
            # and external resources (images, fonts, etc.)
            csp_policy = (
                "default-src 'none'; "
                "script-src 'unsafe-inline' 'unsafe-eval'; "
                "style-src 'unsafe-inline' *; "
                "img-src * data: blob:; "
                "font-src * data:; "
                "connect-src *; "
                "media-src *; "
                "object-src 'none'; "
                "frame-src *; "
                "base-uri 'self'; "
                "form-action 'self'"
            )
        else:
            # JSON API endpoints: very restrictive CSP
            csp_policy = "default-src 'none'; frame-ancestors 'none'"

        headers.setdefault("Content-Security-Policy", csp_policy)

    # HSTS: Enforce HTTPS for 1 year (only meaningful when served over HTTPS)
    if get_hsts_enabled():
        max_age = get_hsts_max_age()
        headers.setdefault("Strict-Transport-Security", f"max-age={max_age}; includeSubDomains")

    return response


app.add_middleware(
    CORSMiddleware,
    allow_origins=get_cors_origins(),
    allow_credentials=False,
    allow_methods=["GET", "HEAD", "OPTIONS"],
    allow_headers=["*"],
)


def _metrics_get_db() -> Iterator[Session]:
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
        db.query(ArchiveJob.status, func.count(ArchiveJob.id)).group_by(ArchiveJob.status).all()
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

    # Storage bytes (best-effort; depends on jobs having been scanned and persisted).
    totals_row = db.query(
        func.coalesce(func.sum(ArchiveJob.warc_bytes_total), 0),
        func.coalesce(func.sum(ArchiveJob.output_bytes_total), 0),
        func.coalesce(func.sum(ArchiveJob.tmp_bytes_total), 0),
        func.coalesce(func.sum(ArchiveJob.tmp_non_warc_bytes_total), 0),
    ).one()
    (
        total_warc_bytes,
        total_output_bytes,
        total_tmp_bytes,
        total_tmp_non_warc_bytes,
    ) = totals_row

    scanned_jobs = (
        db.query(func.count(ArchiveJob.id))
        .filter(ArchiveJob.storage_scanned_at.isnot(None))
        .scalar()
        or 0
    )

    lines.append(
        "# HELP healtharchive_jobs_storage_scanned_total Number of jobs with storage stats computed at least once"
    )
    lines.append("# TYPE healtharchive_jobs_storage_scanned_total gauge")
    lines.append(f"healtharchive_jobs_storage_scanned_total {int(scanned_jobs)}")

    lines.append(
        "# HELP healtharchive_jobs_warc_bytes_total Total WARC bytes across jobs (from last persisted scan)"
    )
    lines.append("# TYPE healtharchive_jobs_warc_bytes_total gauge")
    lines.append(f"healtharchive_jobs_warc_bytes_total {int(total_warc_bytes)}")

    lines.append(
        "# HELP healtharchive_jobs_output_bytes_total Total output bytes across jobs (from last persisted scan)"
    )
    lines.append("# TYPE healtharchive_jobs_output_bytes_total gauge")
    lines.append(f"healtharchive_jobs_output_bytes_total {int(total_output_bytes)}")

    lines.append(
        "# HELP healtharchive_jobs_tmp_bytes_total Total .tmp* bytes across jobs (from last persisted scan)"
    )
    lines.append("# TYPE healtharchive_jobs_tmp_bytes_total gauge")
    lines.append(f"healtharchive_jobs_tmp_bytes_total {int(total_tmp_bytes)}")

    lines.append(
        "# HELP healtharchive_jobs_tmp_non_warc_bytes_total Total non-WARC bytes under .tmp* across jobs (from last persisted scan)"
    )
    lines.append("# TYPE healtharchive_jobs_tmp_non_warc_bytes_total gauge")
    lines.append(f"healtharchive_jobs_tmp_non_warc_bytes_total {int(total_tmp_non_warc_bytes)}")

    per_source_storage = (
        db.query(
            Source.code,
            func.coalesce(func.sum(ArchiveJob.warc_bytes_total), 0),
            func.coalesce(func.sum(ArchiveJob.output_bytes_total), 0),
            func.coalesce(func.sum(ArchiveJob.tmp_bytes_total), 0),
            func.coalesce(func.sum(ArchiveJob.tmp_non_warc_bytes_total), 0),
        )
        .join(ArchiveJob, ArchiveJob.source_id == Source.id)
        .group_by(Source.code)
        .all()
    )
    for code, warc_bytes, output_bytes, tmp_bytes, tmp_non_warc_bytes in per_source_storage:
        lines.append(f'healtharchive_jobs_warc_bytes_total{{source="{code}"}} {int(warc_bytes)}')
        lines.append(
            f'healtharchive_jobs_output_bytes_total{{source="{code}"}} {int(output_bytes)}'
        )
        lines.append(f'healtharchive_jobs_tmp_bytes_total{{source="{code}"}} {int(tmp_bytes)}')
        lines.append(
            f'healtharchive_jobs_tmp_non_warc_bytes_total{{source="{code}"}} {int(tmp_non_warc_bytes)}'
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

    insp = inspect(db.get_bind())
    pages_table_present = bool(insp.has_table("pages"))
    lines.append(
        "# HELP healtharchive_pages_table_present Whether the pages table exists in the DB"
    )
    lines.append("# TYPE healtharchive_pages_table_present gauge")
    lines.append(f"healtharchive_pages_table_present {1 if pages_table_present else 0}")

    lines.append(
        "# HELP healtharchive_pages_fastpath_enabled Whether /api/search can use the pages-table browse fast path"
    )
    lines.append("# TYPE healtharchive_pages_fastpath_enabled gauge")
    lines.append(f"healtharchive_pages_fastpath_enabled {1 if get_pages_fastpath_enabled() else 0}")

    if pages_table_present:
        total_pages = db.query(func.count(Page.id)).scalar() or 0
        lines.append(
            "# HELP healtharchive_pages_total Number of pages (URL groups) in the pages table"
        )
        lines.append("# TYPE healtharchive_pages_total gauge")
        lines.append(f"healtharchive_pages_total {int(total_pages)}")

        per_source_pages_rows = (
            db.query(Source.code, func.count(Page.id))
            .join(Page, Page.source_id == Source.id)
            .group_by(Source.code)
            .all()
        )
        for code, count in per_source_pages_rows:
            lines.append(f'healtharchive_pages_total{{source="{code}"}} {int(count)}')

        max_updated_at = db.query(func.max(Page.updated_at)).scalar()
        max_updated_at_seconds = 0
        if max_updated_at is not None:
            if max_updated_at.tzinfo is None:
                max_updated_at = max_updated_at.replace(tzinfo=timezone.utc)
            max_updated_at_seconds = int(max_updated_at.timestamp())
        lines.append(
            "# HELP healtharchive_pages_updated_at_max_seconds Max pages.updated_at (Unix epoch seconds, UTC)"
        )
        lines.append("# TYPE healtharchive_pages_updated_at_max_seconds gauge")
        lines.append(f"healtharchive_pages_updated_at_max_seconds {max_updated_at_seconds}")

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

    lines.extend(render_search_metrics_prometheus())

    body = "\n".join(lines) + "\n"
    return PlainTextResponse(content=body)


app.include_router(public_router, prefix="/api")
app.include_router(admin_router, prefix="/api")

__all__ = ["app"]
