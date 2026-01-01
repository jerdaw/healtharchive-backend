from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from ha_backend.crawl_stats import parse_crawl_log_progress


def _dt(ts: str) -> datetime:
    # Helper for assertions.
    if ts.endswith("Z"):
        ts = f"{ts[:-1]}+00:00"
    return datetime.fromisoformat(ts).astimezone(timezone.utc)


def test_parse_crawl_log_progress_finds_last_crawled_change(tmp_path: Path) -> None:
    log_path = tmp_path / "archive_initial_crawl_123.combined.log"
    events = [
        ("2026-01-01T00:00:00.000Z", 10),
        ("2026-01-01T00:05:00.000Z", 11),
        ("2026-01-01T00:10:00.000Z", 11),
        ("2026-01-01T00:15:00.000Z", 11),
    ]
    lines = []
    for ts, crawled in events:
        lines.append(
            json.dumps(
                {
                    "timestamp": ts,
                    "logLevel": "info",
                    "context": "crawlStatus",
                    "message": "Crawl statistics",
                    "details": {"crawled": crawled, "total": 100, "pending": 1, "failed": 0},
                }
            )
        )
    # Add some noise lines.
    lines.insert(0, "not json")
    lines.insert(1, json.dumps({"context": "other", "message": "hello"}))
    log_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    progress = parse_crawl_log_progress(log_path)
    assert progress is not None
    assert progress.last_status.crawled == 11
    assert progress.last_status.timestamp_utc == _dt("2026-01-01T00:15:00.000Z")
    assert progress.last_crawled_change_timestamp_utc == _dt("2026-01-01T00:05:00.000Z")
