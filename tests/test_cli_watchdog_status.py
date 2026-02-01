"""Tests for ha-backend watchdog-status command."""

from __future__ import annotations

import json
import sys
from io import StringIO
from pathlib import Path

from ha_backend import cli as cli_module
from ha_backend import db as db_module
from ha_backend.db import Base, get_engine, get_session
from ha_backend.models import ArchiveJob, Source


def _init_test_db(tmp_path: Path, monkeypatch) -> None:
    """Point the ORM at a throwaway SQLite database and create all tables."""
    db_path = tmp_path / "cli_watchdog_status.db"
    monkeypatch.setenv("HEALTHARCHIVE_DATABASE_URL", f"sqlite:///{db_path}")

    db_module._engine = None
    db_module._SessionLocal = None

    engine = get_engine()
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)


def _run_cli(args_list: list[str]) -> str:
    """Run CLI command and capture output."""
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


def test_watchdog_status_command_basic_output(monkeypatch, tmp_path) -> None:
    """Test that watchdog-status command produces expected output sections."""
    _init_test_db(tmp_path, monkeypatch)

    # Create watchdog state files (path matches monkeypatched /srv/healtharchive/...)
    watchdog_dir = tmp_path / "srv" / "healtharchive" / "ops" / "watchdog"
    watchdog_dir.mkdir(parents=True, exist_ok=True)

    crawl_state = {
        "recoveries": {
            "6": ["2026-01-01T09:35:10+00:00", "2026-02-01T02:15:11+00:00"],
            "7": ["2026-01-12T05:05:02+00:00"],
        }
    }
    (watchdog_dir / "crawl-auto-recover.json").write_text(json.dumps(crawl_state))

    storage_state = {
        "last_healthy_utc": "2026-02-01T12:13:10+00:00",
        "last_apply_ok": 1,
        "last_apply_utc": "2026-01-24T06:28:01+00:00",
        "recoveries": {"global": ["2026-01-24T06:28:01+00:00"]},
        "observations": {},
    }
    (watchdog_dir / "storage-hotpath-auto-recover.json").write_text(json.dumps(storage_state))

    # Mock sentinel files
    sentinel_dir = tmp_path / "etc" / "healtharchive"
    sentinel_dir.mkdir(parents=True, exist_ok=True)
    (sentinel_dir / "crawl-auto-recover-enabled").touch()

    # Mock paths - use module object directly for reliability
    def mock_path(p):
        if p.startswith("/srv") or p.startswith("/etc") or p.startswith("/opt"):
            return tmp_path / p.lstrip("/")
        return Path(p)

    monkeypatch.setattr(cli_module, "Path", mock_path)

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

        job = ArchiveJob(
            source_id=source.id,
            name="job-running",
            output_dir="/tmp/running",
            status="running",
        )
        session.add(job)
        session.commit()

    output = _run_cli(["watchdog-status"])

    # Verify key sections are present
    assert "HealthArchive Watchdog Status" in output
    assert "[Crawl Auto-Recovery]" in output
    assert "[Storage Hot-Path Recovery]" in output
    assert "[Disk Cleanup]" in output
    assert "[Current Health]" in output

    # Verify data is shown
    assert "3 total recorded" in output  # Crawl recoveries
    assert "1 total recorded" in output  # Storage recoveries
    assert "Running jobs:" in output


def test_watchdog_status_handles_missing_files(monkeypatch, tmp_path) -> None:
    """Test watchdog-status handles missing state files gracefully."""
    _init_test_db(tmp_path, monkeypatch)

    # Don't create any watchdog files - mock paths
    def mock_path(p):
        if p.startswith("/srv") or p.startswith("/etc") or p.startswith("/opt"):
            return tmp_path / p.lstrip("/")
        return Path(p)

    monkeypatch.setattr(cli_module, "Path", mock_path)

    output = _run_cli(["watchdog-status"])

    # Should still show all sections
    assert "[Crawl Auto-Recovery]" in output
    assert "[Storage Hot-Path Recovery]" in output
    assert "0 total recorded" in output
    assert "No (sentinel missing)" in output


def test_watchdog_status_shows_stale_mounts(monkeypatch, tmp_path) -> None:
    """Test that stale mounts are detected and shown."""
    _init_test_db(tmp_path, monkeypatch)

    watchdog_dir = tmp_path / "srv" / "healtharchive" / "ops" / "watchdog"
    watchdog_dir.mkdir(parents=True, exist_ok=True)

    # Storage state with stale mount observations
    storage_state = {
        "observations": {
            "job:7": {
                "kind": "job_output_dir",
                "job_id": 7,
                "source": "phac",
                "path": "/srv/healtharchive/jobs/phac/20260101T000502Z__phac-20260101",
                "errno": 107,
            },
            "job:8": {
                "kind": "job_output_dir",
                "job_id": 8,
                "source": "cihr",
                "path": "/srv/healtharchive/jobs/cihr/20260101T000502Z__cihr-20260101",
                "errno": 107,
            },
        },
        "recoveries": {"global": []},
    }
    (watchdog_dir / "storage-hotpath-auto-recover.json").write_text(json.dumps(storage_state))

    def mock_path(p):
        if p.startswith("/srv") or p.startswith("/etc") or p.startswith("/opt"):
            return tmp_path / p.lstrip("/")
        return Path(p)

    monkeypatch.setattr(cli_module, "Path", mock_path)

    output = _run_cli(["watchdog-status"])

    # Should show detected stale mounts
    assert "2 stale target(s)" in output
    assert "2 detected" in output  # In Current Health
    assert "Clear stale mounts" in output  # In Recommendations
