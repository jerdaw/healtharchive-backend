"""Tests for _find_job_log in vps-crawl-auto-recover.py.

Validates that the auto-recover watchdog picks the newest combined log by mtime,
even when the DB ``combined_log_path`` points to a stale log from a previous
crawl attempt.  This was the root cause of the watchdog reporting
``no_stalled_jobs`` while the metrics exporter correctly detected a stall.
"""

from __future__ import annotations

import importlib.util
import sys
import time
from pathlib import Path
from typing import Any

import pytest


def _load_script_module() -> Any:
    repo_root = Path(__file__).resolve().parents[1]
    script_path = repo_root / "scripts" / "vps-crawl-auto-recover.py"
    spec = importlib.util.spec_from_file_location("vps_crawl_auto_recover", script_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def mod():
    return _load_script_module()


def _make_running_job(mod: Any, **kwargs) -> Any:
    return mod.RunningJob(**kwargs)


class TestFindJobLog:
    """_find_job_log should prefer the newest log by mtime."""

    def test_prefers_newest_log_on_disk_over_stale_db_path(self, mod, tmp_path: Path):
        output_dir = tmp_path / "job_output"
        output_dir.mkdir()

        # Old log (what the DB combined_log_path points to)
        old_log = output_dir / "archive_old_attempt.combined.log"
        old_log.write_text("old log with no crawlStatus events\n")

        # Ensure a measurable mtime gap
        time.sleep(0.05)

        # New log (current crawl attempt, written later)
        new_log = output_dir / "archive_new_attempt.combined.log"
        new_log.write_text("new log\n")

        job = _make_running_job(
            mod,
            job_id=1,
            source_code="hc",
            started_at=None,
            output_dir=str(output_dir),
            combined_log_path=str(old_log),
        )

        result = mod._find_job_log(job)
        assert result == new_log

    def test_returns_db_path_when_it_is_the_newest(self, mod, tmp_path: Path):
        output_dir = tmp_path / "job_output"
        output_dir.mkdir()

        # Older log on disk
        older_log = output_dir / "archive_older.combined.log"
        older_log.write_text("older\n")

        time.sleep(0.05)

        # DB path is the newest
        db_log = output_dir / "archive_current.combined.log"
        db_log.write_text("current\n")

        job = _make_running_job(
            mod,
            job_id=2,
            source_code="phac",
            started_at=None,
            output_dir=str(output_dir),
            combined_log_path=str(db_log),
        )

        result = mod._find_job_log(job)
        assert result == db_log

    def test_falls_back_to_glob_when_db_path_missing(self, mod, tmp_path: Path):
        output_dir = tmp_path / "job_output"
        output_dir.mkdir()

        log_on_disk = output_dir / "archive_attempt_1.combined.log"
        log_on_disk.write_text("log content\n")

        job = _make_running_job(
            mod,
            job_id=3,
            source_code="cihr",
            started_at=None,
            output_dir=str(output_dir),
            combined_log_path=str(tmp_path / "nonexistent.combined.log"),
        )

        result = mod._find_job_log(job)
        assert result == log_on_disk

    def test_falls_back_to_glob_when_db_path_is_none(self, mod, tmp_path: Path):
        output_dir = tmp_path / "job_output"
        output_dir.mkdir()

        log_on_disk = output_dir / "archive_attempt_1.combined.log"
        log_on_disk.write_text("log content\n")

        job = _make_running_job(
            mod,
            job_id=4,
            source_code="hc",
            started_at=None,
            output_dir=str(output_dir),
            combined_log_path=None,
        )

        result = mod._find_job_log(job)
        assert result == log_on_disk

    def test_returns_none_when_no_logs_anywhere(self, mod, tmp_path: Path):
        output_dir = tmp_path / "job_output"
        output_dir.mkdir()

        job = _make_running_job(
            mod,
            job_id=5,
            source_code="hc",
            started_at=None,
            output_dir=str(output_dir),
            combined_log_path=None,
        )

        result = mod._find_job_log(job)
        assert result is None

    def test_returns_none_when_no_output_dir(self, mod):
        job = _make_running_job(
            mod,
            job_id=6,
            source_code="hc",
            started_at=None,
            output_dir=None,
            combined_log_path=None,
        )

        result = mod._find_job_log(job)
        assert result is None
