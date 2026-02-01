"""Tests for ha-backend status command."""

from __future__ import annotations

import sys
from io import StringIO
from pathlib import Path

from ha_backend import cli as cli_module
from ha_backend import db as db_module
from ha_backend.db import Base, get_engine, get_session
from ha_backend.models import ArchiveJob, Source


def _init_test_db(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "cli_status.db"
    monkeypatch.setenv("HEALTHARCHIVE_DATABASE_URL", f"sqlite:///{db_path}")

    db_module._engine = None
    db_module._SessionLocal = None

    engine = get_engine()
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)


def _run_cli(args_list: list[str]) -> str:
    parser = cli_module.build_parser()
    args = parser.parse_args(args_list)

    stdout = StringIO()
    old_stdout = sys.stdout
    try:
        sys.stdout = stdout
        args.func(args)
    finally:
        sys.stdout = old_stdout

    return stdout.getvalue()


def test_status_command_basic_output(monkeypatch, tmp_path) -> None:
    """Test that status command produces expected output sections."""
    _init_test_db(tmp_path, monkeypatch)

    # Create some test jobs
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

        # Create jobs in different states
        for status in ["indexed", "running", "retryable"]:
            job = ArchiveJob(
                source_id=source.id,
                name=f"job-{status}",
                output_dir=f"/tmp/{status}",
                status=status,
            )
            session.add(job)
        session.commit()

    output = _run_cli(["status"])

    # Verify key sections are present
    assert "HealthArchive Backend – Status Overview" in output
    assert "[Worker]" in output
    assert "[Disk]" in output
    assert "[Jobs]" in output
    assert "[Automation]" in output

    # Verify job counts (accounting for ANSI color codes)
    assert "Total: 3" in output
    assert "Running:" in output and "1" in output
    assert "Retryable:" in output
    assert "Indexed:" in output


def test_status_command_with_no_jobs(monkeypatch, tmp_path) -> None:
    """Test status command output when there are no jobs."""
    _init_test_db(tmp_path, monkeypatch)

    output = _run_cli(["status"])

    # Should still show all sections
    assert "[Worker]" in output
    assert "[Disk]" in output
    assert "[Jobs]" in output
    assert "Total: 0" in output


def test_status_command_disk_usage_shown(monkeypatch, tmp_path) -> None:
    """Test that disk usage information is displayed."""
    _init_test_db(tmp_path, monkeypatch)

    output = _run_cli(["status"])

    # Should show disk usage with percentage
    assert "[Disk]" in output
    assert "%" in output
    assert "used" in output or "free" in output


def test_status_command_handles_storage_box_check(monkeypatch, tmp_path) -> None:
    """Test that Storage Box mount check handles missing mount gracefully."""
    _init_test_db(tmp_path, monkeypatch)

    # Mock a non-existent storage box path
    monkeypatch.setenv("HEALTHARCHIVE_STORAGEBOX_PATH", "/nonexistent/storagebox")

    output = _run_cli(["status"])

    # Should still complete without crashing
    assert "[Storage Box]" in output
