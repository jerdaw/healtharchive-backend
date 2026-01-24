from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from ha_backend import db as db_module
from ha_backend.db import Base, get_engine, get_session
from ha_backend.models import ArchiveJob, Source
from ha_backend.worker.main import MAX_CRAWL_RETRIES, run_worker_loop


def _init_test_db(tmp_path: Path, monkeypatch) -> None:
    """
    Point the ORM at a throwaway SQLite database and create all tables.
    """
    db_path = tmp_path / "worker.db"
    monkeypatch.setenv("HEALTHARCHIVE_DATABASE_URL", f"sqlite:///{db_path}")

    db_module._engine = None
    db_module._SessionLocal = None

    engine = get_engine()
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)


def test_worker_processes_queued_job_and_indexes(monkeypatch, tmp_path) -> None:
    """
    The worker should take a queued job, run crawl and indexing, and mark it indexed.
    """
    _init_test_db(tmp_path, monkeypatch)

    # Use a temp archive root and a harmless tool command.
    archive_root = tmp_path / "jobs"
    monkeypatch.setenv("HEALTHARCHIVE_ARCHIVE_ROOT", str(archive_root))
    monkeypatch.setenv("HEALTHARCHIVE_TOOL_CMD", "echo")

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
            name="worker-test",
            output_dir=str(archive_root / "hc" / "jobdir"),
            status="queued",
        )
        session.add(job)
        session.flush()
        job_id = job.id

    # Monkeypatch crawl and index helpers to avoid invoking external tools.
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
            j.indexed_page_count = 42
        return 0

    monkeypatch.setattr("ha_backend.worker.main.run_persistent_job", fake_run_persistent_job)
    monkeypatch.setattr("ha_backend.worker.main.index_job", fake_index_job)

    # Single iteration should process the job fully.
    run_worker_loop(poll_interval=1, run_once=True)

    with get_session() as session:
        loaded_job = session.get(ArchiveJob, job_id)
        assert loaded_job is not None
        assert loaded_job.status == "indexed"
        assert loaded_job.retry_count == 0
        assert loaded_job.indexed_page_count == 42


def test_worker_marks_failed_job_retryable_until_limit(monkeypatch, tmp_path) -> None:
    """
    The worker should mark failed crawls as retryable until MAX_CRAWL_RETRIES is reached.
    """
    _init_test_db(tmp_path, monkeypatch)

    archive_root = tmp_path / "jobs"
    monkeypatch.setenv("HEALTHARCHIVE_ARCHIVE_ROOT", str(archive_root))
    monkeypatch.setenv("HEALTHARCHIVE_TOOL_CMD", "echo")

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
            name="worker-fail",
            output_dir=str(archive_root / "hc" / "jobdir2"),
            status="queued",
            retry_count=0,
        )
        session.add(job)
        session.flush()
        job_id = job.id

    def failing_run_persistent_job(jid: int) -> int:
        with get_session() as session:
            j = session.get(ArchiveJob, jid)
            assert j is not None
            j.status = "failed"
            j.crawler_exit_code = 1
        return 1

    monkeypatch.setattr("ha_backend.worker.main.run_persistent_job", failing_run_persistent_job)

    # First attempt: should mark as retryable and increment retry_count.
    run_worker_loop(poll_interval=1, run_once=True)
    with get_session() as session:
        loaded_job = session.get(ArchiveJob, job_id)
        assert loaded_job is not None
        assert loaded_job.status == "retryable"
        assert loaded_job.retry_count == 1

    # Set retry_count just below the max and run again, then once more to
    # verify that we eventually stop retrying.
    with get_session() as session:
        loaded_job = session.get(ArchiveJob, job_id)
        assert loaded_job is not None
        loaded_job.status = "retryable"
        loaded_job.retry_count = MAX_CRAWL_RETRIES - 1

    run_worker_loop(poll_interval=1, run_once=True)
    run_worker_loop(poll_interval=1, run_once=True)
    with get_session() as session:
        loaded_job = session.get(ArchiveJob, job_id)
        assert loaded_job is not None
        # After exceeding the max retries, job should remain failed.
        assert loaded_job.retry_count >= MAX_CRAWL_RETRIES
        assert loaded_job.status == "failed"


def test_worker_does_not_consume_retry_budget_on_storage_infra_error(monkeypatch, tmp_path) -> None:
    _init_test_db(tmp_path, monkeypatch)

    archive_root = tmp_path / "jobs"
    monkeypatch.setenv("HEALTHARCHIVE_ARCHIVE_ROOT", str(archive_root))
    monkeypatch.setenv("HEALTHARCHIVE_TOOL_CMD", "echo")

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
            name="worker-infra",
            output_dir=str(archive_root / "hc" / "jobdir3"),
            status="queued",
            retry_count=0,
        )
        session.add(job)
        session.flush()
        job_id = job.id

    def infra_failing_run_persistent_job(jid: int) -> int:
        with get_session() as session:
            j = session.get(ArchiveJob, jid)
            assert j is not None
            j.status = "retryable"
            j.crawler_status = "infra_error"
            j.crawler_exit_code = None
        return 1

    monkeypatch.setattr(
        "ha_backend.worker.main.run_persistent_job", infra_failing_run_persistent_job
    )

    run_worker_loop(poll_interval=1, run_once=True)

    with get_session() as session:
        loaded_job = session.get(ArchiveJob, job_id)
        assert loaded_job is not None
        assert loaded_job.status == "retryable"
        assert loaded_job.retry_count == 0


def test_worker_does_not_auto_retry_config_errors(monkeypatch, tmp_path) -> None:
    _init_test_db(tmp_path, monkeypatch)

    archive_root = tmp_path / "jobs"
    monkeypatch.setenv("HEALTHARCHIVE_ARCHIVE_ROOT", str(archive_root))
    monkeypatch.setenv("HEALTHARCHIVE_TOOL_CMD", "echo")

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
            name="worker-infra-config",
            output_dir=str(archive_root / "hc" / "jobdir4"),
            status="queued",
            retry_count=0,
        )
        session.add(job)
        session.flush()
        job_id = job.id

    def config_failing_run_persistent_job(jid: int) -> int:
        with get_session() as session:
            j = session.get(ArchiveJob, jid)
            assert j is not None
            j.status = "failed"
            j.crawler_status = "infra_error_config"
            j.crawler_exit_code = None
        return 1

    monkeypatch.setattr(
        "ha_backend.worker.main.run_persistent_job", config_failing_run_persistent_job
    )

    run_worker_loop(poll_interval=1, run_once=True)

    with get_session() as session:
        loaded_job = session.get(ArchiveJob, job_id)
        assert loaded_job is not None
        assert loaded_job.status == "failed"
        assert loaded_job.retry_count == 0


def test_worker_skips_recent_infra_error_jobs(monkeypatch, tmp_path) -> None:
    """
    The worker should avoid immediately re-processing jobs that most recently
    failed due to infra_error, so it does not tight-loop and spam alerts.
    """
    _init_test_db(tmp_path, monkeypatch)

    archive_root = tmp_path / "jobs"
    monkeypatch.setenv("HEALTHARCHIVE_ARCHIVE_ROOT", str(archive_root))
    monkeypatch.setenv("HEALTHARCHIVE_TOOL_CMD", "echo")

    now = datetime.now(timezone.utc)

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

        # This job would normally be picked first (earlier queued_at), but it is
        # in crawler_status=infra_error and updated recently.
        infra_job = ArchiveJob(
            source_id=source.id,
            name="worker-infra-recent",
            output_dir=str(archive_root / "hc" / "infra"),
            status="retryable",
            retry_count=0,
            queued_at=now - timedelta(hours=1),
            crawler_status="infra_error",
            updated_at=now,
            created_at=now - timedelta(hours=2),
        )
        session.add(infra_job)
        session.flush()
        infra_id = infra_job.id

        ok_job = ArchiveJob(
            source_id=source.id,
            name="worker-ok",
            output_dir=str(archive_root / "hc" / "ok"),
            status="queued",
            queued_at=now - timedelta(minutes=1),
        )
        session.add(ok_job)
        session.flush()
        ok_id = ok_job.id

    def fake_run_persistent_job(jid: int) -> int:
        with get_session() as session:
            j = session.get(ArchiveJob, jid)
            assert j is not None
            j.status = "completed"
            j.crawler_exit_code = 0
            j.crawler_status = None
        return 0

    def fake_index_job(jid: int) -> int:
        with get_session() as session:
            j = session.get(ArchiveJob, jid)
            assert j is not None
            j.status = "indexed"
        return 0

    monkeypatch.setattr("ha_backend.worker.main.run_persistent_job", fake_run_persistent_job)
    monkeypatch.setattr("ha_backend.worker.main.index_job", fake_index_job)

    run_worker_loop(poll_interval=1, run_once=True)

    with get_session() as session:
        loaded_ok = session.get(ArchiveJob, ok_id)
        assert loaded_ok is not None
        assert loaded_ok.status == "indexed"

        loaded_infra = session.get(ArchiveJob, infra_id)
        assert loaded_infra is not None
        assert loaded_infra.status == "retryable"
        assert loaded_infra.crawler_status == "infra_error"
