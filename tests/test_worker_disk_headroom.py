"""Tests for worker disk headroom checking."""
from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from ha_backend import db as db_module
from ha_backend.db import Base, get_engine, get_session
from ha_backend.models import ArchiveJob, Source
from ha_backend.worker.main import (
    DISK_HEADROOM_CHECK_PATH,
    DISK_HEADROOM_THRESHOLD_PERCENT,
    _check_disk_headroom,
    run_worker_loop,
)


def _init_test_db(tmp_path: Path, monkeypatch) -> None:
    """Point the ORM at a throwaway SQLite database and create all tables."""
    db_path = tmp_path / "worker-disk.db"
    monkeypatch.setenv("HEALTHARCHIVE_DATABASE_URL", f"sqlite:///{db_path}")

    db_module._engine = None
    db_module._SessionLocal = None

    engine = get_engine()
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)


def test_check_disk_headroom_sufficient(tmp_path) -> None:
    """Test disk headroom check when plenty of space is available."""
    # Most systems running tests will have >85% free space
    has_headroom, usage = _check_disk_headroom()

    # We can't assert the exact value since it depends on the system,
    # but we can verify the function returns valid data
    assert isinstance(has_headroom, bool)
    assert isinstance(usage, int)
    assert 0 <= usage <= 100


def test_check_disk_headroom_with_mock_statvfs(monkeypatch) -> None:
    """Test disk headroom check with mocked filesystem stats."""

    class MockStatVFS:
        def __init__(self, usage_percent: int):
            # Calculate blocks to achieve desired usage percentage
            self.f_frsize = 4096
            self.f_blocks = 1000000
            # Free blocks calculated to give desired usage
            used_blocks = int(self.f_blocks * usage_percent / 100)
            self.f_bavail = self.f_blocks - used_blocks

    # Test with low usage (70% - should have headroom)
    monkeypatch.setattr("os.statvfs", lambda path: MockStatVFS(70))
    has_headroom, usage = _check_disk_headroom()
    assert has_headroom is True
    assert usage == 70

    # Test with high usage (90% - should not have headroom)
    monkeypatch.setattr("os.statvfs", lambda path: MockStatVFS(90))
    has_headroom, usage = _check_disk_headroom()
    assert has_headroom is False
    assert usage == 90

    # Test at threshold (85% - should not have headroom)
    monkeypatch.setattr("os.statvfs", lambda path: MockStatVFS(85))
    has_headroom, usage = _check_disk_headroom()
    assert has_headroom is False
    assert usage == 85

    # Test just below threshold (84% - should have headroom)
    monkeypatch.setattr("os.statvfs", lambda path: MockStatVFS(84))
    has_headroom, usage = _check_disk_headroom()
    assert has_headroom is True
    assert usage == 84


def test_check_disk_headroom_oserror_fallback(monkeypatch) -> None:
    """Test that OSError during disk check returns safe defaults."""

    def raise_oserror(path):
        raise OSError("Simulated disk check failure")

    monkeypatch.setattr("os.statvfs", raise_oserror)

    # Should return True (has headroom) on error to avoid blocking work
    has_headroom, usage = _check_disk_headroom()
    assert has_headroom is True
    assert usage == 0


def test_worker_skips_job_when_disk_full(monkeypatch, tmp_path) -> None:
    """Test that worker skips queued jobs when disk usage is above threshold."""
    _init_test_db(tmp_path, monkeypatch)

    archive_root = tmp_path / "jobs"
    monkeypatch.setenv("HEALTHARCHIVE_ARCHIVE_ROOT", str(archive_root))
    monkeypatch.setenv("HEALTHARCHIVE_TOOL_CMD", "echo")

    # Create a queued job
    with get_session() as session:
        source = Source(
            code="hc",
            name="Health Canada",
            base_url="https://www.canada.ca/en/health-canada.html",
            description="HC",
            enabled=True,
        )
        session.add(source)
        session.flush()

        job = ArchiveJob(
            source_id=source.id,
            name="disk-test-job",
            output_dir=str(archive_root / "hc" / "jobdir"),
            status="queued",
        )
        session.add(job)
        session.flush()
        job_id = job.id

    # Mock disk as full (90% usage)
    class MockStatVFS:
        f_frsize = 4096
        f_blocks = 1000000
        f_bavail = 100000  # 90% used

    monkeypatch.setattr("os.statvfs", lambda path: MockStatVFS())

    # Worker should skip the job
    run_worker_loop(poll_interval=1, run_once=True)

    # Job should still be queued (not picked up)
    with get_session() as session:
        loaded_job = session.get(ArchiveJob, job_id)
        assert loaded_job is not None
        assert loaded_job.status == "queued"


def test_worker_processes_job_when_disk_has_headroom(monkeypatch, tmp_path) -> None:
    """Test that worker processes queued jobs when disk usage is below threshold."""
    _init_test_db(tmp_path, monkeypatch)

    archive_root = tmp_path / "jobs"
    monkeypatch.setenv("HEALTHARCHIVE_ARCHIVE_ROOT", str(archive_root))
    monkeypatch.setenv("HEALTHARCHIVE_TOOL_CMD", "echo")

    # Create a queued job
    with get_session() as session:
        source = Source(
            code="hc",
            name="Health Canada",
            base_url="https://www.canada.ca/en/health-canada.html",
            description="HC",
            enabled=True,
        )
        session.add(source)
        session.flush()

        job = ArchiveJob(
            source_id=source.id,
            name="disk-ok-job",
            output_dir=str(archive_root / "hc" / "jobdir"),
            status="queued",
        )
        session.add(job)
        session.flush()
        job_id = job.id

    # Mock disk as having headroom (70% usage)
    class MockStatVFS:
        f_frsize = 4096
        f_blocks = 1000000
        f_bavail = 300000  # 70% used

    monkeypatch.setattr("os.statvfs", lambda path: MockStatVFS())

    # Mock the crawl and index functions
    def fake_run_persistent_job(jid: int) -> int:
        with get_session() as session:
            j = session.get(ArchiveJob, jid)
            assert j is not None
            j.status = "completed"
            j.crawler_exit_code = 0
        return 0

    def fake_index_job(jid: int) -> int:
        with get_session() as session:
            j = session.get(ArchiveJob, jid)
            assert j is not None
            j.status = "indexed"
            j.indexed_page_count = 10
        return 0

    monkeypatch.setattr("ha_backend.worker.main.run_persistent_job", fake_run_persistent_job)
    monkeypatch.setattr("ha_backend.worker.main.index_job", fake_index_job)

    # Worker should process the job
    run_worker_loop(poll_interval=1, run_once=True)

    # Job should be indexed
    with get_session() as session:
        loaded_job = session.get(ArchiveJob, job_id)
        assert loaded_job is not None
        assert loaded_job.status == "indexed"
