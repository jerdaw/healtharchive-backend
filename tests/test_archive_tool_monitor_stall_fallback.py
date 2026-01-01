from __future__ import annotations

import argparse
import threading
import time
from pathlib import Path
from queue import Queue
from typing import Any, cast

from archive_tool.monitor import CrawlMonitor
from archive_tool.state import CrawlState


class _FakePopen:
    def poll(self):
        return None


def test_monitor_stall_triggers_when_pending_unknown(tmp_path: Path) -> None:
    state = CrawlState(tmp_path, initial_workers=1)
    # Simulate progress happened long ago.
    now = time.monotonic()
    state.last_crawled_count = 10
    state.last_pending_count = -1  # unknown
    state.last_progress_timestamp = now - (31 * 60)

    args = argparse.Namespace(
        enable_monitoring=True,
        monitor_interval_seconds=30,
        stall_timeout_minutes=30,
        error_threshold_timeout=10,
        error_threshold_http=10,
    )

    q: Queue = Queue()
    m = CrawlMonitor(
        container_id="deadbeef",
        process_handle=cast(Any, _FakePopen()),
        state=state,
        args=args,
        output_queue=q,
        stop_event=threading.Event(),
    )

    assert m._check_stall_and_error_conditions(time.monotonic()) is True
    msg = q.get_nowait()
    assert msg["status"] == "stalled"


def test_monitor_error_threshold_does_not_reset_last_progress_timestamp(tmp_path: Path) -> None:
    state = CrawlState(tmp_path, initial_workers=1)
    now = time.monotonic()
    state.last_crawled_count = 10
    state.last_pending_count = 1
    state.last_progress_timestamp = now - 60
    state.error_counts["timeout"] = 10

    args = argparse.Namespace(
        enable_monitoring=True,
        monitor_interval_seconds=30,
        stall_timeout_minutes=30,
        error_threshold_timeout=10,
        error_threshold_http=10,
    )

    q: Queue = Queue()
    m = CrawlMonitor(
        container_id="deadbeef",
        process_handle=cast(Any, _FakePopen()),
        state=state,
        args=args,
        output_queue=q,
        stop_event=threading.Event(),
    )

    before = state.last_progress_timestamp
    assert m._check_stall_and_error_conditions(now) is True
    msg = q.get_nowait()
    assert msg["status"] == "error"
    assert msg["reason"] == "timeout_threshold"
    assert state.last_progress_timestamp == before
