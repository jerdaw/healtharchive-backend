from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from io import StringIO
from pathlib import Path

from ha_backend import cli as cli_module
from ha_backend import db as db_module
from ha_backend.db import Base, get_engine, get_session
from ha_backend.job_registry import create_job_for_source
from ha_backend.models import ArchiveJob
from ha_backend.seeds import seed_sources


def _init_test_db(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "cli_recover_stale_jobs.db"
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


def test_recover_stale_jobs_dry_run_and_apply(tmp_path, monkeypatch) -> None:
    _init_test_db(tmp_path, monkeypatch)
    monkeypatch.setenv("HEALTHARCHIVE_ARCHIVE_ROOT", str(tmp_path / "jobs"))

    with get_session() as session:
        seed_sources(session)

    with get_session() as session:
        job = create_job_for_source("hc", session=session)
        job.status = "running"
        job.started_at = datetime.now(timezone.utc) - timedelta(hours=10)
        job_id = int(job.id)

    out = _run_cli(["recover-stale-jobs", "--older-than-minutes", "60"])
    assert "DRY-RUN" in out
    assert f"job_id={job_id}" in out

    with get_session() as session:
        reloaded = session.get(ArchiveJob, job_id)
        assert reloaded is not None
        assert reloaded.status == "running"

    out = _run_cli(["recover-stale-jobs", "--older-than-minutes", "60", "--apply"])
    assert "Recovered 1 job(s)" in out

    with get_session() as session:
        reloaded = session.get(ArchiveJob, job_id)
        assert reloaded is not None
        assert reloaded.status == "retryable"
        assert reloaded.crawler_stage == "recovered_stale_running"
