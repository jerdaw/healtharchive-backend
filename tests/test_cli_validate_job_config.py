from __future__ import annotations

import sys
from io import StringIO
from pathlib import Path

from ha_backend import cli as cli_module
from ha_backend import db as db_module
from ha_backend.db import Base, get_engine, get_session
from ha_backend.job_registry import create_job_for_source
from ha_backend.models import ArchiveJob, Source
from ha_backend.seeds import seed_sources


def _init_test_db(tmp_path: Path, monkeypatch) -> None:
    """
    Point the ORM at a throwaway SQLite database and create all tables.
    """
    db_path = tmp_path / "cli_validate_job.db"
    monkeypatch.setenv("HEALTHARCHIVE_DATABASE_URL", f"sqlite:///{db_path}")

    db_module._engine = None
    db_module._SessionLocal = None

    engine = get_engine()
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)


def test_validate_job_config_runs_dry_run_without_status_change(tmp_path, monkeypatch) -> None:
    """
    validate-job-config should invoke archive_tool in dry-run mode for a job
    without changing its status or timestamps.
    """
    _init_test_db(tmp_path, monkeypatch)

    archive_root = tmp_path / "jobs"
    monkeypatch.setenv("HEALTHARCHIVE_ARCHIVE_ROOT", str(archive_root))

    # Seed sources and create a job using the registry.
    with get_session() as session:
        seed_sources(session)

    with get_session() as session:
        job_row = create_job_for_source("hc", session=session)
        job_id = job_row.id
        original_status = job_row.status
        original_started = job_row.started_at
        original_finished = job_row.finished_at

    # Point archive_tool_cmd at echo so the dry-run does not require Docker
    # inside tests; archive_tool itself still enforces its own invariants.
    monkeypatch.setenv("HEALTHARCHIVE_TOOL_CMD", "archive-tool")

    parser = cli_module.build_parser()
    args = parser.parse_args(["validate-job-config", "--id", str(job_id)])

    stdout = StringIO()
    old_stdout = sys.stdout
    try:
        sys.stdout = stdout
        args.func(args)
    finally:
        sys.stdout = old_stdout

    out = stdout.getvalue()
    assert "HealthArchive Backend – Validate Job Config" in out
    assert f"Job ID:      {job_id}" in out

    # Ensure the job status and timestamps were not mutated by validation.
    with get_session() as session:
        stored = session.get(ArchiveJob, job_id)
        assert stored is not None
        assert stored.status == original_status
        assert stored.started_at == original_started
        assert stored.finished_at == original_finished

