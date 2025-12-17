from __future__ import annotations

import time
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
import re
from typing import Any, Dict, List, Optional
from urllib.parse import urlsplit, urlunsplit

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from sqlalchemy import and_, case, func, inspect, or_, text
from sqlalchemy.orm import Session, joinedload, load_only
from threading import Lock

from ha_backend.config import get_replay_base_url, get_replay_preview_dir
from ha_backend.db import get_session
from ha_backend.indexing.viewer import find_record_for_snapshot
from ha_backend.models import ArchiveJob, PageSignal, Snapshot, Source
from ha_backend.search import TS_CONFIG, build_search_vector
from ha_backend.search_ranking import (
    QueryMode,
    RankingVersion,
    classify_query_mode,
    get_ranking_config,
    get_ranking_version,
    tokenize_query,
)
from ha_backend.search_query import (
    And as BoolAnd,
    Not as BoolNot,
    Or as BoolOr,
    QueryNode as BoolNode,
    QueryParseError,
    Term as BoolTerm,
    iter_positive_terms,
    looks_like_advanced_query,
    parse_query,
)
from ha_backend.search_fuzzy import (
    pick_word_similarity_threshold,
    should_use_url_similarity,
    token_variants,
)
from ha_backend.url_normalization import normalize_url_for_grouping
from ha_backend.runtime_metrics import observe_search_request

from .schemas import (
    ArchiveStatsSchema,
    ReplayResolveSchema,
    SearchResponseSchema,
    SnapshotDetailSchema,
    SnapshotSummarySchema,
    SourceEditionSchema,
    SourceSummarySchema,
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
    preview_dir: Path, source_code: str, job_id: int
) -> Optional[tuple[Path, str]]:
    """
    Return the first matching preview file path + media type.

    We allow multiple formats so operators can migrate to more efficient image
    encodings without changing the public API contract.
    """
    base = f"source-{source_code}-job-{job_id}"
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
    return (
        value.replace("\\", "\\\\")
        .replace("%", "\\%")
        .replace("_", "\\_")
    )


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
        if not (lowered.startswith("http://") or lowered.startswith("https://") or lowered.startswith("www.")):
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
                candidates.add(
                    urlunsplit((scheme_value, netloc_value, path_value, query, ""))
                )

    return sorted(candidates)


def _select_best_replay_candidate(
    rows: List[tuple[int, str, Any, Optional[int]]],
    anchor: Optional[datetime],
) -> Optional[tuple[int, str, Any, Optional[int]]]:
    best: Optional[tuple[int, str, Any, Optional[int]]] = None
    best_key: Optional[tuple] = None

    anchor_ts = anchor.timestamp() if anchor else None

    for snap_id, snap_url, capture_ts, status_code in rows:
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
            best = (snap_id, snap_url, capture_ts, status_code)

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

    url_search_targets: list[str] | None = (
        _extract_url_search_targets(raw_q) if raw_q else None
    )
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

    rank_text = q_filter or q_rank

    ranking_version = get_ranking_version(ranking)
    # For v2 we use different blends depending on query "intent".
    query_mode = None
    query_tokens: list[str] = []
    ranking_cfg = None
    if ranking_version == RankingVersion.v2 and rank_text:
        query_mode = classify_query_mode(rank_text)
        ranking_cfg = get_ranking_config(mode=query_mode)
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

    group_key = func.coalesce(
        Snapshot.normalized_url_group,
        strip_query_fragment_expr(Snapshot.url),
    )

    def compute_total(query: Any) -> int:
        if effective_view == SearchView.pages:
            return query.with_entities(func.count(func.distinct(group_key))).scalar() or 0
        return query.with_entities(func.count(Snapshot.id)).scalar() or 0

    query = base_query
    tsquery = None
    vector_expr = None
    score_override = None
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
            search_mode = "relevance_fallback" if effective_sort == SearchSort.relevance else "newest"

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

    mode = search_mode or "newest"
    if ranking_version == RankingVersion.v2 and mode.startswith("relevance"):
        mode = f"{mode}_v2"

    offset = (page - 1) * pageSize

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

    has_ps_outlink_count = use_page_signals and _has_column(
        db, "page_signals", "outlink_count"
    )
    has_ps_pagerank = use_page_signals and _has_column(db, "page_signals", "pagerank")

    use_hubness = (
        ranking_version == RankingVersion.v2
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
        ranking_version == RankingVersion.v2
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
        if not rank_text:
            return 0.0
        if ranking_version != RankingVersion.v2 or not query_tokens or ranking_cfg is None:
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
            case((Snapshot.title.ilike(f"%{phrase_query}%"), 2), else_=0)
            if phrase_query
            else 0
        )
        score = 3 * title_hits + 2 * url_hits + snippet_hits + phrase_boost

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
        if effective_sort == SearchSort.relevance and rank_text:
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
            snap.url
        )
        if effective_view == SearchView.pages:
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
                rawSnapshotUrl=f"/api/snapshots/raw/{snap.id}",
                browseUrl=_build_browse_url(
                    snap.job_id, original_url, snap.capture_timestamp, snap.id
                ),
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
        .filter(~Source.code.in_(_PUBLIC_EXCLUDED_SOURCE_CODES))
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

        entry_record_id: Optional[int] = None
        entry_job_id: Optional[int] = None
        entry_browse_url: Optional[str] = None
        entry_preview_url: Optional[str] = None

        entry_groups = _candidate_entry_groups(source.base_url)
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
        if entry_record_id is None and source.base_url:
            host_variants = _candidate_entry_hosts(source.base_url)
            host_filters = []
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
            if _find_replay_preview_file(preview_dir, source.code, entry_job_id):
                entry_preview_url = f"/api/sources/{source.code}/preview?jobId={entry_job_id}"

        summaries.append(
            SourceSummarySchema(
                sourceCode=source.code,
                sourceName=source.name,
                baseUrl=source.base_url,
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


@router.get(
    "/sources/{source_code}/editions", response_model=List[SourceEditionSchema]
)
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
                host_filters = []
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
                entry_browse_url = _build_browse_url(
                    job_id, entry_url, entry_ts, entry_snapshot_id
                )

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

    resolved = _find_replay_preview_file(preview_dir, normalized_code, jobId)
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
            raise HTTPException(
                status_code=400, detail="timestamp must be a 14-digit UTC value"
            )

    candidate_urls = _candidate_resolve_urls(cleaned_url)
    if not candidate_urls:
        return ReplayResolveSchema(found=False)

    rows = (
        db.query(Snapshot.id, Snapshot.url, Snapshot.capture_timestamp, Snapshot.status_code)
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

    snap_id, resolved_url, capture_ts, _status = best

    return ReplayResolveSchema(
        found=True,
        snapshotId=snap_id,
        captureTimestamp=_format_capture_timestamp(capture_ts),
        resolvedUrl=resolved_url,
        browseUrl=_build_browse_url(jobId, resolved_url, capture_ts, snap_id),
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
                Snapshot.url,
                Snapshot.capture_timestamp,
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

    replay_url = _build_browse_url(snap.job_id, snap.url, snap.capture_timestamp, snap.id)
    snapshot_details_url = f"https://www.healtharchive.ca/snapshot/{snap.id}"
    snapshot_json_url = f"https://api.healtharchive.ca/api/snapshot/{snap.id}"
    replay_link_html = (
        f'<a class="ha-replay-link" href="{replay_url}" rel="noreferrer">Replay</a>'
        if replay_url
        else ""
    )

    banner = f"""
<style id="ha-replay-banner-css">
  #ha-replay-banner {{
    position: sticky;
    top: 0;
    z-index: 2147483647;
    border-bottom: 1px solid rgba(148, 163, 184, 0.35);
    background-color: rgba(255, 255, 255, 0.9);
    background-color: color-mix(in srgb, rgba(255, 255, 255, 0.9) 82%, transparent);
    font-family: system-ui, -apple-system, BlinkMacSystemFont, "SF Pro Text", "Segoe UI", sans-serif;
    font-size: 0.85rem;
    line-height: 1.25;
    -webkit-font-smoothing: antialiased;
    backdrop-filter: blur(10px) saturate(1.1);
    box-shadow: 0 8px 16px rgba(15, 23, 42, 0.04);
  }}

  #ha-replay-banner::after {{
    content: "";
    position: absolute;
    left: 0;
    right: 0;
    bottom: -23px;
    height: 27px;
    pointer-events: none;
    background: linear-gradient(
      to bottom,
      rgba(15, 23, 42, 0.02) 0%,
      rgba(15, 23, 42, 0) 100%
    );
  }}

  #ha-replay-banner * {{
    box-sizing: border-box;
  }}

  #ha-replay-banner .ha-replay-inner {{
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 0.75rem;
    padding: 0.8rem 1.25rem;
  }}

  #ha-replay-banner .ha-replay-left,
  #ha-replay-banner .ha-replay-right {{
    display: flex;
    align-items: center;
    gap: 0.45rem;
    flex-shrink: 0;
  }}

  #ha-replay-banner .ha-replay-pill {{
    display: inline-flex;
    align-items: center;
    gap: 0.35rem;
    padding: 0.18rem 0.6rem;
    border-radius: 999px;
    background: rgba(37, 99, 235, 0.1);
    border: 1px solid rgba(37, 99, 235, 0.25);
    color: #2563eb;
    font-weight: 650;
    white-space: nowrap;
  }}

  #ha-replay-banner .ha-replay-center {{
    flex: 1;
    min-width: 0;
    overflow: hidden;
    display: flex;
    flex-direction: column;
    align-items: flex-start;
    gap: 0.2rem;
  }}

  #ha-replay-banner .ha-replay-meta {{
    color: rgba(15, 23, 42, 0.7);
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    max-width: 100%;
  }}

  #ha-replay-banner .ha-replay-disclaimer {{
    color: rgba(15, 23, 42, 0.55);
    overflow: hidden;
    max-width: 100%;
    white-space: normal;
    display: -webkit-box;
    -webkit-line-clamp: 2;
    -webkit-box-orient: vertical;
  }}

  #ha-replay-banner a,
  #ha-replay-banner button {{
    appearance: none;
    border: 1px solid transparent;
    background: transparent;
    color: rgba(15, 23, 42, 0.72);
    border-radius: 999px;
    padding: 0.3rem 0.85rem;
    font: inherit;
    font-weight: 550;
    cursor: pointer;
    line-height: 1;
    text-decoration: none;
    transition: background 120ms ease, transform 120ms ease;
    display: inline-flex;
    align-items: center;
    justify-content: center;
  }}

  #ha-replay-banner a:hover,
  #ha-replay-banner button:hover {{
    color: rgba(15, 23, 42, 0.95);
    transform: translateY(-1px);
    background: rgba(148, 163, 184, 0.16);
    text-decoration: none;
  }}

  #ha-replay-banner .ha-replay-action-link {{
    background-color: #2563eb;
    color: #ffffff;
    box-shadow: 0 10px 24px rgba(37, 99, 235, 0.35);
  }}

  #ha-replay-banner .ha-replay-action-link:hover {{
    background-color: #1d4ed8;
    color: #ffffff;
  }}

  @media (max-width: 780px) {{
    #ha-replay-banner .ha-replay-center {{
      display: none;
    }}
  }}
</style>
<div id="ha-replay-banner" role="region" aria-label="HealthArchive snapshot header">
  <div class="ha-replay-inner">
    <div class="ha-replay-left">
      <a class="ha-replay-action-link" href="https://www.healtharchive.ca/archive" rel="noreferrer">\u2190 HealthArchive.ca</a>
      <span class="ha-replay-pill">Raw HTML</span>
    </div>
    <div class="ha-replay-center">
      <div class="ha-replay-meta">Raw HTML debug view</div>
      <div class="ha-replay-disclaimer">Independent archive \u00b7 Not an official government website \u00b7 Content may be outdated</div>
    </div>
    <div class="ha-replay-right">
      <a class="ha-replay-link" href="{snapshot_details_url}" rel="noreferrer">Snapshot details</a>
      {replay_link_html}
      <a class="ha-replay-link" href="{snapshot_json_url}" rel="noreferrer">Metadata JSON</a>
      <button type="button" class="ha-replay-link" id="ha-replay-hide" aria-label="Hide this banner">Hide</button>
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
        match = re.search(r"<body\\b[^>]*>", html, flags=re.IGNORECASE)
        if match:
            insert_at = match.end()
            html = html[:insert_at] + banner + html[insert_at:]
        else:
            html = banner + html
    except Exception:
        html = banner + html

    return HTMLResponse(content=html, media_type="text/html")


__all__ = ["router"]
