from __future__ import annotations

from dataclasses import dataclass, field
from threading import Lock


@dataclass
class _SearchMetrics:
    lock: Lock = field(default_factory=Lock)

    count: int = 0
    error_count: int = 0

    duration_seconds_sum: float = 0.0
    duration_seconds_max: float = 0.0

    # Prometheus-style cumulative histogram buckets.
    bucket_le_005: int = 0
    bucket_le_01: int = 0
    bucket_le_03: int = 0
    bucket_le_1: int = 0
    bucket_le_3: int = 0
    bucket_le_inf: int = 0

    # Simple breakdown counters.
    relevance_fts: int = 0
    relevance_fallback: int = 0
    relevance_fuzzy: int = 0
    boolean: int = 0
    url: int = 0
    pages_fastpath: int = 0
    newest: int = 0


SEARCH_METRICS = _SearchMetrics()


def observe_search_request(*, duration_seconds: float, mode: str, ok: bool) -> None:
    """
    Record a single /api/search request observation.

    Notes:
    - These metrics are per-process and reset on restart.
    - We keep the label-space intentionally small to avoid cardinality issues.
    """
    m = SEARCH_METRICS
    with m.lock:
        m.count += 1
        if not ok:
            m.error_count += 1

        m.duration_seconds_sum += float(duration_seconds)
        m.duration_seconds_max = max(m.duration_seconds_max, float(duration_seconds))

        if duration_seconds <= 0.05:
            m.bucket_le_005 += 1
        if duration_seconds <= 0.1:
            m.bucket_le_01 += 1
        if duration_seconds <= 0.3:
            m.bucket_le_03 += 1
        if duration_seconds <= 1.0:
            m.bucket_le_1 += 1
        if duration_seconds <= 3.0:
            m.bucket_le_3 += 1
        m.bucket_le_inf += 1

        if mode.startswith("relevance_fts"):
            m.relevance_fts += 1
        elif mode.startswith("relevance_fallback"):
            m.relevance_fallback += 1
        elif mode.startswith("relevance_fuzzy"):
            m.relevance_fuzzy += 1
        elif mode == "boolean":
            m.boolean += 1
        elif mode == "url":
            m.url += 1
        elif mode == "pages_fastpath":
            m.pages_fastpath += 1
        else:
            m.newest += 1


def render_search_metrics_prometheus() -> list[str]:
    """
    Render search-related metrics in Prometheus text exposition format.
    """
    m = SEARCH_METRICS
    with m.lock:
        lines = []

        lines.append("# HELP healtharchive_search_requests_total Total /api/search requests")
        lines.append("# TYPE healtharchive_search_requests_total counter")
        lines.append(f"healtharchive_search_requests_total {m.count}")

        lines.append("# HELP healtharchive_search_errors_total Total /api/search requests that raised an error")
        lines.append("# TYPE healtharchive_search_errors_total counter")
        lines.append(f"healtharchive_search_errors_total {m.error_count}")

        lines.append("# HELP healtharchive_search_duration_seconds /api/search latency histogram (per-process)")
        lines.append("# TYPE healtharchive_search_duration_seconds histogram")
        lines.append(f'healtharchive_search_duration_seconds_bucket{{le="0.05"}} {m.bucket_le_005}')
        lines.append(f'healtharchive_search_duration_seconds_bucket{{le="0.1"}} {m.bucket_le_01}')
        lines.append(f'healtharchive_search_duration_seconds_bucket{{le="0.3"}} {m.bucket_le_03}')
        lines.append(f'healtharchive_search_duration_seconds_bucket{{le="1"}} {m.bucket_le_1}')
        lines.append(f'healtharchive_search_duration_seconds_bucket{{le="3"}} {m.bucket_le_3}')
        lines.append(f'healtharchive_search_duration_seconds_bucket{{le="+Inf"}} {m.bucket_le_inf}')
        lines.append(f"healtharchive_search_duration_seconds_sum {m.duration_seconds_sum}")
        lines.append(f"healtharchive_search_duration_seconds_count {m.count}")

        lines.append("# HELP healtharchive_search_mode_total /api/search mode breakdown (per-process)")
        lines.append("# TYPE healtharchive_search_mode_total counter")
        lines.append(f'healtharchive_search_mode_total{{mode="relevance_fts"}} {m.relevance_fts}')
        lines.append(f'healtharchive_search_mode_total{{mode="relevance_fallback"}} {m.relevance_fallback}')
        lines.append(f'healtharchive_search_mode_total{{mode="relevance_fuzzy"}} {m.relevance_fuzzy}')
        lines.append(f'healtharchive_search_mode_total{{mode="boolean"}} {m.boolean}')
        lines.append(f'healtharchive_search_mode_total{{mode="url"}} {m.url}')
        lines.append(f'healtharchive_search_mode_total{{mode="pages_fastpath"}} {m.pages_fastpath}')
        lines.append(f'healtharchive_search_mode_total{{mode="newest"}} {m.newest}')

        lines.append("# HELP healtharchive_search_duration_seconds_max Max observed /api/search latency (seconds)")
        lines.append("# TYPE healtharchive_search_duration_seconds_max gauge")
        lines.append(f"healtharchive_search_duration_seconds_max {m.duration_seconds_max}")

        return lines


__all__ = ["observe_search_request", "render_search_metrics_prometheus"]
