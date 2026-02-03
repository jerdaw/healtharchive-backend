from __future__ import annotations

import errno
import fcntl
import os
from pathlib import Path

import pytest

from ha_backend import db as db_module
from ha_backend.db import Base, get_engine, get_session
from ha_backend.job_registry import SOURCE_JOB_CONFIGS, create_job_for_source
from ha_backend.jobs import JobAlreadyRunningError, run_persistent_job
from ha_backend.models import ArchiveJob
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


def test_run_persistent_job_builds_monitoring_and_vpn_args(tmp_path, monkeypatch):
    """
    Ensure monitoring/adaptive/VPN options are translated into archive_tool args
    in the expected order, and zimit passthrough args are appended.
    """
    _init_test_db(tmp_path, monkeypatch)

    # Use a temp archive root and harmless tool command.
    archive_root = tmp_path / "jobs"
    monkeypatch.setenv("HEALTHARCHIVE_ARCHIVE_ROOT", str(archive_root))
    monkeypatch.setenv("HEALTHARCHIVE_TOOL_CMD", "echo")

    with get_session() as session:
        seed_sources(session)

    # Capture the args that would be passed to archive_tool.
    captured: dict[str, object] = {}

    class DummyRuntime:
        def __init__(self, name, seeds):
            captured["init"] = (name, tuple(seeds))

        def run(
            self,
            *,
            initial_workers,
            cleanup,
            overwrite,
            log_level,
            extra_args,
            stream_output,
            output_dir_override,
        ):
            captured["run_kwargs"] = dict(
                initial_workers=initial_workers,
                cleanup=cleanup,
                overwrite=overwrite,
                log_level=log_level,
                extra_args=tuple(extra_args),
                stream_output=stream_output,
                output_dir_override=str(output_dir_override),
            )
            return 0

    monkeypatch.setattr("ha_backend.jobs.RuntimeArchiveJob", DummyRuntime)

    # Create a job with monitoring/adaptive/VPN flags and a zimit passthrough arg.
    tool_overrides = {
        "enable_monitoring": True,
        "monitor_interval_seconds": 10,
        "stall_timeout_minutes": 5,
        "error_threshold_timeout": 3,
        "error_threshold_http": 2,
        "enable_adaptive_workers": True,
        "min_workers": 1,
        "max_worker_reductions": 2,
        "enable_vpn_rotation": True,
        "vpn_connect_command": "vpn up",
        "max_vpn_rotations": 4,
        "vpn_rotation_frequency_minutes": 60,
        "backoff_delay_minutes": 15,
    }

    with get_session() as session:
        job_row = create_job_for_source(
            "hc",
            session=session,
            overrides=tool_overrides,
            extra_zimit_args=["--pageLimit", "5"],
        )
        job_id = job_row.id

    rc = run_persistent_job(job_id)
    assert rc == 0

    expected_extra_args = (
        "--docker-shm-size",
        "1g",
        "--enable-monitoring",
        "--monitor-interval-seconds",
        "10",
        "--stall-timeout-minutes",
        "5",
        "--error-threshold-timeout",
        "3",
        "--error-threshold-http",
        "2",
        "--enable-adaptive-workers",
        "--min-workers",
        "1",
        "--max-worker-reductions",
        "2",
        "--enable-vpn-rotation",
        "--vpn-connect-command",
        "vpn up",
        "--max-vpn-rotations",
        "4",
        "--vpn-rotation-frequency-minutes",
        "60",
        "--enable-adaptive-restart",
        "--max-container-restarts",
        "20",
        "--backoff-delay-minutes",
        "15",
        "--relax-perms",
        "--skip-final-build",
        *tuple(SOURCE_JOB_CONFIGS["hc"].default_zimit_passthrough_args),
        "--pageLimit",
        "5",
    )

    assert captured["run_kwargs"]["extra_args"] == expected_extra_args
    # Basic sanity on other args
    assert captured["run_kwargs"]["initial_workers"] == 2
    assert captured["run_kwargs"]["cleanup"] is False
    assert captured["run_kwargs"]["overwrite"] is False
    assert captured["run_kwargs"]["log_level"] == "INFO"


def test_run_persistent_job_marks_storage_infra_errors_retryable(tmp_path, monkeypatch) -> None:
    """
    A storage/mount failure (e.g. Errno 107 from sshfs) should not leave jobs stuck
    as running, and should be classified as infra_error + retryable.
    """
    _init_test_db(tmp_path, monkeypatch)

    with get_session() as session:
        seed_sources(session)

    with get_session() as session:
        job_row = create_job_for_source("hc", session=session)
        job_id = job_row.id

    class FailingRuntime:
        def __init__(self, name, seeds):
            self.name = name
            self.seeds = list(seeds)

        def run(self, **_kwargs):
            raise OSError(107, "Transport endpoint is not connected")

    monkeypatch.setattr("ha_backend.jobs.RuntimeArchiveJob", FailingRuntime)

    rc = run_persistent_job(job_id)
    assert rc != 0

    with get_session() as session:
        stored = session.get(ArchiveJob, job_id)
        assert stored is not None
        assert stored.status == "retryable"
        assert stored.crawler_status == "infra_error"
        assert stored.crawler_exit_code is None
        assert stored.started_at is not None
        assert stored.finished_at is not None


def test_run_persistent_job_marks_output_dir_permission_errors_retryable(
    tmp_path, monkeypatch
) -> None:
    """
    If archive_tool fails because the job output directory isn't writable (e.g. a
    tiered SSHFS mount owned by root), treat it as infra_error so we don't burn
    crawl retry budget.
    """
    _init_test_db(tmp_path, monkeypatch)

    with get_session() as session:
        seed_sources(session)

    with get_session() as session:
        job_row = create_job_for_source("hc", session=session)
        job_id = job_row.id

    class PermissionDeniedRuntime:
        def __init__(self, name, seeds):
            self.name = name
            self.seeds = list(seeds)

        def run(self, **kwargs):
            output_dir = Path(str(kwargs["output_dir_override"]))
            raise PermissionError(
                errno.EACCES,
                "Permission denied",
                str(output_dir / ".writable_test_123"),
            )

    monkeypatch.setattr("ha_backend.jobs.RuntimeArchiveJob", PermissionDeniedRuntime)

    rc = run_persistent_job(job_id)
    assert rc != 0

    with get_session() as session:
        stored = session.get(ArchiveJob, job_id)
        assert stored is not None
        assert stored.status == "retryable"
        assert stored.crawler_status == "infra_error"
        assert stored.crawler_exit_code is None


def test_run_persistent_job_marks_cli_usage_errors_as_infra_error_config(
    tmp_path, monkeypatch
) -> None:
    """
    If the underlying crawler exits quickly with a CLI usage/config error, the
    job should be marked as infra_error_config so the worker doesn't churn the
    retry budget.
    """
    _init_test_db(tmp_path, monkeypatch)
    monkeypatch.setenv("HEALTHARCHIVE_ARCHIVE_ROOT", str(tmp_path / "jobs"))

    with get_session() as session:
        seed_sources(session)

    with get_session() as session:
        job_row = create_job_for_source("cihr", session=session)
        job_id = job_row.id

    class UsageErrorRuntime:
        def __init__(self, name, seeds):
            self.name = name
            self.seeds = list(seeds)

        def run(self, **kwargs):
            output_dir = Path(str(kwargs["output_dir_override"]))
            output_dir.mkdir(parents=True, exist_ok=True)
            log_path = output_dir / "archive_new_crawl_phase_-_attempt_1_test.combined.log"
            log_path.write_text(
                "\n".join(
                    [
                        "[zimit::2026-01-19 12:29:14,718] INFO:Running: warc2zim --name cihr --max-pages 20000",
                        "zimit: error: unrecognized arguments: --max-pages",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            return 1

    monkeypatch.setattr("ha_backend.jobs.RuntimeArchiveJob", UsageErrorRuntime)

    rc = run_persistent_job(job_id)
    assert rc == 1

    with get_session() as session:
        stored = session.get(ArchiveJob, job_id)
        assert stored is not None
        assert stored.status == "failed"
        assert stored.crawler_status == "infra_error_config"
        assert stored.crawler_exit_code == 1
        assert stored.combined_log_path is not None


def test_run_persistent_job_refuses_to_run_when_lock_held(tmp_path, monkeypatch) -> None:
    _init_test_db(tmp_path, monkeypatch)

    archive_root = tmp_path / "jobs"
    monkeypatch.setenv("HEALTHARCHIVE_ARCHIVE_ROOT", str(archive_root))
    monkeypatch.setenv("HEALTHARCHIVE_TOOL_CMD", "echo")
    lock_dir = tmp_path / "locks"
    monkeypatch.setenv("HEALTHARCHIVE_JOB_LOCK_DIR", str(lock_dir))

    with get_session() as session:
        seed_sources(session)

    with get_session() as session:
        job_row = create_job_for_source("hc", session=session)
        job_id = job_row.id

    lock_dir.mkdir(parents=True, exist_ok=True)
    lock_path = lock_dir / f"job-{job_id}.lock"
    fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR, 0o666)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(JobAlreadyRunningError):
            run_persistent_job(job_id)
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        except OSError:
            pass
        os.close(fd)

    with get_session() as session:
        stored = session.get(ArchiveJob, job_id)
        assert stored is not None
        assert stored.status in {"queued", "retryable"}
        assert stored.started_at is None
        assert stored.finished_at is None
