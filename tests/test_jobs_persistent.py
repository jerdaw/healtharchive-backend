from __future__ import annotations

from pathlib import Path

from ha_backend import db as db_module
from ha_backend.db import Base, get_engine, get_session
from ha_backend.job_registry import create_job_for_source
from ha_backend.jobs import run_persistent_job
from ha_backend.models import ArchiveJob, Source
from ha_backend.seeds import seed_sources


def _init_test_db(tmp_path: Path, monkeypatch) -> None:
    """
    Point the ORM at a throwaway SQLite database and create all tables.
    """
    db_path = tmp_path / "jobs_persistent.db"
    monkeypatch.setenv("HEALTHARCHIVE_DATABASE_URL", f"sqlite:///{db_path}")

    # Reset cached engine/session so we pick up the new URL.
    db_module._engine = None
    db_module._SessionLocal = None

    engine = get_engine()
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)


def test_run_persistent_job_updates_status_and_exit_code(tmp_path, monkeypatch) -> None:
    """
    run_persistent_job should honour DB configuration, invoke the runner, and
    update job status and exit code.

    We point the archive_tool command at 'echo' to avoid hitting Docker.
    """
    _init_test_db(tmp_path, monkeypatch)

    # Use a temp archive root and a harmless tool command for the test.
    archive_root = tmp_path / "jobs"
    monkeypatch.setenv("HEALTHARCHIVE_ARCHIVE_ROOT", str(archive_root))
    monkeypatch.setenv("HEALTHARCHIVE_TOOL_CMD", "echo")

    with get_session() as session:
        # Ensure Source rows exist.
        seed_sources(session)

    # Create a queued job for 'hc'.
    with get_session() as session:
        job_row = create_job_for_source("hc", session=session)
        job_id = job_row.id

    # Run the job via the persistent runner.
    rc = run_persistent_job(job_id)
    assert rc == 0

    # Verify that the job row was updated.
    with get_session() as session:
        stored = session.get(ArchiveJob, job_id)
        assert stored is not None
        assert stored.status == "completed"
        assert stored.crawler_exit_code == 0
        assert stored.started_at is not None
        assert stored.finished_at is not None

