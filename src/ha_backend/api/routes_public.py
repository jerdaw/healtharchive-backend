from __future__ import annotations

import csv
import gzip
import html
import io
import json
import re
import time
from datetime import date, datetime, timedelta, timezone
from enum import Enum
from pathlib import Path
from threading import BoundedSemaphore, Lock
from typing import Any, Dict, Iterable, Iterator, List, Optional, Sequence
from urllib.parse import urlencode, urlsplit, urlunsplit

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse
from sqlalchemy import String, and_, case, cast, func, inspect, literal, or_, text
from sqlalchemy.orm import Session, joinedload, load_only

from ha_backend.changes import CHANGE_TYPE_UNCHANGED, get_latest_job_ids_by_source
from ha_backend.config import (
    get_change_tracking_enabled,
    get_compare_live_enabled,
    get_compare_live_max_archive_bytes,
    get_compare_live_max_bytes,
    get_compare_live_max_concurrency,
    get_compare_live_max_redirects,
    get_compare_live_max_render_lines,
    get_compare_live_timeout_seconds,
    get_compare_live_user_agent,
    get_exports_default_limit,
    get_exports_enabled,
    get_exports_max_limit,
    get_pages_fastpath_enabled,
    get_public_site_base_url,
    get_replay_base_url,
    get_replay_preview_dir,
    get_usage_metrics_enabled,
    get_usage_metrics_window_days,
)
from ha_backend.db import get_session
from ha_backend.indexing.viewer import find_record_for_snapshot
from ha_backend.live_compare import (
    LiveCompareError,
    LiveCompareTooLarge,
    LiveFetchBlocked,
    LiveFetchError,
    LiveFetchNotHtml,
    LiveFetchTooLarge,
    build_compare_documents,
    build_compare_render_payload,
    compute_live_compare_from_docs,
    fetch_live_html,
    is_html_mime_type,
    load_snapshot_html,
    summarize_live_compare,
)
from ha_backend.models import (
    ArchiveJob,
    IssueReport,
    Page,
    PageSignal,
    Snapshot,
    SnapshotChange,
    Source,
)
from ha_backend.runtime_metrics import observe_search_request
from ha_backend.search import TS_CONFIG, build_search_vector
from ha_backend.search_fuzzy import (
    pick_word_similarity_threshold,
    should_use_url_similarity,
    token_variants,
)
from ha_backend.search_query import (
    And as BoolAnd,
)
from ha_backend.search_query import (
    Not as BoolNot,
)
from ha_backend.search_query import (
    Or as BoolOr,
)
from ha_backend.search_query import (
    QueryNode as BoolNode,
)
from ha_backend.search_query import (
    QueryParseError,
    iter_positive_terms,
    looks_like_advanced_query,
    parse_query,
)
from ha_backend.search_query import (
    Term as BoolTerm,
)
from ha_backend.search_ranking import (
    QueryMode,
    RankingVersion,
    classify_query_mode,
    get_ranking_config,
    get_ranking_version,
    tokenize_query,
)
from ha_backend.url_normalization import normalize_url_for_grouping
from ha_backend.usage_metrics import (
    EVENT_CHANGES_LIST,
    EVENT_COMPARE_LIVE_VIEW,
    EVENT_COMPARE_VIEW,
    EVENT_EXPORTS_DOWNLOAD_CHANGES,
    EVENT_EXPORTS_DOWNLOAD_SNAPSHOTS,
    EVENT_REPORT_SUBMITTED,
    EVENT_SEARCH_REQUEST,
    EVENT_SNAPSHOT_DETAIL,
    EVENT_SNAPSHOT_RAW,
    EVENT_TIMELINE_VIEW,
    build_usage_summary,
    record_usage_event,
)

from .schemas import (
    ArchiveStatsSchema,
    ChangeCompareSchema,
    ChangeCompareSnapshotSchema,
    ChangeEventSchema,
    ChangeFeedSchema,
    CompareLiveDiffSchema,
    CompareLiveFetchSchema,
    CompareLiveRenderInstructionSchema,
    CompareLiveRenderSchema,
    CompareLiveSchema,
    CompareLiveStatsSchema,
    ExportManifestSchema,
    ExportResourceSchema,
    IssueReportCreateSchema,
    IssueReportReceiptSchema,
    ReplayResolveSchema,
    SearchResponseSchema,
    SnapshotDetailSchema,
    SnapshotLatestSchema,
    SnapshotSummarySchema,
    SnapshotTimelineItemSchema,
    SnapshotTimelineSchema,
    SourceEditionSchema,
    SourceSummarySchema,
    UsageMetricsCountsSchema,
    UsageMetricsDaySchema,
    UsageMetricsSchema,
)

router = APIRouter()

_TABLE_EXISTS_CACHE: dict[tuple[int, str], bool] = {}
_TABLE_EXISTS_LOCK = Lock()

_COLUMN_EXISTS_CACHE: dict[tuple[int, str, str], bool] = {}
_COLUMN_EXISTS_LOCK = Lock()

_PG_TRGM_EXISTS_CACHE: dict[int, bool] = {}
_PG_TRGM_EXISTS_LOCK = Lock()

# We sometimes create synthetic test snapshots/sources for operational
# verification (e.g., backup restore checks). These should not surface in
# public browsing/search UI.
_PUBLIC_EXCLUDED_SOURCE_CODES = {"test"}

_COMPARE_LIVE_SEMAPHORE = BoundedSemaphore(get_compare_live_max_concurrency())


def _has_pg_trgm(db: Session) -> bool:
    bind = db.get_bind()
    if bind.dialect.name != "postgresql":
        return False

    cache_key = id(bind)
    with _PG_TRGM_EXISTS_LOCK:
        cached = _PG_TRGM_EXISTS_CACHE.get(cache_key)
    if cached is not None:
        return cached

    exists = False
    try:
        row = db.execute(
            text("SELECT 1 FROM pg_extension WHERE extname = 'pg_trgm' LIMIT 1")
        ).first()
        exists = row is not None
    except Exception:
        exists = False

    with _PG_TRGM_EXISTS_LOCK:
        _PG_TRGM_EXISTS_CACHE[cache_key] = exists
    return exists


def _format_capture_timestamp(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo:
            return value.astimezone(timezone.utc).isoformat()
        # Treat naive datetimes as UTC for API consistency (SQLite often
        # round-trips timezone-aware values as naive).
        return value.replace(tzinfo=timezone.utc).isoformat()
    return str(value)


_EXPORT_FORMATS = ("jsonl", "csv")
_EXPORT_CONTENT_TYPES = {
    "jsonl": "application/x-ndjson",
    "csv": "text/csv; charset=utf-8",
}
_SNAPSHOT_EXPORT_FIELDS = [
    "snapshot_id",
    "source_code",
    "source_name",
    "captured_url",
    "normalized_url_group",
    "capture_timestamp_utc",
    "language",
    "status_code",
    "mime_type",
    "title",
    "job_id",
    "job_name",
    "snapshot_url",
]
_CHANGE_EXPORT_FIELDS = [
    "change_id",
    "source_code",
    "source_name",
    "normalized_url_group",
    "from_snapshot_id",
    "to_snapshot_id",
    "from_capture_timestamp_utc",
    "to_capture_timestamp_utc",
    "from_job_id",
    "to_job_id",
    "change_type",
    "summary",
    "added_sections",
    "removed_sections",
    "changed_sections",
    "added_lines",
    "removed_lines",
    "change_ratio",
    "high_noise",
    "diff_truncated",
    "diff_version",
    "normalization_version",
    "computed_at_utc",
    "compare_url",
]


def _normalize_export_format(value: str | None) -> str:
    normalized = (value or "jsonl").strip().lower()
    if normalized == "ndjson":
        normalized = "jsonl"
    if normalized not in _EXPORT_FORMATS:
        raise HTTPException(
            status_code=422,
            detail="Unsupported export format; use jsonl or csv.",
        )
    return normalized


def _build_date_range(
    *,
    from_: date | None,
    to: date | None,
    dialect_name: str,
) -> tuple[datetime | None, datetime | None]:
    if from_ and to and from_ > to:
        raise HTTPException(
            status_code=422,
            detail="Invalid date range: 'from' must be <= 'to'.",
        )

    range_start: datetime | None = None
    range_end_exclusive: datetime | None = None
    if from_:
        range_start = datetime(from_.year, from_.month, from_.day, tzinfo=timezone.utc)
    if to:
        range_end_exclusive = datetime(to.year, to.month, to.day, tzinfo=timezone.utc) + timedelta(
            days=1
        )

    if dialect_name == "sqlite":
        if range_start is not None:
            range_start = range_start.replace(tzinfo=None)
        if range_end_exclusive is not None:
            range_end_exclusive = range_end_exclusive.replace(tzinfo=None)

    return range_start, range_end_exclusive


def _resolve_source_id(db: Session, source: str | None) -> Optional[int]:
    if not source:
        return None
    normalized = source.strip().lower()
    if not normalized or normalized in _PUBLIC_EXCLUDED_SOURCE_CODES:
        raise HTTPException(status_code=404, detail="Source not found")
    source_row = db.query(Source).filter(Source.code == normalized).first()
    if not source_row:
        raise HTTPException(status_code=404, detail="Source not found")
    return source_row.id


def _iter_snapshot_export_rows(
    *,
    db: Session,
    source_id: int | None,
    after_id: int | None,
    limit: int,
    range_start: datetime | None,
    range_end_exclusive: datetime | None,
    public_base: str,
) -> Iterable[dict[str, Any]]:
    query = (
        db.query(Snapshot, Source, ArchiveJob)
        .join(Source, Snapshot.source_id == Source.id, isouter=True)
        .join(ArchiveJob, Snapshot.job_id == ArchiveJob.id, isouter=True)
    )
    query = query.filter(
        or_(Source.code.is_(None), ~Source.code.in_(_PUBLIC_EXCLUDED_SOURCE_CODES))
    )
    if source_id is not None:
        query = query.filter(Snapshot.source_id == source_id)
    if after_id is not None:
        query = query.filter(Snapshot.id > after_id)
    if range_start is not None:
        query = query.filter(Snapshot.capture_timestamp >= range_start)
    if range_end_exclusive is not None:
        query = query.filter(Snapshot.capture_timestamp < range_end_exclusive)

    rows = query.order_by(Snapshot.id).limit(limit).yield_per(1000)

    for snapshot, source, job in rows:
        yield {
            "snapshot_id": snapshot.id,
            "source_code": source.code if source else None,
            "source_name": source.name if source else None,
            "captured_url": snapshot.url,
            "normalized_url_group": snapshot.normalized_url_group,
            "capture_timestamp_utc": _format_capture_timestamp(snapshot.capture_timestamp),
            "language": snapshot.language,
            "status_code": snapshot.status_code,
            "mime_type": snapshot.mime_type,
            "title": snapshot.title,
            "job_id": snapshot.job_id,
            "job_name": job.name if job else None,
            "snapshot_url": f"{public_base}/snapshot/{snapshot.id}",
        }


def _iter_change_export_rows(
    *,
    db: Session,
    source_id: int | None,
    after_id: int | None,
    limit: int,
    range_start: datetime | None,
    range_end_exclusive: datetime | None,
    public_base: str,
) -> Iterable[dict[str, Any]]:
    query = db.query(SnapshotChange, Source).join(
        Source, SnapshotChange.source_id == Source.id, isouter=True
    )
    query = query.filter(
        or_(Source.code.is_(None), ~Source.code.in_(_PUBLIC_EXCLUDED_SOURCE_CODES))
    )
    if source_id is not None:
        query = query.filter(SnapshotChange.source_id == source_id)
    if after_id is not None:
        query = query.filter(SnapshotChange.id > after_id)
    if range_start is not None:
        query = query.filter(SnapshotChange.to_capture_timestamp >= range_start)
    if range_end_exclusive is not None:
        query = query.filter(SnapshotChange.to_capture_timestamp < range_end_exclusive)

    rows = query.order_by(SnapshotChange.id).limit(limit).yield_per(1000)

    for change, source in rows:
        compare_url = f"{public_base}/compare?to={change.to_snapshot_id}"
        if change.from_snapshot_id is not None:
            compare_url = (
                f"{public_base}/compare?from={change.from_snapshot_id}&to={change.to_snapshot_id}"
            )
        yield {
            "change_id": change.id,
            "source_code": source.code if source else None,
            "source_name": source.name if source else None,
            "normalized_url_group": change.normalized_url_group,
            "from_snapshot_id": change.from_snapshot_id,
            "to_snapshot_id": change.to_snapshot_id,
            "from_capture_timestamp_utc": _format_capture_timestamp(change.from_capture_timestamp),
            "to_capture_timestamp_utc": _format_capture_timestamp(change.to_capture_timestamp),
            "from_job_id": change.from_job_id,
            "to_job_id": change.to_job_id,
            "change_type": change.change_type,
            "summary": change.summary,
            "added_sections": change.added_sections,
            "removed_sections": change.removed_sections,
            "changed_sections": change.changed_sections,
            "added_lines": change.added_lines,
            "removed_lines": change.removed_lines,
            "change_ratio": change.change_ratio,
            "high_noise": bool(change.high_noise),
            "diff_truncated": bool(change.diff_truncated),
            "diff_version": change.diff_version,
            "normalization_version": change.normalization_version,
            "computed_at_utc": _format_capture_timestamp(change.computed_at),
            "compare_url": compare_url,
        }


def _iter_jsonl(rows: Iterable[dict[str, Any]]) -> Iterator[bytes]:
    for row in rows:
        yield (json.dumps(row, ensure_ascii=False) + "\n").encode("utf-8")


def _iter_csv(rows: Iterable[dict[str, Any]], fieldnames: list[str]) -> Iterator[bytes]:
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=fieldnames, extrasaction="ignore")
    writer.writeheader()
    yield buffer.getvalue().encode("utf-8")
    buffer.seek(0)
    buffer.truncate(0)

    for row in rows:
        writer.writerow(row)
        yield buffer.getvalue().encode("utf-8")
        buffer.seek(0)
        buffer.truncate(0)


def _iter_gzip(chunks: Iterable[bytes]) -> Iterator[bytes]:
    buffer = io.BytesIO()
    gzipper = gzip.GzipFile(fileobj=buffer, mode="wb")
    for chunk in chunks:
        gzipper.write(chunk)
        gzipper.flush()
        data = buffer.getvalue()
        if data:
            yield data
            buffer.seek(0)
            buffer.truncate(0)
    gzipper.close()
    data = buffer.getvalue()
    if data:
        yield data


def _build_export_response(
    *,
    rows: Iterable[dict[str, Any]],
    export_format: str,
    filename_base: str,
    fieldnames: list[str],
    compressed: bool,
) -> StreamingResponse:
    content_type = _EXPORT_CONTENT_TYPES[export_format]
    if export_format == "jsonl":
        stream = _iter_jsonl(rows)
        filename = f"{filename_base}.jsonl"
    else:
        stream = _iter_csv(rows, fieldnames)
        filename = f"{filename_base}.csv"

    headers: dict[str, str] = {}
    if compressed:
        stream = _iter_gzip(stream)
        filename = f"{filename}.gz"
        headers["Content-Encoding"] = "gzip"

    headers["Content-Disposition"] = f'attachment; filename="{filename}"'
    return StreamingResponse(stream, media_type=content_type, headers=headers)


def _build_browse_url(
    job_id: Optional[int],
    original_url: str,
    capture_timestamp: Any = None,
    snapshot_id: Optional[int] = None,
) -> Optional[str]:
    base = get_replay_base_url()
    if not base or not job_id:
        return None

    normalized = original_url.strip()
    if not normalized:
        return None

    ts_value: Optional[str] = None
    if isinstance(capture_timestamp, datetime):
        dt = capture_timestamp
        if dt.tzinfo:
            dt = dt.astimezone(timezone.utc)
        else:
            dt = dt.replace(tzinfo=timezone.utc)
        ts_value = dt.strftime("%Y%m%d%H%M%S")

    # Do not append a trailing "/" here. If the original URL contains a query
    # string, adding "/" would modify it (because the browser would treat it as
    # part of the *outer* URL's query). pywb accepts the timegate form without
    # a trailing slash, eg:
    #   /job-1/https://example.com/path?x=y
    suffix = f"#ha_snapshot={snapshot_id}" if snapshot_id else ""
    if ts_value:
        return f"{base}/job-{job_id}/{ts_value}/{normalized}{suffix}"

    return f"{base}/job-{job_id}/{normalized}{suffix}"


def _normalize_url_group(value: str) -> Optional[str]:
    """
    Normalize a URL the same way Snapshot.normalized_url_group is computed.
    """
    return normalize_url_for_grouping(value)


_REPLAY_PREVIEW_FORMATS: tuple[tuple[str, str], ...] = (
    (".webp", "image/webp"),
    (".jpg", "image/jpeg"),
    (".jpeg", "image/jpeg"),
    (".png", "image/png"),
)


def _find_replay_preview_file(
    preview_dir: Path,
    source_code: str,
    job_id: int,
    *,
    lang: Optional[str] = None,
) -> Optional[tuple[Path, str]]:
    """
    Return the first matching preview file path + media type.

    We allow multiple formats so operators can migrate to more efficient image
    encodings without changing the public API contract.
    """
    base = f"source-{source_code}-job-{job_id}"
    normalized_lang = (lang or "").strip().lower()
    if normalized_lang in ("en", "fr"):
        localized_base = f"{base}-{normalized_lang}"
        for ext, media_type in _REPLAY_PREVIEW_FORMATS:
            candidate = preview_dir / f"{localized_base}{ext}"
            if candidate.exists():
                return candidate, media_type
    for ext, media_type in _REPLAY_PREVIEW_FORMATS:
        candidate = preview_dir / f"{base}{ext}"
        if candidate.exists():
            return candidate, media_type
    return None


def _strip_url_fragment(value: str) -> str:
    trimmed = value.strip()
    hash_idx = trimmed.find("#")
    if hash_idx == -1:
        return trimmed
    return trimmed[:hash_idx]


def _strip_url_query_and_fragment(value: str) -> str:
    trimmed = value.strip()
    if not trimmed:
        return ""
    q_idx = trimmed.find("?")
    hash_idx = trimmed.find("#")
    cut = len(trimmed)
    if q_idx != -1:
        cut = min(cut, q_idx)
    if hash_idx != -1:
        cut = min(cut, hash_idx)
    return trimmed[:cut]


def _escape_like(value: str) -> str:
    """
    Escape LIKE wildcards so user input is treated as a literal substring.
    """
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


_URL_QUERY_PREFIX_RE = re.compile(r"^url:\s*", re.IGNORECASE)
_URL_SCHEME_RE = re.compile(r"^[a-z][a-z0-9+.-]*://", re.IGNORECASE)


def _looks_like_url_query(value: str) -> bool:
    raw = value.strip()
    if not raw:
        return False
    if " " in raw or "\n" in raw or "\t" in raw:
        return False
    if "://" in raw:
        return True

    lower = raw.lower()
    if lower.startswith("www."):
        return True

    if "/" in raw:
        head = raw.split("/", 1)[0]
        return "." in head

    return "." in raw


def _expand_url_search_variants(normalized_url: str) -> list[str]:
    """
    Expand a normalized URL into a small set of commonly equivalent variants.

    This is intentionally conservative: we currently only vary scheme (http/https)
    and the presence of a leading "www." hostname.
    """
    try:
        parts = urlsplit(normalized_url)
    except Exception:
        return [normalized_url]

    scheme = parts.scheme.lower()
    netloc = parts.netloc.lower()
    path = parts.path or "/"

    scheme_variants = {scheme}
    if scheme == "https":
        scheme_variants.add("http")
    elif scheme == "http":
        scheme_variants.add("https")

    host, sep, port = netloc.partition(":")
    host_variants = {host}
    if host.startswith("www."):
        host_variants.add(host[len("www.") :])
    else:
        host_variants.add(f"www.{host}")

    suffix = f"{sep}{port}" if port else ""
    netloc_variants = {f"{h}{suffix}" for h in host_variants if h}

    urls: set[str] = set()
    for scheme_value in scheme_variants:
        for netloc_value in netloc_variants:
            urls.add(urlunsplit((scheme_value, netloc_value, path, "", "")))
    return sorted(urls)


def _extract_url_search_targets(q_clean: str) -> list[str] | None:
    raw = q_clean.strip()
    if not raw:
        return None

    explicit = False
    m = _URL_QUERY_PREFIX_RE.match(raw)
    if m:
        explicit = True
        raw = raw[m.end() :].strip()

    if not raw:
        return None

    # The `url:` prefix is also supported by boolean/field search (as a field
    # selector). To avoid misclassifying URL-field substring queries like
    # `url:covid19.html` as an *exact* URL lookup (host=`covid19.html`),
    # only treat `url:` as a URL-lookup hint when the remainder looks like a
    # real URL (scheme or leading "www."). Otherwise, fall through so the
    # boolean query parser can handle `url:` as a field prefix.
    if explicit:
        lowered = raw.lower()
        if '"' in raw or " " in raw:
            return None
        if not (
            lowered.startswith("http://")
            or lowered.startswith("https://")
            or lowered.startswith("www.")
        ):
            return None

    if not _looks_like_url_query(raw):
        return None

    candidate = raw
    if not _URL_SCHEME_RE.match(candidate):
        candidate = f"https://{candidate.lstrip('/')}"

    normalized = normalize_url_for_grouping(candidate)
    if not normalized:
        stripped = _strip_url_query_and_fragment(candidate)
        if not _URL_SCHEME_RE.match(stripped):
            stripped = f"https://{stripped.lstrip('/')}"
        normalized = normalize_url_for_grouping(stripped)

    if not normalized:
        return None

    return _expand_url_search_variants(normalized)


def _parse_timestamp14(value: str) -> Optional[datetime]:
    raw = value.strip()
    if len(raw) != 14 or not raw.isdigit():
        return None
    try:
        year = int(raw[0:4])
        month = int(raw[4:6])
        day = int(raw[6:8])
        hour = int(raw[8:10])
        minute = int(raw[10:12])
        second = int(raw[12:14])
        return datetime(year, month, day, hour, minute, second, tzinfo=timezone.utc)
    except ValueError:
        return None


def _candidate_resolve_urls(original_url: str) -> List[str]:
    cleaned = _strip_url_fragment(original_url)
    if not cleaned:
        return []

    seeded = cleaned
    if not (seeded.startswith("http://") or seeded.startswith("https://")):
        seeded = f"https://{seeded}"

    try:
        parts = urlsplit(seeded)
    except Exception:
        return [seeded]

    scheme = parts.scheme.lower() if parts.scheme else "https"
    netloc = parts.netloc.lower()
    path = parts.path
    query = parts.query

    host, sep, port = netloc.partition(":")
    if not host:
        return [seeded]

    scheme_variants = {scheme}
    if scheme == "https":
        scheme_variants.add("http")
    elif scheme == "http":
        scheme_variants.add("https")

    host_variants = {host}
    if host.startswith("www."):
        host_variants.add(host[len("www.") :])
    else:
        host_variants.add(f"www.{host}")

    if path == "":
        path_variants = {"", "/"}
    elif path == "/":
        path_variants = {"/", ""}
    else:
        path_variants = {path}
        if path.endswith("/"):
            path_variants.add(path.rstrip("/"))
        else:
            path_variants.add(f"{path}/")

    candidates: set[str] = set()
    for scheme_value in scheme_variants:
        for host_value in host_variants:
            netloc_value = f"{host_value}{sep}{port}" if port else host_value
            for path_value in path_variants:
                candidates.add(urlunsplit((scheme_value, netloc_value, path_value, query, "")))

    return sorted(candidates)


def _select_best_replay_candidate(
    rows: Iterable[Sequence[Any]],
    anchor: Optional[datetime],
) -> Optional[tuple[int, str, Any, Optional[int], Optional[str]]]:
    best: Optional[tuple[int, str, Any, Optional[int], Optional[str]]] = None
    best_key: Optional[tuple] = None

    anchor_ts = anchor.timestamp() if anchor else None

    for snap_id, snap_url, capture_ts, status_code, mime_type in rows:
        quality = _status_quality(status_code)

        ts_value = 0.0
        if isinstance(capture_ts, datetime):
            dt = capture_ts
            if dt.tzinfo:
                dt = dt.astimezone(timezone.utc)
            else:
                dt = dt.replace(tzinfo=timezone.utc)
            ts_value = dt.timestamp()

        diff = abs(ts_value - anchor_ts) if anchor_ts is not None else 0.0

        key = (quality, -diff, ts_value, snap_id)
        if best_key is None or key > best_key:
            best_key = key
            best = (snap_id, snap_url, capture_ts, status_code, mime_type)

    return best


def _candidate_entry_groups(base_url: Optional[str]) -> List[str]:
    """
    Build a small set of normalized_url_group candidates for a Source.base_url.

    We include common scheme and www/no-www variants because archived URLs may
    differ slightly from the configured base URL.
    """
    if not base_url:
        return []

    canonical = _normalize_url_group(base_url)
    if not canonical:
        return []

    parts = urlsplit(canonical)
    scheme = parts.scheme
    netloc = parts.netloc
    path = parts.path or "/"

    host, sep, port = netloc.partition(":")
    if not host:
        return [canonical]

    scheme_variants = {scheme}
    if scheme == "https":
        scheme_variants.add("http")
    elif scheme == "http":
        scheme_variants.add("https")

    host_variants = {host}
    if host.startswith("www."):
        host_variants.add(host[len("www.") :])
    else:
        host_variants.add(f"www.{host}")

    candidates: set[str] = set()
    for scheme_value in scheme_variants:
        for host_value in host_variants:
            netloc_value = f"{host_value}{sep}{port}" if port else host_value
            candidates.add(urlunsplit((scheme_value, netloc_value, path, "", "")))

    return sorted(candidates)


def _candidate_entry_hosts(base_url: Optional[str]) -> List[str]:
    """
    Return hostname variants (www/no-www) for a Source.base_url.
    """
    if not base_url:
        return []

    raw = base_url.strip()
    if not raw:
        return []
    if not (raw.startswith("http://") or raw.startswith("https://")):
        raw = f"https://{raw}"

    try:
        parts = urlsplit(raw)
    except Exception:
        return []

    netloc = (parts.netloc or "").lower()
    host = netloc.partition(":")[0]
    if not host:
        return []

    variants = {host}
    if host.startswith("www."):
        variants.add(host[len("www.") :])
    else:
        variants.add(f"www.{host}")

    return sorted(variants)


def _status_quality(status_code: Optional[int]) -> int:
    if status_code is None:
        return 0
    if 200 <= status_code < 300:
        return 2
    if 300 <= status_code < 400:
        return 1
    return -1


def _entry_candidate_key(
    *,
    snapshot_id: int,
    url: str,
    capture_timestamp: Any,
    status_code: Optional[int],
) -> tuple:
    """
    Sort key for choosing an entry-point page for a source when the configured
    baseUrl wasn't captured exactly.
    """
    quality = _status_quality(status_code)

    try:
        parts = urlsplit(url)
        path = parts.path or "/"
        has_query = 1 if parts.query else 0
    except Exception:
        path = "/"
        has_query = 0

    is_root = 1 if path in ("", "/") else 0
    depth = 0 if is_root else path.strip("/").count("/") + 1
    path_len = len(path)

    ts_score = 0.0
    if isinstance(capture_timestamp, datetime):
        dt = capture_timestamp
        if dt.tzinfo:
            dt = dt.astimezone(timezone.utc)
        else:
            dt = dt.replace(tzinfo=timezone.utc)
        ts_score = dt.timestamp()

    # Prefer: 2xx > 3xx > None > other, root-like pages, shallower/shorter
    # paths, no query strings, and finally newer captures.
    return (
        quality,
        is_root,
        -depth,
        -path_len,
        -has_query,
        ts_score,
        snapshot_id,
    )


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
    sort: SearchSort | None,
    view: SearchView | None,
    includeNon2xx: bool,
    includeDuplicates: bool,
    from_date: date | None,
    to_date: date | None,
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
        - "relevance_fuzzy"
        - "boolean"
        - "url"
    """
    raw_q = q.strip() if q else None
    if raw_q == "":
        raw_q = None

    url_search_targets: list[str] | None = _extract_url_search_targets(raw_q) if raw_q else None
    boolean_query: BoolNode | None = None

    q_filter = raw_q
    q_rank = raw_q
    phrase_query = raw_q
    if url_search_targets:
        q_filter = None
        q_rank = None
        phrase_query = None
    elif raw_q and looks_like_advanced_query(raw_q):
        try:
            boolean_query = parse_query(raw_q)
        except QueryParseError:
            boolean_query = None
        else:
            q_filter = None
            phrase_query = None
            positive_terms = [t.text for t in iter_positive_terms(boolean_query) if t.text]
            q_rank = " ".join(positive_terms).strip() or None

    effective_sort = sort
    if effective_sort is None:
        effective_sort = SearchSort.relevance if (q_filter or q_rank) else SearchSort.newest
    if effective_sort == SearchSort.relevance and not (q_filter or q_rank):
        effective_sort = SearchSort.newest

    effective_view = view or SearchView.snapshots

    dialect_name = db.get_bind().dialect.name
    use_postgres_fts = dialect_name == "postgresql"

    if from_date and to_date and from_date > to_date:
        raise HTTPException(
            status_code=422,
            detail="Invalid date range: 'from' must be <= 'to'.",
        )

    range_start: datetime | None = None
    range_end_exclusive: datetime | None = None
    if from_date:
        range_start = datetime(from_date.year, from_date.month, from_date.day, tzinfo=timezone.utc)
    if to_date:
        range_end_exclusive = datetime(
            to_date.year, to_date.month, to_date.day, tzinfo=timezone.utc
        ) + timedelta(days=1)

    # SQLite often round-trips timezone-aware datetimes as naive. Use naive bounds
    # for consistent filtering in tests/dev when running on SQLite.
    if dialect_name == "sqlite":
        if range_start is not None:
            range_start = range_start.replace(tzinfo=None)
        if range_end_exclusive is not None:
            range_end_exclusive = range_end_exclusive.replace(tzinfo=None)

    rank_text = q_filter or q_rank

    ranking_version = get_ranking_version(ranking)
    # For v2/v3 we use different blends depending on query "intent".
    query_mode = None
    query_tokens: list[str] = []
    ranking_cfg = None
    if ranking_version in (RankingVersion.v2, RankingVersion.v3) and rank_text:
        query_mode = classify_query_mode(rank_text)
        ranking_cfg = get_ranking_config(mode=query_mode, version=ranking_version)
        query_tokens = tokenize_query(rank_text)

    match_tokens: list[str] = []
    if rank_text:
        match_tokens = [t for t in tokenize_query(rank_text) if len(t) >= 3]
        if not match_tokens:
            match_tokens = [rank_text]

    base_query = db.query(Snapshot).join(Source)
    base_query = base_query.filter(~Source.code.in_(_PUBLIC_EXCLUDED_SOURCE_CODES))

    if source:
        base_query = base_query.filter(Source.code == source.lower())

    if range_start is not None:
        base_query = base_query.filter(Snapshot.capture_timestamp >= range_start)
    if range_end_exclusive is not None:
        base_query = base_query.filter(Snapshot.capture_timestamp < range_end_exclusive)

    if not includeNon2xx:
        base_query = base_query.filter(
            or_(
                Snapshot.status_code.is_(None),
                and_(
                    Snapshot.status_code >= 200,
                    Snapshot.status_code < 300,
                ),
            )
        )

    offset = (page - 1) * pageSize

    # Fast path: when browsing pages without a search query or date range,
    # prefer the Page table (if present) to avoid window functions over the
    # full Snapshot table.
    if (
        effective_view == SearchView.pages
        and raw_q is None
        and range_start is None
        and range_end_exclusive is None
        and get_pages_fastpath_enabled()
        and _has_table(db, "pages")
    ):
        selected_snapshot_id = (
            Page.latest_snapshot_id if includeNon2xx else Page.latest_ok_snapshot_id
        )

        page_query = (
            db.query(Snapshot)
            .join(
                Page,
                and_(
                    Snapshot.id == selected_snapshot_id,
                    Snapshot.source_id == Page.source_id,
                ),
            )
            .join(Source)
            .filter(~Source.code.in_(_PUBLIC_EXCLUDED_SOURCE_CODES))
        )
        if source:
            page_query = page_query.filter(Source.code == source.lower())

        total = page_query.with_entities(func.count(Page.id)).scalar() or 0
        if total > 0:
            status_quality = case(
                (Snapshot.status_code.is_(None), 0),
                (and_(Snapshot.status_code >= 200, Snapshot.status_code < 300), 2),
                (and_(Snapshot.status_code >= 300, Snapshot.status_code < 400), 1),
                else_=-1,
            )

            items = (
                page_query.options(
                    load_only(
                        Snapshot.id,
                        Snapshot.job_id,
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
                )
                .order_by(
                    status_quality.desc(),
                    Snapshot.capture_timestamp.desc(),
                    Snapshot.id.desc(),
                )
                .offset(offset)
                .limit(pageSize)
                .all()
            )

            page_counts_by_snapshot_id: dict[int, int] = {}
            snapshot_ids = [s.id for s in items]
            if snapshot_ids:
                rows = (
                    db.query(selected_snapshot_id.label("snapshot_id"), Page.snapshot_count)
                    .filter(selected_snapshot_id.isnot(None))
                    .filter(selected_snapshot_id.in_(snapshot_ids))
                    .all()
                )
                page_counts_by_snapshot_id = {
                    int(snapshot_id): int(count)
                    for snapshot_id, count in rows
                    if snapshot_id is not None and count is not None
                }

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

                original_url = (
                    snap.normalized_url_group
                    or normalize_url_for_grouping(snap.url)
                    or _strip_url_query_and_fragment(snap.url)
                )

                results.append(
                    SnapshotSummarySchema(
                        id=snap.id,
                        title=snap.title,
                        sourceCode=source_obj.code,
                        sourceName=source_obj.name,
                        language=snap.language,
                        captureDate=capture_date,
                        captureTimestamp=_format_capture_timestamp(snap.capture_timestamp),
                        jobId=snap.job_id,
                        originalUrl=original_url,
                        snippet=snap.snippet,
                        pageSnapshotsCount=page_counts_by_snapshot_id.get(snap.id),
                        rawSnapshotUrl=f"/api/snapshots/raw/{snap.id}",
                        browseUrl=_build_browse_url(
                            snap.job_id, original_url, snap.capture_timestamp, snap.id
                        ),
                    )
                )

            return (
                SearchResponseSchema(
                    results=results,
                    total=int(total),
                    page=page,
                    pageSize=pageSize,
                ),
                "pages_fastpath",
            )

    def strip_query_fragment_expr(url_expr: Any) -> Any:
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

    def capture_date_expr(ts_expr: Any) -> Any:
        if dialect_name == "postgresql":
            return func.date(func.timezone("UTC", ts_expr))
        if dialect_name == "sqlite":
            return func.date(ts_expr)
        return func.date(ts_expr)

    group_key = func.coalesce(
        Snapshot.normalized_url_group,
        strip_query_fragment_expr(Snapshot.url),
    )
    page_partition_key = (Snapshot.source_id, group_key)

    pages_url_length = func.length(group_key)

    def compute_total(query: Any) -> int:
        if effective_view == SearchView.pages:
            distinct_pages = (
                query.with_entities(
                    Snapshot.source_id.label("source_id"),
                    group_key.label("group_key"),
                )
                .distinct()
                .subquery()
            )
            return db.query(func.count()).select_from(distinct_pages).scalar() or 0
        if effective_view == SearchView.snapshots and not includeDuplicates:
            capture_day = capture_date_expr(Snapshot.capture_timestamp).label("capture_day")
            content_key = cast(
                func.coalesce(Snapshot.content_hash, cast(Snapshot.id, String)), String
            ).label("content_key")
            distinct_items = (
                query.with_entities(
                    Snapshot.source_id.label("source_id"),
                    Snapshot.url.label("url"),
                    content_key,
                    capture_day,
                )
                .distinct()
                .subquery()
            )
            return db.query(func.count()).select_from(distinct_items).scalar() or 0
        return query.with_entities(func.count(Snapshot.id)).scalar() or 0

    def apply_snapshot_dedup(query: Any) -> Any:
        capture_day = capture_date_expr(Snapshot.capture_timestamp)
        content_key = cast(
            func.coalesce(Snapshot.content_hash, cast(Snapshot.id, String)),
            String,
        )
        partition_key = (Snapshot.source_id, Snapshot.url, content_key, capture_day)
        row_number = (
            func.row_number()
            .over(
                partition_by=partition_key,
                order_by=(Snapshot.capture_timestamp.desc(), Snapshot.id.desc()),
            )
            .label("rn")
        )
        dedup_subq = query.with_entities(
            Snapshot.id.label("id"),
            row_number,
        ).subquery()
        return (
            db.query(Snapshot)
            .join(dedup_subq, Snapshot.id == dedup_subq.c.id)
            .filter(dedup_subq.c.rn == 1)
        )

    query = base_query
    tsquery = None
    vector_expr = None
    score_override: Any | None = None
    search_mode: str | None = None

    def apply_substring_filter(qry: Any) -> Any:
        tokens = match_tokens[:8]
        token_filters = []
        for token in tokens:
            pattern = f"%{token}%"
            token_filters.append(
                or_(
                    Snapshot.title.ilike(pattern),
                    Snapshot.snippet.ilike(pattern),
                    Snapshot.url.ilike(pattern),
                )
            )
        return qry.filter(and_(*token_filters)) if token_filters else qry

    def apply_fts_filter(qry: Any) -> Any:
        nonlocal tsquery, vector_expr
        if q_filter is None:
            raise ValueError("apply_fts_filter called without q_filter")
        tsquery = func.websearch_to_tsquery(TS_CONFIG, q_filter)
        computed_vector = build_search_vector(Snapshot.title, Snapshot.snippet, Snapshot.url)

        # Filter using the indexed column where possible so Postgres can use the
        # `ix_snapshots_search_vector` GIN index; only fall back to an on-the-fly
        # computed vector for rows that are missing the cached value.
        vector_expr = func.coalesce(Snapshot.search_vector, computed_vector)
        fts_filter = or_(
            Snapshot.search_vector.op("@@")(tsquery),
            and_(Snapshot.search_vector.is_(None), computed_vector.op("@@")(tsquery)),
        )
        return qry.filter(fts_filter)

    def apply_fuzzy_filter(qry: Any) -> Any:
        nonlocal score_override
        if q_filter is None:
            raise ValueError("apply_fuzzy_filter called without q_filter")
        if not _has_pg_trgm(db):
            return qry.filter(text("0=1"))

        # For misspellings we want word-level matching ("coronovirus" should match
        # "Coronavirus disease ...") without lowering the global similarity
        # threshold enough to create enormous candidate sets.
        #
        # pg_trgm provides a word-similarity operator (<%) which compares against
        # the best-matching word/substring instead of the entire field.
        tokens = [t for t in match_tokens if t][:4]
        if not tokens:
            return qry.filter(text("0=1"))

        if dialect_name == "postgresql":
            threshold = pick_word_similarity_threshold(tokens)
            db.execute(text(f"SET LOCAL pg_trgm.word_similarity_threshold = {threshold:.2f}"))

        title_expr = func.coalesce(Snapshot.title, "")
        url_expr = Snapshot.url

        per_token_title_scores = []
        for token in tokens:
            variants = token_variants(token)
            per_token_title_scores.append(
                func.greatest(*(func.word_similarity(title_expr, v) for v in variants))
            )
        title_score = sum(per_token_title_scores, 0.0) / float(len(per_token_title_scores))

        url_tokens = [t for t in tokens if should_use_url_similarity(t)]
        if url_tokens:
            url_scores = [func.similarity(url_expr, t) for t in url_tokens]
            url_score = sum(url_scores, 0.0) / float(len(url_scores))
            score_override = func.greatest(title_score, 0.8 * url_score)
        else:
            score_override = title_score

        # Candidate filter: AND across tokens, OR across fields.
        # Use raw columns so trigram GIN indexes can be used.
        title_candidate = Snapshot.title
        token_filters = []
        for token in tokens:
            variants = token_variants(token)
            title_match = or_(*(title_candidate.op("<%")(v) for v in variants))
            if should_use_url_similarity(token):
                token_filters.append(or_(title_match, url_expr.op("%")(token)))
            else:
                token_filters.append(title_match)

        return qry.filter(and_(*token_filters))

    if url_search_targets:
        query = query.filter(group_key.in_(url_search_targets))
        total = compute_total(query)
        search_mode = "url"
    elif boolean_query:

        def build_term_expr(term: BoolTerm) -> Any:
            text_value = term.text.strip()
            if not text_value:
                return text("1=1")
            escaped = _escape_like(text_value)
            pattern = f"%{escaped}%"

            title_expr = func.coalesce(Snapshot.title, "")
            snippet_expr = func.coalesce(Snapshot.snippet, "")
            url_expr = Snapshot.url
            group_expr = func.coalesce(Snapshot.normalized_url_group, "")

            def match(expr: Any) -> Any:
                return expr.ilike(pattern, escape="\\")

            if term.field == "title":
                return match(title_expr)
            if term.field == "snippet":
                return match(snippet_expr)
            if term.field == "url":
                return or_(match(url_expr), match(group_expr))
            return or_(match(title_expr), match(snippet_expr), match(url_expr))

        def build_expr(node: BoolNode) -> Any:
            if isinstance(node, BoolTerm):
                return build_term_expr(node)
            if isinstance(node, BoolNot):
                return ~build_expr(node.child)
            if isinstance(node, BoolAnd):
                return and_(*(build_expr(c) for c in node.children))
            if isinstance(node, BoolOr):
                return or_(*(build_expr(c) for c in node.children))
            return text("1=1")

        query = query.filter(build_expr(boolean_query))
        total = compute_total(query)
        search_mode = "boolean"
    elif q_filter:
        # Prefer Postgres FTS for relevance ordering, but fall back to substring
        # matching (and then fuzzy matching) when FTS yields no results.
        if use_postgres_fts and effective_sort == SearchSort.relevance:
            query = apply_fts_filter(query)
            total = compute_total(query)
            search_mode = "relevance_fts"

            if total == 0:
                tsquery = None
                vector_expr = None
                score_override = None
                query = apply_substring_filter(base_query)
                total = compute_total(query)
                search_mode = "relevance_fallback"

                if total == 0 and len(q_filter) >= 4 and _has_pg_trgm(db):
                    query = apply_fuzzy_filter(base_query)
                    total = compute_total(query)
                    search_mode = "relevance_fuzzy" if total > 0 else search_mode
        else:
            query = apply_substring_filter(query)
            total = compute_total(query)
            search_mode = (
                "relevance_fallback" if effective_sort == SearchSort.relevance else "newest"
            )

            if total == 0 and use_postgres_fts and len(q_filter) >= 4 and _has_pg_trgm(db):
                score_override = None
                query = apply_fuzzy_filter(base_query)
                total = compute_total(query)
                if total > 0:
                    search_mode = (
                        "relevance_fuzzy"
                        if effective_sort == SearchSort.relevance
                        else "newest_fuzzy"
                    )
    else:
        total = compute_total(query)

    if effective_view == SearchView.snapshots and not includeDuplicates:
        query = apply_snapshot_dedup(query)

    mode = search_mode or "newest"
    if ranking_version == RankingVersion.v2 and mode.startswith("relevance"):
        mode = f"{mode}_v2"

    status_quality = case(
        (Snapshot.status_code.is_(None), 0),
        (and_(Snapshot.status_code >= 200, Snapshot.status_code < 300), 2),
        (and_(Snapshot.status_code >= 300, Snapshot.status_code < 400), 1),
        else_=-1,
    )

    use_page_signals = (
        effective_sort == SearchSort.relevance
        and rank_text is not None
        and score_override is None
        and _has_table(db, "page_signals")
    )
    use_authority = use_page_signals

    has_ps_outlink_count = use_page_signals and _has_column(db, "page_signals", "outlink_count")
    has_ps_pagerank = use_page_signals and _has_column(db, "page_signals", "pagerank")

    use_hubness = (
        ranking_version in (RankingVersion.v2, RankingVersion.v3)
        and ranking_cfg is not None
        and query_mode == QueryMode.broad
        and effective_sort == SearchSort.relevance
        and rank_text is not None
        and has_ps_outlink_count
    )

    inlink_count = None
    if use_authority:
        inlink_count = func.coalesce(PageSignal.inlink_count, 0)

    use_pagerank = (
        ranking_version in (RankingVersion.v2, RankingVersion.v3)
        and ranking_cfg is not None
        and query_mode == QueryMode.broad
        and effective_sort == SearchSort.relevance
        and rank_text is not None
        and has_ps_pagerank
    )

    outlink_count = None
    if use_hubness:
        outlink_count = func.coalesce(PageSignal.outlink_count, 0)

    pagerank_value = None
    if use_pagerank:
        pagerank_value = func.coalesce(PageSignal.pagerank, 0.0)

    def build_authority_expr() -> Any:
        if inlink_count is None:
            return 0.0
        if ranking_version in (RankingVersion.v2, RankingVersion.v3) and ranking_cfg is not None:
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
        if ranking_version in (RankingVersion.v2, RankingVersion.v3) and ranking_cfg is not None:
            return float(ranking_cfg.depth_coef) * slash_count
        return (-0.01) * slash_count

    # Check if is_archived column exists for v3 ranking.
    has_is_archived = _has_column(db, "snapshots", "is_archived")

    def build_archived_penalty() -> Any:
        if ranking_version not in (RankingVersion.v2, RankingVersion.v3) or ranking_cfg is None:
            return 0.0
        if ranking_cfg.archived_penalty == 0:
            return 0.0

        # v3: Use is_archived column if available, with fallback to heuristics.
        if ranking_version == RankingVersion.v3 and has_is_archived and use_postgres_fts:
            snippet_text = func.coalesce(Snapshot.snippet, "")
            fallback_match = or_(
                Snapshot.title.ilike("archived%"),
                snippet_text.ilike("%we have archived this page%"),
                snippet_text.ilike("%this page has been archived%"),
                snippet_text.ilike("%cette page a été archivée%"),
            )
            return case(
                # is_archived = True -> apply penalty.
                (Snapshot.is_archived == True, float(ranking_cfg.archived_penalty)),  # noqa: E712
                # is_archived = False -> no penalty.
                (Snapshot.is_archived == False, 0.0),  # noqa: E712
                # is_archived IS NULL -> fall back to heuristics.
                (fallback_match, float(ranking_cfg.archived_penalty)),
                else_=0.0,
            )

        # v2 / fallback: Canada.ca often marks pages as archived via title prefixes
        # or a banner in the rendered HTML that ends up in our snippet extraction.
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
        if not rank_text:
            return 0.0
        if (
            ranking_version not in (RankingVersion.v2, RankingVersion.v3)
            or not query_tokens
            or ranking_cfg is None
        ):
            return case(
                (Snapshot.title.ilike(f"%{rank_text}%"), 0.2),
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

    def build_title_exact_match_boost() -> Any:
        """v3: Bonus when query appears exactly as substring in title."""
        if ranking_version != RankingVersion.v3 or ranking_cfg is None or not rank_text:
            return 0.0
        if ranking_cfg.title_exact_match_boost == 0:
            return 0.0
        return case(
            (Snapshot.title.ilike(f"%{rank_text}%"), float(ranking_cfg.title_exact_match_boost)),
            else_=0.0,
        )

    def build_recency_boost() -> Any:
        """v3: Boost recent snapshots for broad/mixed queries."""
        if ranking_version != RankingVersion.v3 or ranking_cfg is None:
            return 0.0
        if ranking_cfg.recency_coef == 0:
            return 0.0
        if not use_postgres_fts:
            return 0.0  # Recency boost only on Postgres.

        # Calculate days since capture (use current_date for reference).
        days_ago = (
            func.extract("epoch", func.current_date() - func.date(Snapshot.capture_timestamp))
            / 86400.0
        )
        # Clamp to minimum of 1 day.
        days_ago_clamped = case(
            (days_ago < 1, 1.0),
            else_=days_ago,
        )
        # Logarithmic decay: ln(1 + 365 / days_ago).
        recency_score = func.ln(1.0 + 365.0 / days_ago_clamped)
        return float(ranking_cfg.recency_coef) * recency_score

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
        if effective_sort != SearchSort.relevance or not rank_text:
            return None
        if score_override is not None:
            score = score_override
            if (
                ranking_version in (RankingVersion.v2, RankingVersion.v3)
                and ranking_cfg is not None
            ):
                score = score + build_archived_penalty() + build_depth_penalty(group_key)
                if use_authority and inlink_count is not None:
                    score = score + build_authority_expr()
                if use_hubness and outlink_count is not None:
                    score = score + build_hubness_expr()
                if use_pagerank and pagerank_value is not None:
                    score = score + build_pagerank_expr()
                # v3 additions.
                if ranking_version == RankingVersion.v3:
                    score = score + build_title_exact_match_boost() + build_recency_boost()
            else:
                if use_authority and inlink_count is not None:
                    score = score + build_authority_expr()
            return score
        if use_postgres_fts and tsquery is not None and vector_expr is not None:
            if (
                ranking_version in (RankingVersion.v2, RankingVersion.v3)
                and query_mode is not None
                and query_mode != QueryMode.specific
            ):
                rank = func.ts_rank_cd(vector_expr, tsquery, 32)
            else:
                rank = func.ts_rank_cd(vector_expr, tsquery)
            depth_basis = (
                group_key
                if (
                    ranking_version in (RankingVersion.v2, RankingVersion.v3)
                    and ranking_cfg is not None
                )
                else Snapshot.url
            )
            url_penalty_basis = (
                group_key
                if (
                    ranking_version in (RankingVersion.v2, RankingVersion.v3)
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
            # v3 additions.
            if ranking_version == RankingVersion.v3:
                score = score + build_title_exact_match_boost() + build_recency_boost()
            return score

        # DB-agnostic fallback: score by field match presence.
        tokens = match_tokens[:8]
        title_hits = sum(
            (case((Snapshot.title.ilike(f"%{t}%"), 1), else_=0) for t in tokens),
            0,
        )
        url_hits = sum(
            (case((Snapshot.url.ilike(f"%{t}%"), 1), else_=0) for t in tokens),
            0,
        )
        snippet_hits = sum(
            (case((Snapshot.snippet.ilike(f"%{t}%"), 1), else_=0) for t in tokens),
            0,
        )
        phrase_boost = (
            case((Snapshot.title.ilike(f"%{phrase_query}%"), 2), else_=0) if phrase_query else 0
        )
        score = 3 * title_hits + 2 * url_hits + snippet_hits + phrase_boost

        if ranking_version in (RankingVersion.v2, RankingVersion.v3) and ranking_cfg is not None:
            score = score + build_archived_penalty() + build_depth_penalty(group_key)
            if use_authority and inlink_count is not None:
                score = score + build_authority_expr()
            if use_hubness and outlink_count is not None:
                score = score + build_hubness_expr()
            if use_pagerank and pagerank_value is not None:
                score = score + build_pagerank_expr()
            # v3 additions.
            if ranking_version == RankingVersion.v3:
                score = score + build_title_exact_match_boost() + build_recency_boost()
        else:
            if use_authority and inlink_count is not None:
                score = score + build_authority_expr()
        return score

    snapshot_score = build_snapshot_score()

    def build_item_query_for_pages_v1() -> Any:
        row_number = (
            func.row_number()
            .over(
                partition_by=page_partition_key,
                order_by=(Snapshot.capture_timestamp.desc(), Snapshot.id.desc()),
            )
            .label("rn")
        )
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

        row_number = (
            func.row_number()
            .over(
                partition_by=page_partition_key,
                order_by=(Snapshot.capture_timestamp.desc(), Snapshot.id.desc()),
            )
            .label("rn")
        )
        candidates_query = query
        if use_page_signals:
            candidates_query = candidates_query.outerjoin(
                PageSignal, PageSignal.normalized_url_group == group_key
            )

        candidates_subq = candidates_query.with_entities(
            Snapshot.id.label("id"),
            Snapshot.source_id.label("source_id"),
            group_key.label("group_key"),
            Snapshot.capture_timestamp.label("capture_timestamp"),
            row_number,
            snapshot_score.label("snapshot_score"),
        ).subquery()

        group_scores_subq = (
            db.query(
                candidates_subq.c.source_id.label("source_id"),
                candidates_subq.c.group_key.label("group_key"),
                func.max(candidates_subq.c.snapshot_score).label("group_score"),
            )
            .group_by(candidates_subq.c.source_id, candidates_subq.c.group_key)
            .subquery()
        )

        latest_ids_subq = db.query(
            candidates_subq.c.id.label("id"),
            candidates_subq.c.source_id.label("source_id"),
            candidates_subq.c.group_key.label("group_key"),
            candidates_subq.c.rn.label("rn"),
        ).subquery()

        return (
            db.query(Snapshot)
            .join(latest_ids_subq, Snapshot.id == latest_ids_subq.c.id)
            .join(
                group_scores_subq,
                and_(
                    group_scores_subq.c.source_id == latest_ids_subq.c.source_id,
                    group_scores_subq.c.group_key == latest_ids_subq.c.group_key,
                ),
            )
            .filter(latest_ids_subq.c.rn == 1)
            .order_by(
                status_quality.desc(),
                group_scores_subq.c.group_score.desc(),
                func.length(group_scores_subq.c.group_key).asc(),
                Snapshot.capture_timestamp.desc(),
                Snapshot.id.desc(),
            )
        )

    ordered = query
    if effective_view == SearchView.pages:
        if (
            ranking_version == RankingVersion.v2
            and effective_sort == SearchSort.relevance
            and rank_text
            and score_override is None
        ):
            ordered = build_item_query_for_pages_v2()
        else:
            item_query = build_item_query_for_pages_v1()
            if use_page_signals:
                item_query = item_query.outerjoin(
                    PageSignal, PageSignal.normalized_url_group == group_key
                )

            if effective_sort == SearchSort.relevance and rank_text:
                rank_score = snapshot_score if snapshot_score is not None else literal(0.0)
                ordered = item_query.order_by(
                    status_quality.desc(),
                    rank_score.desc(),
                    pages_url_length.asc(),
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
        if effective_sort == SearchSort.relevance and rank_text:
            rank_score = snapshot_score if snapshot_score is not None else literal(0.0)
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
                Snapshot.job_id,
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
        )
        .offset(offset)
        .limit(pageSize)
        .all()
    )

    search_results: List[SnapshotSummarySchema] = []
    page_counts_by_key: dict[tuple[int, str], int] = {}
    if effective_view == SearchView.pages and _has_table(db, "pages"):
        pairs: set[tuple[int, str]] = set()
        for snap in items:
            if snap.source_id is None:
                continue
            group_val = snap.normalized_url_group or _strip_url_query_and_fragment(snap.url)
            if not group_val:
                continue
            pairs.add((int(snap.source_id), group_val))

        if pairs:
            conditions = [
                and_(Page.source_id == sid, Page.normalized_url_group == group)
                for sid, group in pairs
            ]
            page_rows = (
                db.query(Page.source_id, Page.normalized_url_group, Page.snapshot_count)
                .filter(or_(*conditions))
                .all()
            )
            page_counts_by_key = {
                (int(sid), str(group)): int(count)
                for sid, group, count in page_rows
                if sid is not None and group and count is not None
            }

    for snap in items:
        source_obj = snap.source
        if source_obj is None:
            continue

        capture_date = (
            snap.capture_timestamp.date().isoformat()
            if isinstance(snap.capture_timestamp, datetime)
            else str(snap.capture_timestamp)
        )

        original_url = snap.url
        if effective_view == SearchView.pages:
            original_url = (
                snap.normalized_url_group
                or normalize_url_for_grouping(snap.url)
                or _strip_url_query_and_fragment(snap.url)
            )

        page_snapshots_count = None
        if effective_view == SearchView.pages and snap.source_id is not None:
            group_val = snap.normalized_url_group or _strip_url_query_and_fragment(snap.url)
            if group_val:
                page_snapshots_count = page_counts_by_key.get((int(snap.source_id), group_val))

        search_results.append(
            SnapshotSummarySchema(
                id=snap.id,
                title=snap.title,
                sourceCode=source_obj.code,
                sourceName=source_obj.name,
                language=snap.language,
                captureDate=capture_date,
                captureTimestamp=_format_capture_timestamp(snap.capture_timestamp),
                jobId=snap.job_id,
                originalUrl=original_url,
                snippet=snap.snippet,
                pageSnapshotsCount=page_snapshots_count,
                rawSnapshotUrl=f"/api/snapshots/raw/{snap.id}",
                browseUrl=_build_browse_url(
                    snap.job_id, original_url, snap.capture_timestamp, snap.id
                ),
            )
        )

    return (
        SearchResponseSchema(
            results=search_results,
            total=total,
            page=page,
            pageSize=pageSize,
        ),
        mode,
    )


def get_db() -> Iterator[Session]:
    """
    FastAPI dependency that yields a DB session.
    """
    with get_session() as session:
        yield session


def _build_usage_counts(raw: dict[str, int]) -> UsageMetricsCountsSchema:
    return UsageMetricsCountsSchema(
        searchRequests=raw.get(EVENT_SEARCH_REQUEST, 0),
        snapshotDetailViews=raw.get(EVENT_SNAPSHOT_DETAIL, 0),
        rawSnapshotViews=raw.get(EVENT_SNAPSHOT_RAW, 0),
        reportSubmissions=raw.get(EVENT_REPORT_SUBMITTED, 0),
    )


def _build_change_event_schema(change: SnapshotChange) -> ChangeEventSchema:
    source = change.source
    return ChangeEventSchema(
        changeId=change.id,
        changeType=change.change_type,
        summary=change.summary,
        highNoise=bool(change.high_noise),
        diffAvailable=bool(change.diff_html),
        sourceCode=source.code if source else None,
        sourceName=source.name if source else None,
        normalizedUrlGroup=change.normalized_url_group,
        fromSnapshotId=change.from_snapshot_id,
        toSnapshotId=change.to_snapshot_id,
        fromCaptureTimestamp=_format_capture_timestamp(change.from_capture_timestamp),
        toCaptureTimestamp=_format_capture_timestamp(change.to_capture_timestamp),
        fromJobId=change.from_job_id,
        toJobId=change.to_job_id,
        addedSections=change.added_sections,
        removedSections=change.removed_sections,
        changedSections=change.changed_sections,
        addedLines=change.added_lines,
        removedLines=change.removed_lines,
        changeRatio=change.change_ratio,
    )


def _build_compare_snapshot(
    snapshot: Snapshot, job_names: Dict[int, str]
) -> ChangeCompareSnapshotSchema:
    capture_date = (
        snapshot.capture_timestamp.date().isoformat()
        if isinstance(snapshot.capture_timestamp, datetime)
        else str(snapshot.capture_timestamp)
    )
    return ChangeCompareSnapshotSchema(
        snapshotId=snapshot.id,
        title=snapshot.title,
        captureDate=capture_date,
        captureTimestamp=_format_capture_timestamp(snapshot.capture_timestamp),
        originalUrl=snapshot.url,
        jobId=snapshot.job_id,
        jobName=job_names.get(snapshot.job_id) if snapshot.job_id else None,
    )


@router.post("/reports", response_model=IssueReportReceiptSchema, status_code=201)
def submit_issue_report(
    payload: IssueReportCreateSchema, db: Session = Depends(get_db)
) -> IssueReportReceiptSchema:
    """
    Accept a public issue report submission.

    This endpoint intentionally collects minimal data and does not accept
    attachments. Reports are meant for metadata errors, broken snapshots,
    missing coverage, or takedown requests.
    """
    received_at = datetime.now(timezone.utc)

    if payload.website and payload.website.strip():
        return IssueReportReceiptSchema(
            reportId=None,
            status="received",
            receivedAt=received_at,
        )

    description = payload.description.strip()
    if len(description) < 20:
        raise HTTPException(status_code=400, detail="Description must be at least 20 characters.")

    original_url = payload.originalUrl.strip() if payload.originalUrl else None
    if original_url == "":
        original_url = None

    page_url = payload.pageUrl.strip() if payload.pageUrl else None
    if page_url == "":
        page_url = None

    reporter_email = payload.reporterEmail.strip() if payload.reporterEmail else None
    if reporter_email == "":
        reporter_email = None

    report = IssueReport(
        category=payload.category.value,
        description=description,
        snapshot_id=payload.snapshotId,
        original_url=original_url,
        reporter_email=reporter_email,
        page_url=page_url,
        status="new",
    )
    db.add(report)
    db.commit()
    db.refresh(report)

    record_usage_event(db, EVENT_REPORT_SUBMITTED)

    return IssueReportReceiptSchema(
        reportId=report.id,
        status=report.status,
        receivedAt=report.created_at or received_at,
    )


@router.get("/usage", response_model=UsageMetricsSchema)
def get_usage_metrics(db: Session = Depends(get_db)) -> UsageMetricsSchema:
    """
    Return aggregated usage metrics (daily counts).
    """
    window_days = get_usage_metrics_window_days()
    if not get_usage_metrics_enabled():
        empty = UsageMetricsCountsSchema(
            searchRequests=0,
            snapshotDetailViews=0,
            rawSnapshotViews=0,
            reportSubmissions=0,
        )
        return UsageMetricsSchema(
            enabled=False,
            windowDays=window_days,
            totals=empty,
            daily=[],
        )

    _start, _end, totals_raw, daily_raw = build_usage_summary(db, window_days)
    totals = _build_usage_counts(totals_raw)
    daily = [
        UsageMetricsDaySchema(
            date=str(row["date"]),
            searchRequests=int(row.get(EVENT_SEARCH_REQUEST, 0) or 0),
            snapshotDetailViews=int(row.get(EVENT_SNAPSHOT_DETAIL, 0) or 0),
            rawSnapshotViews=int(row.get(EVENT_SNAPSHOT_RAW, 0) or 0),
            reportSubmissions=int(row.get(EVENT_REPORT_SUBMITTED, 0) or 0),
        )
        for row in daily_raw
    ]

    return UsageMetricsSchema(
        enabled=True,
        windowDays=window_days,
        totals=totals,
        daily=daily,
    )


@router.get("/exports", response_model=ExportManifestSchema)
def get_exports_manifest() -> ExportManifestSchema:
    """
    Describe the public export endpoints and limits.
    """
    enabled = get_exports_enabled()
    site_base = get_public_site_base_url()
    return ExportManifestSchema(
        enabled=enabled,
        formats=list(_EXPORT_FORMATS),
        defaultLimit=get_exports_default_limit(),
        maxLimit=get_exports_max_limit(),
        dataDictionaryUrl=f"{site_base}/exports",
        snapshots=ExportResourceSchema(
            path="/api/exports/snapshots",
            description="Snapshot metadata export (no raw HTML).",
            formats=list(_EXPORT_FORMATS),
        ),
        changes=ExportResourceSchema(
            path="/api/exports/changes",
            description="Change event export (no diff HTML bodies).",
            formats=list(_EXPORT_FORMATS),
        ),
    )


@router.get("/exports/snapshots")
def export_snapshots(
    format: str = Query(default="jsonl"),
    compressed: bool = Query(default=True),
    source: Optional[str] = Query(default=None),
    afterId: Optional[int] = Query(default=None, ge=0),
    limit: Optional[int] = Query(default=None, ge=1),
    from_: Optional[date] = Query(default=None, alias="from"),
    to: Optional[date] = Query(default=None),
    db: Session = Depends(get_db),
) -> StreamingResponse:
    """
    Stream snapshot metadata exports in JSONL or CSV format.
    """
    if not get_exports_enabled():
        raise HTTPException(status_code=403, detail="Exports are disabled.")

    export_format = _normalize_export_format(format)
    max_limit = get_exports_max_limit()
    if limit is not None and limit > max_limit:
        raise HTTPException(
            status_code=422,
            detail=f"limit must be <= {max_limit}",
        )
    effective_limit = limit or get_exports_default_limit()

    dialect_name = db.get_bind().dialect.name
    range_start, range_end_exclusive = _build_date_range(
        from_=from_, to=to, dialect_name=dialect_name
    )
    source_id = _resolve_source_id(db, source)
    public_base = get_public_site_base_url()

    rows = _iter_snapshot_export_rows(
        db=db,
        source_id=source_id,
        after_id=afterId,
        limit=effective_limit,
        range_start=range_start,
        range_end_exclusive=range_end_exclusive,
        public_base=public_base,
    )

    record_usage_event(db, EVENT_EXPORTS_DOWNLOAD_SNAPSHOTS)

    return _build_export_response(
        rows=rows,
        export_format=export_format,
        filename_base="healtharchive-snapshots",
        fieldnames=_SNAPSHOT_EXPORT_FIELDS,
        compressed=compressed,
    )


@router.head("/exports/snapshots")
def export_snapshots_head(
    format: str = Query(default="jsonl"),
    compressed: bool = Query(default=True),
) -> Response:
    """
    Return headers for snapshot exports without streaming a body.

    Some clients use HEAD requests (`curl -I`) to inspect download headers.
    """
    if not get_exports_enabled():
        raise HTTPException(status_code=403, detail="Exports are disabled.")

    export_format = _normalize_export_format(format)
    content_type = _EXPORT_CONTENT_TYPES[export_format]
    filename = (
        "healtharchive-snapshots.jsonl"
        if export_format == "jsonl"
        else "healtharchive-snapshots.csv"
    )

    headers: dict[str, str] = {}
    if compressed:
        headers["Content-Encoding"] = "gzip"
        filename = f"{filename}.gz"
    headers["Content-Disposition"] = f'attachment; filename="{filename}"'

    return Response(content=b"", media_type=content_type, headers=headers)


@router.get("/exports/changes")
def export_changes(
    format: str = Query(default="jsonl"),
    compressed: bool = Query(default=True),
    source: Optional[str] = Query(default=None),
    afterId: Optional[int] = Query(default=None, ge=0),
    limit: Optional[int] = Query(default=None, ge=1),
    from_: Optional[date] = Query(default=None, alias="from"),
    to: Optional[date] = Query(default=None),
    db: Session = Depends(get_db),
) -> StreamingResponse:
    """
    Stream change event exports in JSONL or CSV format.
    """
    if not get_exports_enabled():
        raise HTTPException(status_code=403, detail="Exports are disabled.")
    if not get_change_tracking_enabled():
        raise HTTPException(
            status_code=403,
            detail="Change tracking is disabled; change exports are unavailable.",
        )

    export_format = _normalize_export_format(format)
    max_limit = get_exports_max_limit()
    if limit is not None and limit > max_limit:
        raise HTTPException(
            status_code=422,
            detail=f"limit must be <= {max_limit}",
        )
    effective_limit = limit or get_exports_default_limit()

    dialect_name = db.get_bind().dialect.name
    range_start, range_end_exclusive = _build_date_range(
        from_=from_, to=to, dialect_name=dialect_name
    )
    source_id = _resolve_source_id(db, source)
    public_base = get_public_site_base_url()

    rows = _iter_change_export_rows(
        db=db,
        source_id=source_id,
        after_id=afterId,
        limit=effective_limit,
        range_start=range_start,
        range_end_exclusive=range_end_exclusive,
        public_base=public_base,
    )

    record_usage_event(db, EVENT_EXPORTS_DOWNLOAD_CHANGES)

    return _build_export_response(
        rows=rows,
        export_format=export_format,
        filename_base="healtharchive-changes",
        fieldnames=_CHANGE_EXPORT_FIELDS,
        compressed=compressed,
    )


@router.head("/exports/changes")
def export_changes_head(
    format: str = Query(default="jsonl"),
    compressed: bool = Query(default=True),
) -> Response:
    """
    Return headers for change exports without streaming a body.

    Some clients use HEAD requests (`curl -I`) to inspect download headers.
    """
    if not get_exports_enabled():
        raise HTTPException(status_code=403, detail="Exports are disabled.")
    if not get_change_tracking_enabled():
        raise HTTPException(
            status_code=403,
            detail="Change tracking is disabled; change exports are unavailable.",
        )

    export_format = _normalize_export_format(format)
    content_type = _EXPORT_CONTENT_TYPES[export_format]
    filename = (
        "healtharchive-changes.jsonl" if export_format == "jsonl" else "healtharchive-changes.csv"
    )

    headers: dict[str, str] = {}
    if compressed:
        headers["Content-Encoding"] = "gzip"
        filename = f"{filename}.gz"
    headers["Content-Disposition"] = f'attachment; filename="{filename}"'

    return Response(content=b"", media_type=content_type, headers=headers)


@router.get("/changes", response_model=ChangeFeedSchema)
def list_changes(
    source: Optional[str] = Query(default=None),
    jobId: Optional[int] = Query(default=None, ge=1),
    latest: bool = Query(default=False),
    includeUnchanged: bool = Query(default=False),
    from_: Optional[date] = Query(default=None, alias="from"),
    to: Optional[date] = Query(default=None),
    page: int = Query(default=1, ge=1),
    pageSize: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
) -> ChangeFeedSchema:
    """
    Return a feed of precomputed change events.
    """
    if not get_change_tracking_enabled():
        return ChangeFeedSchema(
            enabled=False,
            total=0,
            page=page,
            pageSize=pageSize,
            results=[],
        )

    if not (source or jobId or from_ or to or latest):
        latest = True

    if from_ and to and from_ > to:
        raise HTTPException(
            status_code=422,
            detail="Invalid date range: 'from' must be <= 'to'.",
        )

    record_usage_event(db, EVENT_CHANGES_LIST)

    range_start: datetime | None = None
    range_end_exclusive: datetime | None = None
    if from_:
        range_start = datetime(from_.year, from_.month, from_.day, tzinfo=timezone.utc)
    if to:
        range_end_exclusive = datetime(to.year, to.month, to.day, tzinfo=timezone.utc) + timedelta(
            days=1
        )

    dialect_name = db.get_bind().dialect.name
    if dialect_name == "sqlite":
        if range_start is not None:
            range_start = range_start.replace(tzinfo=None)
        if range_end_exclusive is not None:
            range_end_exclusive = range_end_exclusive.replace(tzinfo=None)

    query = db.query(SnapshotChange).join(
        Source, SnapshotChange.source_id == Source.id, isouter=True
    )
    query = query.filter(
        or_(Source.code.is_(None), ~Source.code.in_(_PUBLIC_EXCLUDED_SOURCE_CODES))
    )

    source_id: Optional[int] = None
    if source:
        normalized_code = source.strip().lower()
        if not normalized_code or normalized_code in _PUBLIC_EXCLUDED_SOURCE_CODES:
            raise HTTPException(status_code=404, detail="Source not found")
        source_row = db.query(Source).filter(Source.code == normalized_code).first()
        if not source_row:
            raise HTTPException(status_code=404, detail="Source not found")
        source_id = source_row.id
        query = query.filter(SnapshotChange.source_id == source_id)

    if jobId is not None:
        query = query.filter(SnapshotChange.to_job_id == jobId)
    elif latest:
        latest_jobs = get_latest_job_ids_by_source(db, source_id=source_id)
        job_ids = list(latest_jobs.values())
        if not job_ids:
            return ChangeFeedSchema(
                enabled=True,
                total=0,
                page=page,
                pageSize=pageSize,
                results=[],
            )
        query = query.filter(SnapshotChange.to_job_id.in_(job_ids))

    if range_start is not None:
        query = query.filter(SnapshotChange.to_capture_timestamp >= range_start)
    if range_end_exclusive is not None:
        query = query.filter(SnapshotChange.to_capture_timestamp < range_end_exclusive)

    if not includeUnchanged:
        query = query.filter(SnapshotChange.change_type != CHANGE_TYPE_UNCHANGED)

    total = query.count()
    offset = (page - 1) * pageSize
    rows = (
        query.order_by(
            SnapshotChange.to_capture_timestamp.desc(),
            SnapshotChange.id.desc(),
        )
        .offset(offset)
        .limit(pageSize)
        .all()
    )

    return ChangeFeedSchema(
        enabled=True,
        total=total,
        page=page,
        pageSize=pageSize,
        results=[_build_change_event_schema(row) for row in rows],
    )


@router.get("/changes/compare", response_model=ChangeCompareSchema)
def get_change_compare(
    toSnapshotId: int = Query(..., ge=1),
    fromSnapshotId: Optional[int] = Query(default=None, ge=1),
    db: Session = Depends(get_db),
) -> ChangeCompareSchema:
    """
    Return a precomputed diff between two snapshots.
    """
    if not get_change_tracking_enabled():
        raise HTTPException(status_code=404, detail="Change tracking not enabled")

    change = db.query(SnapshotChange).filter(SnapshotChange.to_snapshot_id == toSnapshotId).first()
    if not change:
        raise HTTPException(status_code=404, detail="Change event not found")
    if fromSnapshotId and change.from_snapshot_id != fromSnapshotId:
        raise HTTPException(status_code=400, detail="Snapshot pair not available")

    to_snapshot = db.query(Snapshot).filter(Snapshot.id == change.to_snapshot_id).first()
    if not to_snapshot:
        raise HTTPException(status_code=404, detail="Snapshot not found")

    from_snapshot: Optional[Snapshot] = None
    if change.from_snapshot_id:
        from_snapshot = db.query(Snapshot).filter(Snapshot.id == change.from_snapshot_id).first()

    job_ids = {
        jid for jid in [to_snapshot.job_id, from_snapshot.job_id if from_snapshot else None] if jid
    }
    job_names: Dict[int, str] = {}
    if job_ids:
        rows = db.query(ArchiveJob.id, ArchiveJob.name).filter(ArchiveJob.id.in_(job_ids)).all()
        job_names = {int(job_id): job_name for job_id, job_name in rows}

    record_usage_event(db, EVENT_COMPARE_VIEW)

    return ChangeCompareSchema(
        event=_build_change_event_schema(change),
        fromSnapshot=_build_compare_snapshot(from_snapshot, job_names) if from_snapshot else None,
        toSnapshot=_build_compare_snapshot(to_snapshot, job_names),
        diffFormat=change.diff_format,
        diffHtml=change.diff_html,
        diffTruncated=bool(change.diff_truncated),
        diffVersion=change.diff_version,
        normalizationVersion=change.normalization_version,
    )


@router.get("/snapshots/{snapshot_id}/compare-live", response_model=CompareLiveSchema)
def get_compare_live(
    snapshot_id: int,
    response: Response,
    mode: str = Query(default="main", pattern=r"^(main|full)$"),
    db: Session = Depends(get_db),
) -> CompareLiveSchema:
    """
    Return a live diff between an archived snapshot and the current URL.
    """
    if not get_compare_live_enabled():
        raise HTTPException(status_code=404, detail="Compare-live not enabled")

    if not _COMPARE_LIVE_SEMAPHORE.acquire(blocking=False):
        raise HTTPException(
            status_code=429,
            detail="Too many concurrent live comparisons. Please retry shortly.",
            headers={"Retry-After": "5"},
        )

    try:
        snapshot = db.query(Snapshot).filter(Snapshot.id == snapshot_id).first()
        if not snapshot:
            raise HTTPException(status_code=404, detail="Snapshot not found")
        if not snapshot.url:
            raise HTTPException(status_code=404, detail="Snapshot URL not found")
        if not is_html_mime_type(snapshot.mime_type):
            raise HTTPException(status_code=422, detail="Snapshot is not HTML")

        try:
            archived_html = load_snapshot_html(
                snapshot,
                max_bytes=get_compare_live_max_archive_bytes(),
            )
        except LiveCompareTooLarge as exc:
            raise HTTPException(status_code=413, detail=str(exc)) from exc
        except LiveCompareError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

        try:
            live_result = fetch_live_html(
                snapshot.url,
                timeout_seconds=get_compare_live_timeout_seconds(),
                max_redirects=get_compare_live_max_redirects(),
                max_bytes=get_compare_live_max_bytes(),
                user_agent=get_compare_live_user_agent(),
            )
        except LiveFetchBlocked as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except LiveFetchNotHtml as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except LiveFetchTooLarge as exc:
            raise HTTPException(status_code=413, detail=str(exc)) from exc
        except LiveFetchError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

        doc_a, doc_b, extraction = build_compare_documents(
            archived_html,
            live_result.html,
            mode=mode,
        )
        compare = compute_live_compare_from_docs(doc_a, doc_b)
        render_payload = build_compare_render_payload(
            doc_a,
            doc_b,
            max_lines=get_compare_live_max_render_lines(),
        )
        summary = summarize_live_compare(compare.stats)

        job_names: Dict[int, str] = {}
        if snapshot.job_id:
            row = (
                db.query(ArchiveJob.id, ArchiveJob.name)
                .filter(ArchiveJob.id == snapshot.job_id)
                .first()
            )
            if row:
                job_names = {int(row[0]): row[1]}

        record_usage_event(db, EVENT_COMPARE_LIVE_VIEW)

        response.headers["Cache-Control"] = "no-store"
        response.headers["Pragma"] = "no-cache"
        response.headers["X-Robots-Tag"] = "noindex, nofollow"

        return CompareLiveSchema(
            archivedSnapshot=_build_compare_snapshot(snapshot, job_names),
            liveFetch=CompareLiveFetchSchema(
                requestedUrl=live_result.requested_url,
                finalUrl=live_result.final_url,
                statusCode=live_result.status_code,
                contentType=live_result.content_type,
                bytesRead=live_result.bytes_read,
                fetchedAt=live_result.fetched_at,
            ),
            stats=CompareLiveStatsSchema(
                summary=summary,
                addedSections=compare.stats.added_sections,
                removedSections=compare.stats.removed_sections,
                changedSections=compare.stats.changed_sections,
                addedLines=compare.stats.added_lines,
                removedLines=compare.stats.removed_lines,
                changeRatio=compare.stats.change_ratio,
                highNoise=compare.stats.high_noise,
            ),
            diff=CompareLiveDiffSchema(
                diffFormat="html",
                diffHtml=compare.diff_html,
                diffTruncated=compare.diff_truncated,
                diffVersion=compare.diff_version,
                normalizationVersion=compare.normalization_version,
            ),
            render=CompareLiveRenderSchema(
                archivedLines=render_payload.archived_lines,
                liveLines=render_payload.live_lines,
                renderInstructions=[
                    CompareLiveRenderInstructionSchema(
                        type=instruction.type,
                        lineIndexA=instruction.line_index_a,
                        lineIndexB=instruction.line_index_b,
                    )
                    for instruction in render_payload.render_instructions
                ],
                renderTruncated=render_payload.render_truncated,
                renderLineLimit=render_payload.render_line_limit,
            ),
            textModeRequested=extraction.requested_mode,
            textModeUsed=extraction.used_mode,
            textModeFallback=extraction.fallback_applied,
        )
    finally:
        _COMPARE_LIVE_SEMAPHORE.release()


@router.get("/snapshots/{snapshot_id}/latest", response_model=SnapshotLatestSchema)
def get_snapshot_latest(
    snapshot_id: int,
    requireHtml: bool = Query(
        default=True,
        description="When true (default), only return the most recent HTML snapshot for this page group.",
    ),
    db: Session = Depends(get_db),
) -> SnapshotLatestSchema:
    """
    Return the most recent snapshot for the same normalized_url_group as snapshot_id.
    """
    snap = (
        db.query(Snapshot.id, Snapshot.source_id, Snapshot.url, Snapshot.normalized_url_group)
        .filter(Snapshot.id == snapshot_id)
        .first()
    )
    if not snap:
        raise HTTPException(status_code=404, detail="Snapshot not found")

    source_id: int | None = snap[1]
    url: str = snap[2]
    group: str | None = snap[3]
    if not group:
        group = normalize_url_for_grouping(url)
    if not group or source_id is None:
        return SnapshotLatestSchema(found=False)

    query = (
        db.query(Snapshot.id, Snapshot.capture_timestamp, Snapshot.mime_type)
        .filter(Snapshot.source_id == source_id)
        .filter(Snapshot.normalized_url_group == group)
    )
    if requireHtml:
        query = query.filter(
            or_(
                Snapshot.mime_type.ilike("text/html%"),
                Snapshot.mime_type.ilike("application/xhtml+xml%"),
            )
        )

    row = query.order_by(Snapshot.capture_timestamp.desc(), Snapshot.id.desc()).first()
    if not row:
        return SnapshotLatestSchema(found=False)

    latest_id, capture_ts, mime_type = row
    return SnapshotLatestSchema(
        found=True,
        snapshotId=int(latest_id),
        captureTimestamp=_format_capture_timestamp(capture_ts),
        mimeType=mime_type,
    )


@router.get(
    "/snapshots/{snapshot_id}/timeline",
    response_model=SnapshotTimelineSchema,
)
def get_snapshot_timeline(
    snapshot_id: int,
    db: Session = Depends(get_db),
) -> SnapshotTimelineSchema:
    """
    Return a timeline of captures for the same normalized URL group.
    """
    snap = (
        db.query(Snapshot)
        .options(joinedload(Snapshot.source))
        .filter(Snapshot.id == snapshot_id)
        .first()
    )
    if not snap:
        raise HTTPException(status_code=404, detail="Snapshot not found")

    group = snap.normalized_url_group or normalize_url_for_grouping(snap.url)
    if not group:
        raise HTTPException(status_code=404, detail="Snapshot not grouped")

    snapshots = (
        db.query(Snapshot)
        .filter(Snapshot.source_id == snap.source_id)
        .filter(Snapshot.normalized_url_group == group)
        .order_by(Snapshot.capture_timestamp.asc(), Snapshot.id.asc())
        .all()
    )

    snapshot_ids = [row.id for row in snapshots]
    change_rows = (
        db.query(SnapshotChange.to_snapshot_id, SnapshotChange.from_snapshot_id)
        .filter(SnapshotChange.to_snapshot_id.in_(snapshot_ids))
        .all()
    )
    compare_map = {row[0]: row[1] for row in change_rows}

    job_ids = {row.job_id for row in snapshots if row.job_id}
    job_names: Dict[int, str] = {}
    if job_ids:
        rows = db.query(ArchiveJob.id, ArchiveJob.name).filter(ArchiveJob.id.in_(job_ids)).all()
        job_names = {int(job_id): job_name for job_id, job_name in rows}

    items: List[SnapshotTimelineItemSchema] = []
    for row in snapshots:
        capture_date = (
            row.capture_timestamp.date().isoformat()
            if isinstance(row.capture_timestamp, datetime)
            else str(row.capture_timestamp)
        )
        items.append(
            SnapshotTimelineItemSchema(
                snapshotId=row.id,
                captureDate=capture_date,
                captureTimestamp=_format_capture_timestamp(row.capture_timestamp),
                jobId=row.job_id,
                jobName=job_names.get(row.job_id) if row.job_id else None,
                title=row.title,
                statusCode=row.status_code,
                compareFromSnapshotId=compare_map.get(row.id),
                browseUrl=_build_browse_url(
                    row.job_id,
                    row.url,
                    row.capture_timestamp,
                    row.id,
                ),
            )
        )

    record_usage_event(db, EVENT_TIMELINE_VIEW)

    return SnapshotTimelineSchema(
        sourceCode=snap.source.code if snap.source else None,
        sourceName=snap.source.name if snap.source else None,
        normalizedUrlGroup=group,
        snapshots=items,
    )


@router.get("/changes/rss")
def get_changes_rss(
    source: Optional[str] = Query(default=None),
    db: Session = Depends(get_db),
) -> Response:
    """
    Return an RSS feed for the latest change events.
    """
    if not get_change_tracking_enabled():
        raise HTTPException(status_code=404, detail="Change tracking not enabled")

    source_id: Optional[int] = None
    source_name: Optional[str] = None
    if source:
        normalized_code = source.strip().lower()
        if not normalized_code or normalized_code in _PUBLIC_EXCLUDED_SOURCE_CODES:
            raise HTTPException(status_code=404, detail="Source not found")
        source_row = db.query(Source).filter(Source.code == normalized_code).first()
        if not source_row:
            raise HTTPException(status_code=404, detail="Source not found")
        source_id = source_row.id
        source_name = source_row.name

    latest_jobs = get_latest_job_ids_by_source(db, source_id=source_id)
    job_ids = list(latest_jobs.values())
    if not job_ids:
        raise HTTPException(status_code=404, detail="No change events available")

    query = db.query(SnapshotChange).join(
        Source, SnapshotChange.source_id == Source.id, isouter=True
    )
    query = query.filter(SnapshotChange.to_job_id.in_(job_ids))
    query = query.filter(SnapshotChange.change_type != CHANGE_TYPE_UNCHANGED)
    query = query.order_by(
        SnapshotChange.to_capture_timestamp.desc(),
        SnapshotChange.id.desc(),
    )

    rows = query.limit(50).all()

    site_base = get_public_site_base_url()
    api_base = "https://api.healtharchive.ca"
    if source_name:
        title = f"HealthArchive changes - {source_name}"
    else:
        title = "HealthArchive changes - latest editions"

    from email.utils import format_datetime
    from html import escape as xml_escape

    items = []
    for row in rows:
        source_label = row.source.name if row.source else "HealthArchive"
        summary = row.summary or "Archived text updated"
        link = f"{site_base}/compare?to={row.to_snapshot_id}"
        if row.from_snapshot_id is None:
            link = f"{site_base}/snapshot/{row.to_snapshot_id}"

        pub_date = None
        if isinstance(row.to_capture_timestamp, datetime):
            ts = row.to_capture_timestamp
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            pub_date = format_datetime(ts)

        items.append(
            {
                "title": xml_escape(f"{source_label}: {summary}"),
                "link": xml_escape(link),
                "guid": xml_escape(
                    f"{api_base}/api/changes/compare?toSnapshotId={row.to_snapshot_id}"
                ),
                "pubDate": pub_date or format_datetime(datetime.now(timezone.utc)),
                "description": xml_escape(summary),
            }
        )

    rss_items = "\n".join(
        "\n".join(
            [
                "<item>",
                f"<title>{item['title']}</title>",
                f"<link>{item['link']}</link>",
                f"<guid>{item['guid']}</guid>",
                f"<pubDate>{item['pubDate']}</pubDate>",
                f"<description>{item['description']}</description>",
                "</item>",
            ]
        )
        for item in items
    )

    rss = "\n".join(
        [
            '<?xml version="1.0" encoding="UTF-8"?>',
            '<rss version="2.0">',
            "<channel>",
            f"<title>{xml_escape(title)}</title>",
            f"<link>{xml_escape(site_base)}</link>",
            "<description>Recent archived text changes between captured editions.</description>",
            rss_items,
            "</channel>",
            "</rss>",
        ]
    )

    return Response(content=rss, media_type="application/rss+xml")


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
        db.query(ArchiveJob.status, func.count(ArchiveJob.id)).group_by(ArchiveJob.status).all()
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

    distinct_pages = (
        db.query(
            Snapshot.source_id.label("source_id"),
            func.coalesce(Snapshot.normalized_url_group, Snapshot.url).label("group_key"),
        )
        .filter(Snapshot.source_id.isnot(None))
        .distinct()
        .subquery()
    )
    pages_total = int(db.query(func.count()).select_from(distinct_pages).scalar() or 0)

    sources_total = int(
        db.query(func.count(func.distinct(Snapshot.source_id)))
        .filter(Snapshot.source_id.isnot(None))
        .scalar()
        or 0
    )

    latest_capture_ts = db.query(func.max(Snapshot.capture_timestamp)).scalar()
    latest_capture_date: Optional[str] = None
    latest_capture_age_days: Optional[int] = None
    if latest_capture_ts:
        if isinstance(latest_capture_ts, datetime) and latest_capture_ts.tzinfo:
            latest_capture_date_obj = latest_capture_ts.astimezone(timezone.utc).date()
        else:
            latest_capture_date_obj = latest_capture_ts.date()

        latest_capture_date = latest_capture_date_obj.isoformat()

        today = datetime.now(timezone.utc).date()
        latest_capture_age_days = max(0, (today - latest_capture_date_obj).days)

    return ArchiveStatsSchema(
        snapshotsTotal=snapshots_total,
        pagesTotal=pages_total,
        sourcesTotal=sources_total,
        latestCaptureDate=latest_capture_date,
        latestCaptureAgeDays=latest_capture_age_days,
    )


@router.get("/sources", response_model=List[SourceSummarySchema])
def list_sources(
    lang: Optional[str] = Query(default=None, pattern=r"^(en|fr)$"),
    db: Session = Depends(get_db),
) -> List[SourceSummarySchema]:
    """
    Return per-source summary statistics derived from Snapshot data.
    """
    normalized_lang = (lang or "").strip().lower()
    if normalized_lang not in ("en", "fr"):
        normalized_lang = ""

    source_name_overrides: dict[str, dict[str, str]] = {
        "hc": {"fr": "Santé Canada"},
        "phac": {"fr": "Agence de la santé publique du Canada"},
        "cihr": {"fr": "Instituts de recherche en santé du Canada"},
    }

    # Prefer bilingual "home" pages as entry points when the caller requests a
    # specific language. This affects the entryBrowseUrl + entryPreviewUrl used
    # by the frontend browse cards.
    source_entry_base_urls: dict[str, dict[str, str]] = {
        "hc": {
            "en": "https://www.canada.ca/en/health-canada.html",
            "fr": "https://www.canada.ca/fr/sante-canada.html",
        },
        "phac": {
            "en": "https://www.canada.ca/en/public-health.html",
            "fr": "https://www.canada.ca/fr/sante-publique.html",
        },
        "cihr": {
            "en": "https://cihr-irsc.gc.ca/e/193.html",
            "fr": "https://cihr-irsc.gc.ca/f/193.html",
        },
    }
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
        .filter(~Source.code.in_(_PUBLIC_EXCLUDED_SOURCE_CODES))
        .order_by(Source.name)
        .all()
    )

    summaries: List[SourceSummarySchema] = []

    for source, record_count, first_capture, last_capture in rows:
        localized_name = source.name
        if normalized_lang and source.code in source_name_overrides:
            localized_name = source_name_overrides[source.code].get(normalized_lang, source.name)

        localized_base_url = source.base_url
        if normalized_lang and source.code in source_entry_base_urls:
            localized_base_url = source_entry_base_urls[source.code].get(
                normalized_lang, source.base_url
            )

        # Latest record id for this source
        latest_snapshot = (
            db.query(Snapshot.id)
            .filter(Snapshot.source_id == source.id)
            .order_by(Snapshot.capture_timestamp.desc(), Snapshot.id.desc())
            .first()
        )
        latest_record_id: Optional[int] = latest_snapshot[0] if latest_snapshot else None

        entry_record_id: Optional[int] = None
        entry_job_id: Optional[int] = None
        entry_browse_url: Optional[str] = None
        entry_preview_url: Optional[str] = None

        entry_groups = _candidate_entry_groups(localized_base_url)
        if entry_groups:
            entry_status_quality = case(
                (Snapshot.status_code.is_(None), 0),
                (
                    and_(Snapshot.status_code >= 200, Snapshot.status_code < 300),
                    2,
                ),
                (
                    and_(Snapshot.status_code >= 300, Snapshot.status_code < 400),
                    1,
                ),
                else_=-1,
            )
            entry_snapshot = (
                db.query(
                    Snapshot.id,
                    Snapshot.job_id,
                    Snapshot.url,
                    Snapshot.capture_timestamp,
                    Snapshot.status_code,
                )
                .filter(Snapshot.source_id == source.id)
                .filter(Snapshot.normalized_url_group.in_(entry_groups))
                .order_by(
                    entry_status_quality.desc(),
                    Snapshot.capture_timestamp.desc(),
                    Snapshot.id.desc(),
                )
                .first()
            )
            if entry_snapshot:
                entry_record_id = entry_snapshot[0]
                entry_job_id = entry_snapshot[1]
                entry_browse_url = _build_browse_url(
                    entry_job_id, entry_snapshot[2], entry_snapshot[3], entry_record_id
                )

        # If the exact baseUrl wasn't captured, fall back to a "reasonable"
        # entry point on the same host (avoid third-party pages being treated as
        # the source homepage).
        if entry_record_id is None and localized_base_url:
            host_variants = _candidate_entry_hosts(localized_base_url)
            host_filters: list[Any] = []
            for host in host_variants:
                for scheme in ("https", "http"):
                    prefix = f"{scheme}://{host}"
                    host_filters.append(Snapshot.url.ilike(f"{prefix}/%"))
                    host_filters.append(Snapshot.url == prefix)
                    host_filters.append(Snapshot.url == f"{prefix}/")

            if host_filters:
                candidates = (
                    db.query(
                        Snapshot.id,
                        Snapshot.job_id,
                        Snapshot.url,
                        Snapshot.capture_timestamp,
                        Snapshot.status_code,
                    )
                    .filter(Snapshot.source_id == source.id)
                    .filter(or_(*host_filters))
                    .order_by(Snapshot.capture_timestamp.desc(), Snapshot.id.desc())
                    .limit(500)
                    .all()
                )

                best: Optional[tuple] = None
                best_key: Optional[tuple] = None
                for cand_id, cand_job_id, cand_url, cand_ts, cand_status in candidates:
                    key = _entry_candidate_key(
                        snapshot_id=cand_id,
                        url=cand_url,
                        capture_timestamp=cand_ts,
                        status_code=cand_status,
                    )
                    if best_key is None or key > best_key:
                        best_key = key
                        best = (cand_id, cand_job_id, cand_url, cand_ts)

                if best is not None:
                    entry_record_id, entry_job_id, entry_url, entry_ts = best
                    entry_browse_url = _build_browse_url(
                        entry_job_id, entry_url, entry_ts, entry_record_id
                    )

        preview_dir = get_replay_preview_dir()
        if preview_dir is not None and entry_job_id:
            if _find_replay_preview_file(
                preview_dir, source.code, entry_job_id, lang=normalized_lang
            ):
                entry_preview_url = f"/api/sources/{source.code}/preview?jobId={entry_job_id}"
                if normalized_lang:
                    entry_preview_url = f"{entry_preview_url}&lang={normalized_lang}"

        summaries.append(
            SourceSummarySchema(
                sourceCode=source.code,
                sourceName=localized_name,
                baseUrl=localized_base_url,
                description=source.description,
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
                latestRecordId=latest_record_id,
                entryRecordId=entry_record_id,
                entryBrowseUrl=entry_browse_url,
                entryPreviewUrl=entry_preview_url,
            )
        )

    return summaries


@router.get("/sources/{source_code}/editions", response_model=List[SourceEditionSchema])
def list_source_editions(
    source_code: str, db: Session = Depends(get_db)
) -> List[SourceEditionSchema]:
    """
    Return replayable "editions" (ArchiveJobs) for a source.

    Each indexed ArchiveJob becomes a discrete edition in the replay service
    (`job-<id>` collection). The frontend uses this to power edition switching.
    """
    normalized_code = source_code.strip().lower()
    if not normalized_code or normalized_code in _PUBLIC_EXCLUDED_SOURCE_CODES:
        raise HTTPException(status_code=404, detail="Source not found")

    source = db.query(Source).filter(Source.code == normalized_code).first()
    if not source:
        raise HTTPException(status_code=404, detail="Source not found")

    job_agg = (
        db.query(
            Snapshot.job_id.label("job_id"),
            func.count(Snapshot.id).label("record_count"),
            func.min(Snapshot.capture_timestamp).label("first_capture"),
            func.max(Snapshot.capture_timestamp).label("last_capture"),
        )
        .filter(Snapshot.source_id == source.id)
        .filter(Snapshot.job_id.isnot(None))
        .group_by(Snapshot.job_id)
        .subquery()
    )

    rows = (
        db.query(
            ArchiveJob.id,
            ArchiveJob.name,
            job_agg.c.record_count,
            job_agg.c.first_capture,
            job_agg.c.last_capture,
        )
        .join(job_agg, job_agg.c.job_id == ArchiveJob.id)
        .filter(ArchiveJob.source_id == source.id)
        .filter(ArchiveJob.status == "indexed")
        .order_by(job_agg.c.last_capture.desc(), ArchiveJob.id.desc())
        .all()
    )

    entry_groups = _candidate_entry_groups(source.base_url)
    host_variants = _candidate_entry_hosts(source.base_url)
    replay_enabled = bool(get_replay_base_url())

    editions: List[SourceEditionSchema] = []
    for job_id, job_name, record_count, first_capture, last_capture in rows:
        entry_browse_url: Optional[str] = None
        if replay_enabled and job_id:
            entry_url: Optional[str] = None
            entry_ts: Any = None
            entry_snapshot_id: Optional[int] = None

            if entry_groups:
                entry_status_quality = case(
                    (Snapshot.status_code.is_(None), 0),
                    (
                        and_(Snapshot.status_code >= 200, Snapshot.status_code < 300),
                        2,
                    ),
                    (
                        and_(Snapshot.status_code >= 300, Snapshot.status_code < 400),
                        1,
                    ),
                    else_=-1,
                )
                entry_snapshot = (
                    db.query(Snapshot.id, Snapshot.url, Snapshot.capture_timestamp)
                    .filter(Snapshot.source_id == source.id)
                    .filter(Snapshot.job_id == job_id)
                    .filter(Snapshot.normalized_url_group.in_(entry_groups))
                    .order_by(
                        entry_status_quality.desc(),
                        Snapshot.capture_timestamp.desc(),
                        Snapshot.id.desc(),
                    )
                    .first()
                )
                if entry_snapshot:
                    entry_snapshot_id, entry_url, entry_ts = entry_snapshot

            if entry_url is None and host_variants:
                host_filters: list[Any] = []
                for host in host_variants:
                    for scheme in ("https", "http"):
                        prefix = f"{scheme}://{host}"
                        host_filters.append(Snapshot.url.ilike(f"{prefix}/%"))
                        host_filters.append(Snapshot.url == prefix)
                        host_filters.append(Snapshot.url == f"{prefix}/")

                if host_filters:
                    candidates = (
                        db.query(
                            Snapshot.id,
                            Snapshot.url,
                            Snapshot.capture_timestamp,
                            Snapshot.status_code,
                        )
                        .filter(Snapshot.source_id == source.id)
                        .filter(Snapshot.job_id == job_id)
                        .filter(or_(*host_filters))
                        .order_by(Snapshot.capture_timestamp.desc(), Snapshot.id.desc())
                        .limit(500)
                        .all()
                    )

                    best: Optional[tuple] = None
                    best_key: Optional[tuple] = None
                    for cand_id, cand_url, cand_ts, cand_status in candidates:
                        key = _entry_candidate_key(
                            snapshot_id=cand_id,
                            url=cand_url,
                            capture_timestamp=cand_ts,
                            status_code=cand_status,
                        )
                        if best_key is None or key > best_key:
                            best_key = key
                            best = (cand_id, cand_url, cand_ts)

                    if best is not None:
                        entry_snapshot_id, entry_url, entry_ts = best

            if entry_url is not None:
                entry_browse_url = _build_browse_url(job_id, entry_url, entry_ts, entry_snapshot_id)

        editions.append(
            SourceEditionSchema(
                jobId=job_id,
                jobName=job_name,
                recordCount=int(record_count or 0),
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
                entryBrowseUrl=entry_browse_url,
            )
        )

    return editions


@router.api_route("/sources/{source_code}/preview", methods=["GET", "HEAD"])
def get_source_preview(
    source_code: str,
    jobId: int = Query(..., ge=1),
    lang: Optional[str] = Query(default=None, pattern=r"^(en|fr)$"),
    db: Session = Depends(get_db),
) -> Response:
    """
    Return a cached preview image for a source's replay homepage.

    These previews are generated out-of-band (e.g. via an operator script) and
    stored on disk under HEALTHARCHIVE_REPLAY_PREVIEW_DIR.
    """
    preview_dir = get_replay_preview_dir()
    if preview_dir is None:
        raise HTTPException(status_code=404, detail="Preview images not configured")

    normalized_code = source_code.strip().lower()
    if not normalized_code or normalized_code in _PUBLIC_EXCLUDED_SOURCE_CODES:
        raise HTTPException(status_code=404, detail="Source not found")

    # Validate the source exists to avoid advertising previews for unknown codes.
    source = db.query(Source.id).filter(Source.code == normalized_code).first()
    if not source:
        raise HTTPException(status_code=404, detail="Source not found")

    resolved = _find_replay_preview_file(preview_dir, normalized_code, jobId, lang=lang)
    if resolved is None:
        raise HTTPException(status_code=404, detail="Preview not found")

    candidate, media_type = resolved

    headers = {
        # Previews are derived artifacts; cache aggressively but allow refresh.
        "Cache-Control": "public, max-age=86400, stale-while-revalidate=604800",
    }
    return FileResponse(candidate, media_type=media_type, headers=headers)


@router.get("/replay/resolve", response_model=ReplayResolveSchema)
def resolve_replay_url(
    jobId: int = Query(..., ge=1),
    url: str = Query(..., min_length=1, max_length=4096),
    timestamp: Optional[str] = Query(default=None, pattern=r"^\d{14}$"),
    db: Session = Depends(get_db),
) -> ReplayResolveSchema:
    """
    Resolve a replay URL within a specific job (pywb collection).

    Used by the frontend edition-switching UI to determine whether the current
    original URL exists in another job, and if so, which capture timestamp to
    replay.
    """
    cleaned_url = _strip_url_fragment(url)
    if not cleaned_url:
        raise HTTPException(status_code=400, detail="URL is required")

    job = (
        db.query(ArchiveJob.id)
        .filter(ArchiveJob.id == jobId)
        .filter(ArchiveJob.status == "indexed")
        .first()
    )
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    anchor_dt: Optional[datetime] = None
    if timestamp is not None:
        anchor_dt = _parse_timestamp14(timestamp)
        if anchor_dt is None:
            raise HTTPException(status_code=400, detail="timestamp must be a 14-digit UTC value")

    candidate_urls = _candidate_resolve_urls(cleaned_url)
    if not candidate_urls:
        return ReplayResolveSchema(found=False)

    rows = (
        db.query(
            Snapshot.id,
            Snapshot.url,
            Snapshot.capture_timestamp,
            Snapshot.status_code,
            Snapshot.mime_type,
        )
        .filter(Snapshot.job_id == jobId)
        .filter(Snapshot.url.in_(candidate_urls))
        .all()
    )
    best = _select_best_replay_candidate(rows, anchor_dt)

    if best is None:
        group_candidates: set[str] = set(_candidate_entry_groups(cleaned_url))

        try:
            parts = urlsplit(cleaned_url)
        except Exception:
            parts = None

        if parts is not None:
            path = parts.path or ""
            if path not in ("", "/") and path.endswith("/"):
                group_candidates.update(_candidate_entry_groups(cleaned_url.rstrip("/")))
            elif path not in ("", "/") and not path.endswith("/"):
                group_candidates.update(_candidate_entry_groups(f"{cleaned_url}/"))

        if group_candidates:
            group_rows = (
                db.query(
                    Snapshot.id,
                    Snapshot.url,
                    Snapshot.capture_timestamp,
                    Snapshot.status_code,
                    Snapshot.mime_type,
                )
                .filter(Snapshot.job_id == jobId)
                .filter(Snapshot.normalized_url_group.in_(sorted(group_candidates)))
                .order_by(Snapshot.capture_timestamp.desc(), Snapshot.id.desc())
                .limit(250)
                .all()
            )
            best = _select_best_replay_candidate(group_rows, anchor_dt)

    if best is None:
        return ReplayResolveSchema(found=False)

    snap_id, resolved_url, capture_ts, _status, mime_type = best

    return ReplayResolveSchema(
        found=True,
        snapshotId=snap_id,
        captureTimestamp=_format_capture_timestamp(capture_ts),
        resolvedUrl=resolved_url,
        browseUrl=_build_browse_url(jobId, resolved_url, capture_ts, snap_id),
        mimeType=mime_type,
    )


@router.get("/search", response_model=SearchResponseSchema)
def search_snapshots(
    q: Optional[str] = Query(default=None, min_length=1, max_length=256),
    source: Optional[str] = Query(
        default=None, min_length=1, max_length=16, pattern=r"^[a-z0-9-]+$"
    ),
    sort: Optional[SearchSort] = Query(default=None),
    view: Optional[SearchView] = Query(default=None),
    includeNon2xx: bool = Query(default=False),
    includeDuplicates: bool = Query(
        default=False,
        description="When view=snapshots, include same-day duplicate captures with identical content.",
    ),
    from_: Optional[date] = Query(
        default=None,
        alias="from",
        description="Filter captures from this UTC date (YYYY-MM-DD), inclusive.",
    ),
    to: Optional[date] = Query(
        default=None,
        description="Filter captures up to this UTC date (YYYY-MM-DD), inclusive.",
    ),
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
    Search snapshots by keyword and/or source with simple pagination.
    """
    start_time = time.perf_counter()
    mode = "newest"

    try:
        response, mode = _search_snapshots_inner(
            q=q,
            source=source,
            sort=sort,
            view=view,
            includeNon2xx=includeNon2xx,
            includeDuplicates=includeDuplicates,
            from_date=from_,
            to_date=to,
            page=page,
            pageSize=pageSize,
            ranking=ranking,
            db=db,
        )
    except Exception as exc:
        # Classify error type for metrics
        from fastapi import HTTPException
        from sqlalchemy.exc import TimeoutError as SQLAlchemyTimeout

        error_type = "unknown"
        if isinstance(exc, HTTPException):
            if 400 <= exc.status_code < 500:
                error_type = "client"
            elif exc.status_code >= 500:
                error_type = "server"
        elif isinstance(exc, (TimeoutError, SQLAlchemyTimeout)):
            error_type = "timeout"
        elif isinstance(exc, (ValueError, TypeError, KeyError)):
            error_type = "client"
        else:
            error_type = "server"

        observe_search_request(
            duration_seconds=time.perf_counter() - start_time,
            mode=mode,
            ok=False,
            error_type=error_type,
        )
        raise

    observe_search_request(
        duration_seconds=time.perf_counter() - start_time,
        mode=mode,
        ok=True,
    )
    record_usage_event(db, EVENT_SEARCH_REQUEST)
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
                Snapshot.job_id,
                Snapshot.url,
                Snapshot.capture_timestamp,
                Snapshot.mime_type,
                Snapshot.status_code,
                Snapshot.title,
                Snapshot.snippet,
                Snapshot.language,
            ),
            joinedload(Snapshot.source),
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

    record_usage_event(db, EVENT_SNAPSHOT_DETAIL)

    return SnapshotDetailSchema(
        id=snap.id,
        title=snap.title,
        sourceCode=snap.source.code,
        sourceName=snap.source.name,
        language=snap.language,
        captureDate=capture_date,
        captureTimestamp=_format_capture_timestamp(snap.capture_timestamp),
        jobId=snap.job_id,
        originalUrl=snap.url,
        snippet=snap.snippet,
        rawSnapshotUrl=f"/api/snapshots/raw/{snap.id}",
        browseUrl=_build_browse_url(snap.job_id, snap.url, snap.capture_timestamp, snap.id),
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
                Snapshot.job_id,
                Snapshot.source_id,
                Snapshot.url,
                Snapshot.capture_timestamp,
                Snapshot.mime_type,
                Snapshot.title,
                Snapshot.language,
                Snapshot.warc_path,
                Snapshot.warc_record_id,
            ),
            joinedload(Snapshot.source).load_only(Source.name),
        )
        .filter(Snapshot.id == snapshot_id)
        .first()
    )
    if snap is None:
        raise HTTPException(status_code=404, detail="Snapshot not found")

    if not snap.warc_path:
        raise HTTPException(status_code=404, detail="No WARC path associated with this snapshot")

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

    record_usage_event(db, EVENT_SNAPSHOT_RAW)

    try:
        html_body = record.body_bytes.decode("utf-8", errors="replace")
    except Exception:
        raise HTTPException(
            status_code=500,
            detail="Failed to decode archived HTML content",
        )

    site_base = get_public_site_base_url()
    replay_url = _build_browse_url(snap.job_id, snap.url, snap.capture_timestamp, snap.id)
    snapshot_details_url = f"{site_base}/snapshot/{snap.id}"
    back_url = snapshot_details_url if snap.id else f"{site_base}/"
    snapshot_history_url = f"{snapshot_details_url}?view=details"
    snapshot_json_url = f"/api/snapshot/{snap.id}"

    capture_date = (
        snap.capture_timestamp.date().isoformat()
        if isinstance(snap.capture_timestamp, datetime)
        else str(snap.capture_timestamp)
    )

    def _compact_url(value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            return ""
        try:
            parsed = urlsplit(cleaned)
            host = parsed.netloc or ""
            rest = f"{parsed.path}{parsed.query and f'?{parsed.query}' or ''}"
            rest = rest or ""
            if rest.endswith("/") and len(rest) > 1:
                rest = rest[:-1]
            if host:
                return f"{host}{rest}"
        except Exception:
            return re.sub(r"^https?://", "", cleaned, flags=re.IGNORECASE)
        return re.sub(r"^https?://", "", cleaned, flags=re.IGNORECASE)

    compare_snapshot_id: Optional[int] = None
    if is_html_mime_type(snap.mime_type):
        group = normalize_url_for_grouping(snap.url)
        if group and snap.source_id:
            latest = (
                db.query(Snapshot.id)
                .filter(Snapshot.source_id == snap.source_id)
                .filter(Snapshot.normalized_url_group == group)
                .filter(
                    or_(
                        Snapshot.mime_type.ilike("text/html%"),
                        Snapshot.mime_type.ilike("application/xhtml+xml%"),
                    )
                )
                .order_by(Snapshot.capture_timestamp.desc(), Snapshot.id.desc())
                .first()
            )
            if latest:
                compare_snapshot_id = int(latest[0])
    if compare_snapshot_id is None and is_html_mime_type(snap.mime_type):
        compare_snapshot_id = snap.id

    compare_url = (
        f"{site_base}/compare-live?to={compare_snapshot_id}&run=1" if compare_snapshot_id else None
    )
    cite_url = f"{site_base}/cite"

    report_params = []
    if snap.id:
        report_params.append(("snapshot", str(snap.id)))
        report_params.append(("page", f"/snapshot/{snap.id}"))
    if snap.url:
        report_params.append(("url", snap.url))
    report_query = urlencode(report_params) if report_params else ""
    report_url = f"{site_base}/report?{report_query}" if report_query else f"{site_base}/report"

    action_links = []
    if replay_url:
        action_links.append(
            f'<a class="ha-replay-navlink ha-replay-navlink--primary" href="{replay_url}" rel="noreferrer">View replay</a>'
        )
    if compare_url:
        action_links.append(
            f'<a class="ha-replay-navlink" href="{compare_url}" rel="noreferrer">View diff</a>'
        )
    action_links.append(
        f'<a class="ha-replay-navlink" href="{snapshot_details_url}" rel="noreferrer">Details</a>'
    )
    action_links.append(
        f'<a class="ha-replay-navlink" href="{snapshot_json_url}" rel="noreferrer" target="_blank">Metadata JSON</a>'
    )
    action_links.append(f'<a class="ha-replay-navlink" href="{cite_url}" rel="noreferrer">Cite</a>')
    action_links.append(
        f'<a class="ha-replay-navlink" href="{report_url}" rel="noreferrer">Report issue</a>'
    )
    action_links.append(
        f'<a class="ha-replay-navlink" href="{snapshot_history_url}" rel="noreferrer">All snapshots</a>'
    )
    action_links_html = "".join(action_links)

    title_html = html.escape(snap.title or "Raw HTML")
    date_html = html.escape(capture_date or "")
    url_text_html = html.escape(_compact_url(snap.url) or snap.url or "")
    url_href_html = html.escape(snap.url or "", quote=True)

    banner = f"""
<style id="ha-replay-banner-css">
  #ha-replay-banner {{
    position: sticky;
    top: 0;
    z-index: 2147483647;
    overflow: visible;
    border-bottom: 1px solid rgba(148, 163, 184, 0.32);
    background-color: rgba(255, 255, 255, 0.86);
    background-color: color-mix(in srgb, rgba(255, 255, 255, 0.9) 82%, transparent);
    font-family: system-ui, -apple-system, BlinkMacSystemFont, "SF Pro Text", "Segoe UI", sans-serif;
    font-size: 14px;
    line-height: 1.35;
    -webkit-font-smoothing: antialiased;
    backdrop-filter: blur(10px) saturate(1.1);
    box-shadow: 0 8px 18px rgba(15, 23, 42, 0.06);
  }}

  #ha-replay-banner * {{
    box-sizing: border-box;
  }}

  #ha-replay-banner a {{
    color: inherit;
    text-decoration: none;
  }}

  #ha-replay-banner .ha-replay-bar {{
    display: grid;
    grid-template-columns: max-content minmax(0, 1fr) max-content;
    align-items: center;
    column-gap: 1.15rem;
    padding: 0.55rem 0.9rem;
    min-height: 56px;
  }}

  #ha-replay-banner .ha-replay-left,
  #ha-replay-banner .ha-replay-right {{
    display: flex;
    align-items: center;
    gap: 0.4rem;
    min-width: 0;
  }}

  #ha-replay-banner .ha-replay-left {{
    padding-right: 0.8rem;
  }}

  #ha-replay-banner .ha-replay-right {{
    justify-content: flex-end;
    flex-wrap: nowrap;
  }}

  #ha-replay-banner .ha-replay-center {{
    min-width: 0;
    display: flex;
    flex-direction: column;
    gap: 0.15rem;
    overflow: hidden;
  }}

  #ha-replay-banner .ha-replay-title {{
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    color: rgba(15, 23, 42, 0.92);
    font-weight: 700;
    letter-spacing: -0.01em;
  }}

  #ha-replay-banner .ha-replay-meta {{
    min-width: 0;
    display: flex;
    align-items: center;
    gap: 0.55rem;
    overflow: hidden;
    white-space: nowrap;
    font-size: 1.05rem;
    color: rgba(15, 23, 42, 0.72);
    font-weight: 650;
  }}

  #ha-replay-banner .ha-replay-meta-item {{
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }}

  #ha-replay-banner .ha-replay-meta-item + .ha-replay-meta-item {{
    position: relative;
    padding-left: 0.65rem;
  }}

  #ha-replay-banner .ha-replay-meta-item + .ha-replay-meta-item::before {{
    content: "";
    position: absolute;
    left: 0.22rem;
    top: 50%;
    width: 4px;
    height: 4px;
    border-radius: 999px;
    background: rgba(148, 163, 184, 0.95);
    transform: translateY(-50%);
  }}

  #ha-replay-banner .ha-replay-meta-link {{
    color: rgba(37, 99, 235, 0.95);
  }}

  #ha-replay-banner .ha-replay-meta-link:hover {{
    text-decoration: underline;
  }}

  #ha-replay-banner .ha-replay-disclaimer-inline {{
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    color: #92400e;
    font-weight: 600;
    font-size: 1rem;
    line-height: 1.35;
  }}

  #ha-replay-banner .ha-replay-back-btn {{
    appearance: none;
    border: 1px solid rgba(37, 99, 235, 0.6);
    background-color: #2563eb;
    color: #ffffff;
    border-radius: 12px;
    padding: 0.55rem 0.9rem;
    font: inherit;
    font-weight: 650;
    cursor: pointer;
    line-height: 1;
    text-decoration: none;
    box-shadow: 0 8px 18px rgba(37, 99, 235, 0.2);
    display: inline-flex;
    align-items: center;
    justify-content: center;
    white-space: nowrap;
  }}

  #ha-replay-banner .ha-replay-back-btn:hover {{
    background-color: #1d4ed8;
    color: #ffffff;
  }}

  #ha-replay-banner .ha-replay-navlink,
  #ha-replay-banner .ha-replay-hide {{
    appearance: none;
    border: 1px solid transparent;
    background: transparent;
    color: rgba(15, 23, 42, 0.78);
    border-radius: 10px;
    padding: 0.45rem 0.55rem;
    font: inherit;
    font-weight: 550;
    cursor: pointer;
    line-height: 1.1;
    text-decoration: none;
    display: inline-flex;
    align-items: center;
    justify-content: center;
    white-space: nowrap;
  }}

  #ha-replay-banner .ha-replay-navlink:hover,
  #ha-replay-banner .ha-replay-hide:hover {{
    color: rgba(15, 23, 42, 0.95);
    text-decoration: underline;
    background: rgba(148, 163, 184, 0.12);
    border-color: rgba(148, 163, 184, 0.12);
  }}

  #ha-replay-banner .ha-replay-navlink--primary {{
    color: rgba(37, 99, 235, 0.98);
  }}

  #ha-replay-banner .ha-replay-back-btn:focus-visible,
  #ha-replay-banner .ha-replay-navlink:focus-visible,
  #ha-replay-banner .ha-replay-hide:focus-visible {{
    outline: 3px solid rgba(37, 99, 235, 0.45);
    outline-offset: 2px;
  }}

  #ha-replay-banner .ha-replay-divider {{
    width: 1px;
    height: 20px;
    background: rgba(148, 163, 184, 0.35);
  }}

  @media (max-width: 780px) {{
    #ha-replay-banner .ha-replay-center {{
      display: none;
    }}
  }}

  @media (max-width: 980px) {{
    #ha-replay-banner .ha-replay-right {{
      overflow-x: auto;
      -webkit-overflow-scrolling: touch;
      max-width: 70vw;
      scrollbar-width: thin;
    }}

    #ha-replay-banner .ha-replay-right > * {{
      flex: 0 0 auto;
    }}
  }}
</style>
<div id="ha-replay-banner" role="region" aria-label="HealthArchive snapshot header">
  <div class="ha-replay-bar">
    <div class="ha-replay-left">
      <a class="ha-replay-back-btn" href="{back_url}" rel="noreferrer">\u2190 HealthArchive.ca</a>
    </div>
    <div class="ha-replay-center" aria-label="Snapshot summary">
      <div class="ha-replay-title">{title_html}</div>
      <div class="ha-replay-meta">
        <span class="ha-replay-meta-item">{date_html}</span>
        <a class="ha-replay-meta-item ha-replay-meta-link" href="{url_href_html}" target="_blank" rel="noreferrer noopener">{url_text_html}</a>
      </div>
      <div class="ha-replay-disclaimer-inline">Independent archive \u00b7 Not an official government website \u00b7 Archived content may be outdated</div>
    </div>
    <div class="ha-replay-right">
      {action_links_html}
      <span class="ha-replay-divider" aria-hidden="true"></span>
      <button type="button" class="ha-replay-hide" id="ha-replay-hide" aria-label="Hide this banner">Hide</button>
    </div>
  </div>
</div>
<script>
  (function () {{
    try {{
      var STORAGE_KEY = "haReplayBannerDismissed";
      if (localStorage.getItem(STORAGE_KEY) === "1") {{
        var el = document.getElementById("ha-replay-banner");
        if (el && el.parentNode) el.parentNode.removeChild(el);
        return;
      }}
      var hideBtn = document.getElementById("ha-replay-hide");
      if (!hideBtn) return;
      hideBtn.addEventListener("click", function () {{
        try {{ localStorage.setItem(STORAGE_KEY, "1"); }} catch (e) {{}}
        var el = document.getElementById("ha-replay-banner");
        if (el && el.parentNode) el.parentNode.removeChild(el);
      }});
    }} catch (e) {{}}
  }})();
</script>
"""

    # Try to inject after the first <body ...> tag to avoid breaking <head> content.
    try:
        match = re.search(r"<body\\b[^>]*>", html_body, flags=re.IGNORECASE)
        if match:
            insert_at = match.end()
            html_body = html_body[:insert_at] + banner + html_body[insert_at:]
        else:
            html_body = banner + html_body
    except Exception:
        html_body = banner + html_body

    return HTMLResponse(content=html_body, media_type="text/html")


__all__ = ["router"]
