from __future__ import annotations

from io import StringIO
from pathlib import Path

import sys

from ha_backend import db as db_module
from ha_backend.db import Base, get_engine, get_session
from ha_backend.models import ArchiveJob, Source
from ha_backend import cli as cli_module


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

