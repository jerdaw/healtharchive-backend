import fcntl
import os
from pathlib import Path

import pytest

import ha_backend.cli as cli_module
from ha_backend.db import get_session
from ha_backend.models import ArchiveJob, Source


def _make_source(code: str, session) -> Source:
    source = Source(code=code, name=code.upper(), enabled=True)
    session.add(source)
    session.flush()
    return source


def _make_job(*, session, source: Source, status: str, retry_count: int) -> int:
    job = ArchiveJob(
        source_id=source.id,
        name=f"{source.code}-test",
        output_dir="/tmp/job-out",
        status=status,
        retry_count=retry_count,
    )
    session.add(job)
    session.flush()
    return int(job.id)


def test_reset_retry_count_dry_run_does_not_modify(db_session, capsys) -> None:
    src = _make_source("hc", db_session)
    job_id = _make_job(session=db_session, source=src, status="failed", retry_count=2)
    db_session.commit()

    parser = cli_module.build_parser()
    args = parser.parse_args(["reset-retry-count", "--id", str(job_id)])
    args.func(args)

    captured = capsys.readouterr()
    assert "DRY-RUN" in captured.out
    assert f"job_id={job_id}" in captured.out

    with get_session() as session:
        job = session.get(ArchiveJob, job_id)
        assert job is not None
        assert job.retry_count == 2


def test_reset_retry_count_apply_updates_single_job(db_session, capsys) -> None:
    src = _make_source("hc", db_session)
    job_id = _make_job(session=db_session, source=src, status="retryable", retry_count=1)
    db_session.commit()

    parser = cli_module.build_parser()
    args = parser.parse_args(["reset-retry-count", "--id", str(job_id), "--apply"])
    args.func(args)

    captured = capsys.readouterr()
    assert "APPLY" in captured.out

    with get_session() as session:
        job = session.get(ArchiveJob, job_id)
        assert job is not None
        assert job.retry_count == 0


def test_reset_retry_count_refuses_multi_apply_without_reason(db_session) -> None:
    src = _make_source("hc", db_session)
    job1 = _make_job(session=db_session, source=src, status="failed", retry_count=2)
    job2 = _make_job(session=db_session, source=src, status="retryable", retry_count=1)
    db_session.commit()

    parser = cli_module.build_parser()
    args = parser.parse_args(["reset-retry-count", "--id", str(job1), str(job2), "--apply"])
    with pytest.raises(SystemExit) as exc:
        args.func(args)
    code = exc.value.code
    assert code is not None
    assert int(code) == 2


def test_reset_retry_count_skips_when_job_lock_held(
    db_session, tmp_path: Path, monkeypatch, capsys
) -> None:
    lock_dir = tmp_path / "locks"
    monkeypatch.setenv("HEALTHARCHIVE_JOB_LOCK_DIR", str(lock_dir))

    src = _make_source("hc", db_session)
    job_id = _make_job(session=db_session, source=src, status="retryable", retry_count=2)
    db_session.commit()

    lock_dir.mkdir(parents=True, exist_ok=True)
    lock_path = lock_dir / f"job-{job_id}.lock"
    fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR, 0o666)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)

        parser = cli_module.build_parser()
        args = parser.parse_args(["reset-retry-count", "--id", str(job_id), "--apply"])
        args.func(args)

        captured = capsys.readouterr()
        assert "Skipping jobs" in captured.out
        assert f"job_id={job_id}" in captured.out
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        except OSError:
            pass
        os.close(fd)

    with get_session() as session:
        job = session.get(ArchiveJob, job_id)
        assert job is not None
        assert job.retry_count == 2


def test_reset_retry_count_bulk_requires_limit(db_session) -> None:
    parser = cli_module.build_parser()
    args = parser.parse_args(["reset-retry-count", "--source", "hc", "--status", "failed"])
    with pytest.raises(SystemExit) as exc:
        args.func(args)
    code = exc.value.code
    assert code is not None
    assert int(code) == 2


def test_reset_retry_count_bulk_selects_and_updates(db_session) -> None:
    hc = _make_source("hc", db_session)
    phac = _make_source("phac", db_session)
    hc_failed = _make_job(session=db_session, source=hc, status="failed", retry_count=2)
    hc_retryable = _make_job(session=db_session, source=hc, status="retryable", retry_count=1)
    _make_job(session=db_session, source=hc, status="retryable", retry_count=0)  # ignored by min=1
    _make_job(session=db_session, source=phac, status="failed", retry_count=2)  # filtered by source
    db_session.commit()

    parser = cli_module.build_parser()
    args = parser.parse_args(
        [
            "reset-retry-count",
            "--source",
            "hc",
            "--status",
            "failed",
            "retryable",
            "--limit",
            "10",
            "--apply",
            "--reason",
            "test bulk reset",
        ]
    )
    args.func(args)

    with get_session() as session:
        job1 = session.get(ArchiveJob, hc_failed)
        job2 = session.get(ArchiveJob, hc_retryable)
        assert job1 is not None and job2 is not None
        assert job1.retry_count == 0
        assert job2.retry_count == 0
