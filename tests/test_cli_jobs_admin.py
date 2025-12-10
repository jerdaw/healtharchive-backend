from __future__ import annotations

from io import StringIO
from pathlib import Path

import sys

from ha_backend import db as db_module
from ha_backend.db import Base, get_engine, get_session
from ha_backend.models import ArchiveJob, Source
from ha_backend import cli as cli_module
from ha_backend.seeds import seed_sources


def _init_test_db(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "cli_jobs.db"
    monkeypatch.setenv("HEALTHARCHIVE_DATABASE_URL", f"sqlite:///{db_path}")

    db_module._engine = None
    db_module._SessionLocal = None

    engine = get_engine()
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)


def _seed_jobs() -> None:
    with get_session() as session:
        src = Source(
            code="hc",
            name="Health Canada",
            base_url="https://www.canada.ca/en/health-canada.html",
            description="HC",
            enabled=True,
        )
        session.add(src)
        session.flush()

        job1 = ArchiveJob(
            source_id=src.id,
            name="job1",
            output_dir="/tmp/job1",
            status="queued",
        )
        job2 = ArchiveJob(
            source_id=src.id,
            name="job2",
            output_dir="/tmp/job2",
            status="failed",
            retry_count=1,
        )
        session.add_all([job1, job2])


def test_create_job_injects_zimit_passthrough_args(tmp_path, monkeypatch) -> None:
    """
    create-job should accept dev-only page/depth flags and persist them as
    zimit_passthrough_args in the job config.
    """
    _init_test_db(tmp_path, monkeypatch)

    archive_root = tmp_path / "jobs"
    monkeypatch.setenv("HEALTHARCHIVE_ARCHIVE_ROOT", str(archive_root))

    # Seed baseline sources (hc, phac).
    with get_session() as session:
        seed_sources(session)

    parser = cli_module.build_parser()
    args = parser.parse_args(
        ["create-job", "--source", "hc", "--page-limit", "5", "--depth", "1"]
    )

    # Run the CLI handler; we do not care about stdout here.
    args.func(args)

    # Verify that a job was created with the expected passthrough args.
    with get_session() as session:
        job = session.query(ArchiveJob).one()
        cfg = job.config or {}
        z_args = cfg.get("zimit_passthrough_args") or []

    assert z_args == ["--pageLimit", "5", "--depth", "1"]


def test_register_job_dir_creates_completed_job(tmp_path, monkeypatch) -> None:
    """
    register-job-dir should create a 'completed' ArchiveJob pointed at an
    existing output directory.
    """
    _init_test_db(tmp_path, monkeypatch)

    output_dir = tmp_path / "jobs" / "hc" / "existing"
    output_dir.mkdir(parents=True, exist_ok=True)

    with get_session() as session:
        seed_sources(session)

    parser = cli_module.build_parser()
    args = parser.parse_args(
        [
            "register-job-dir",
            "--source",
            "hc",
            "--output-dir",
            str(output_dir),
            "--name",
            "hc-dev-warcs",
        ]
    )

    # Run the CLI handler.
    stdout = StringIO()
    old_stdout = sys.stdout
    try:
        sys.stdout = stdout
        args.func(args)
    finally:
        sys.stdout = old_stdout

    with get_session() as session:
        jobs = session.query(ArchiveJob).all()
        assert len(jobs) == 1
        job = jobs[0]
        assert job.name == "hc-dev-warcs"
        assert job.status == "completed"
        assert job.output_dir == str(output_dir)


def test_list_jobs_outputs_rows(tmp_path, monkeypatch) -> None:
    _init_test_db(tmp_path, monkeypatch)
    _seed_jobs()

    parser = cli_module.build_parser()
    args = parser.parse_args(["list-jobs"])

    stdout = StringIO()
    old_stdout = sys.stdout
    try:
        sys.stdout = stdout
        args.func(args)
    finally:
        sys.stdout = old_stdout

    out = stdout.getvalue()
    assert "job1" in out
    assert "job2" in out


def test_show_job_displays_details(tmp_path, monkeypatch) -> None:
    _init_test_db(tmp_path, monkeypatch)
    _seed_jobs()

    with get_session() as session:
        job = session.query(ArchiveJob).filter_by(name="job1").one()
        job_id = job.id

    parser = cli_module.build_parser()
    args = parser.parse_args(["show-job", "--id", str(job_id)])

    stdout = StringIO()
    old_stdout = sys.stdout
    try:
        sys.stdout = stdout
        args.func(args)
    finally:
        sys.stdout = old_stdout

    out = stdout.getvalue()
    assert f"ID:              {job_id}" in out
    assert "job1" in out


def test_retry_job_marks_failed_as_retryable(tmp_path, monkeypatch) -> None:
    _init_test_db(tmp_path, monkeypatch)
    _seed_jobs()

    with get_session() as session:
        job = session.query(ArchiveJob).filter_by(name="job2").one()
        job_id = job.id
        assert job.status == "failed"

    parser = cli_module.build_parser()
    args = parser.parse_args(["retry-job", "--id", str(job_id)])

    stdout = StringIO()
    old_stdout = sys.stdout
    try:
        sys.stdout = stdout
        args.func(args)
    finally:
        sys.stdout = old_stdout

    with get_session() as session:
        job = session.get(ArchiveJob, job_id)
        assert job.status == "retryable"
