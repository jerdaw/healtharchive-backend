from __future__ import annotations

import sys
from io import StringIO
from pathlib import Path

from ha_backend import cli as cli_module
from ha_backend import db as db_module
from ha_backend.db import Base, get_engine, get_session
from ha_backend.job_registry import create_job_for_source
from ha_backend.models import ArchiveJob
from ha_backend.seeds import seed_sources


def _init_test_db(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "cli_schedule_annual.db"
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


def test_schedule_annual_dry_run_does_not_create_jobs(tmp_path, monkeypatch) -> None:
    _init_test_db(tmp_path, monkeypatch)
    monkeypatch.setenv("HEALTHARCHIVE_ARCHIVE_ROOT", str(tmp_path / "jobs"))

    with get_session() as session:
        seed_sources(session)

    out = _run_cli(["schedule-annual", "--year", "2027"])
    assert "DRY-RUN" in out

    with get_session() as session:
        assert session.query(ArchiveJob).count() == 0


def test_schedule_annual_apply_creates_jobs_ordered_and_labeled(tmp_path, monkeypatch) -> None:
    _init_test_db(tmp_path, monkeypatch)
    monkeypatch.setenv("HEALTHARCHIVE_ARCHIVE_ROOT", str(tmp_path / "jobs"))

    with get_session() as session:
        seed_sources(session)

    _run_cli(["schedule-annual", "--year", "2027", "--apply"])

    with get_session() as session:
        jobs = session.query(ArchiveJob).order_by(ArchiveJob.id).all()

        assert len(jobs) == 3
        assert [job.source.code for job in jobs] == ["hc", "phac", "cihr"]
        assert [job.name for job in jobs] == [
            "hc-20270101",
            "phac-20270101",
            "cihr-20270101",
        ]
        assert all(job.status == "queued" for job in jobs)
        assert all(job.queued_at is not None for job in jobs)

        for job in jobs:
            cfg = job.config or {}
            assert cfg.get("campaign_kind") == "annual"
            assert cfg.get("campaign_year") == 2027
            assert cfg.get("campaign_date") == "2027-01-01"
            assert cfg.get("campaign_date_utc") == "2027-01-01T00:00:00Z"
            assert cfg.get("scheduler_version") == "v1"
            assert cfg.get("seeds")
            assert cfg.get("zimit_passthrough_args") is not None
            assert cfg.get("tool_options") is not None


def test_schedule_annual_apply_is_idempotent(tmp_path, monkeypatch) -> None:
    _init_test_db(tmp_path, monkeypatch)
    monkeypatch.setenv("HEALTHARCHIVE_ARCHIVE_ROOT", str(tmp_path / "jobs"))

    with get_session() as session:
        seed_sources(session)

    _run_cli(["schedule-annual", "--year", "2027", "--apply"])
    _run_cli(["schedule-annual", "--year", "2027", "--apply"])

    with get_session() as session:
        assert session.query(ArchiveJob).count() == 3


def test_schedule_annual_skips_source_with_active_job(tmp_path, monkeypatch) -> None:
    _init_test_db(tmp_path, monkeypatch)
    monkeypatch.setenv("HEALTHARCHIVE_ARCHIVE_ROOT", str(tmp_path / "jobs"))

    with get_session() as session:
        seed_sources(session)

    # Create an active (queued) job for hc first; the annual scheduler should
    # refuse to enqueue a second hc job until this one is handled.
    with get_session() as session:
        create_job_for_source("hc", session=session)

    _run_cli(["schedule-annual", "--year", "2027", "--apply"])

    with get_session() as session:
        jobs = session.query(ArchiveJob).order_by(ArchiveJob.id).all()

        assert len(jobs) == 3
        annual_jobs = [
            j for j in jobs if (j.config or {}).get("campaign_kind") == "annual"
        ]
        assert {j.source.code for j in annual_jobs} == {"phac", "cihr"}


def test_schedule_annual_respects_max_create_per_run(tmp_path, monkeypatch) -> None:
    _init_test_db(tmp_path, monkeypatch)
    monkeypatch.setenv("HEALTHARCHIVE_ARCHIVE_ROOT", str(tmp_path / "jobs"))

    with get_session() as session:
        seed_sources(session)

    _run_cli(["schedule-annual", "--year", "2027", "--apply", "--max-create-per-run", "1"])

    with get_session() as session:
        jobs = session.query(ArchiveJob).order_by(ArchiveJob.id).all()

        assert len(jobs) == 1
        assert jobs[0].source.code == "hc"
        assert jobs[0].name == "hc-20270101"
