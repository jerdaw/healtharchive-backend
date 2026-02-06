from __future__ import annotations

import sys
from io import StringIO
from pathlib import Path

from ha_backend import cli as cli_module
from ha_backend import db as db_module
from ha_backend.db import Base, get_engine, get_session
from ha_backend.job_registry import SOURCE_JOB_CONFIGS, build_job_config
from ha_backend.models import ArchiveJob, Source
from ha_backend.seeds import seed_sources


def _init_test_db(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "cli_reconcile_annual_tool_options.db"
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


def _create_annual_job(
    session,
    *,
    source_code: str,
    year: int,
    tool_option_overrides: dict[str, object] | None = None,
    include_campaign_meta: bool = True,
) -> int:
    session.flush()
    source = session.query(Source).filter_by(code=source_code).one()
    source_cfg = SOURCE_JOB_CONFIGS[source_code]
    job_cfg = build_job_config(source_cfg)

    tool_opts = dict(job_cfg.get("tool_options") or {})
    if tool_option_overrides:
        tool_opts.update(tool_option_overrides)
    job_cfg["tool_options"] = tool_opts

    if include_campaign_meta:
        job_cfg["campaign_kind"] = "annual"
        job_cfg["campaign_year"] = year
        job_cfg["campaign_date"] = f"{year}-01-01"
        job_cfg["campaign_date_utc"] = f"{year}-01-01T00:00:00Z"

    job_name = f"{source_code}-{year}0101"
    job = ArchiveJob(
        source=source,
        name=job_name,
        output_dir=f"/tmp/{source_code}/{job_name}",
        status="queued",
        config=job_cfg,
    )
    session.add(job)
    session.flush()
    return int(job.id)


def test_reconcile_annual_tool_options_dry_run_leaves_config_unchanged(
    tmp_path, monkeypatch
) -> None:
    _init_test_db(tmp_path, monkeypatch)

    with get_session() as session:
        seed_sources(session)
        job_id = _create_annual_job(
            session,
            source_code="hc",
            year=2026,
            tool_option_overrides={
                "initial_workers": 1,
                "stall_timeout_minutes": 60,
                "error_threshold_timeout": 50,
                "error_threshold_http": 50,
                "backoff_delay_minutes": 2,
                "max_container_restarts": 20,
            },
        )

    out = _run_cli(["reconcile-annual-tool-options", "--year", "2026", "--sources", "hc"])
    assert "Mode:            DRY-RUN" in out
    assert f"hc: WOULD UPDATE job_id={job_id}" in out

    with get_session() as session:
        job = session.get(ArchiveJob, job_id)
        assert job is not None
        tool_opts = dict((job.config or {}).get("tool_options") or {})
        assert tool_opts.get("initial_workers") == 1
        assert tool_opts.get("stall_timeout_minutes") == 60
        assert tool_opts.get("error_threshold_timeout") == 50
        assert tool_opts.get("error_threshold_http") == 50
        assert tool_opts.get("backoff_delay_minutes") == 2
        assert tool_opts.get("max_container_restarts") == 20


def test_reconcile_annual_tool_options_apply_updates_profile_and_sets_campaign_metadata(
    tmp_path, monkeypatch
) -> None:
    _init_test_db(tmp_path, monkeypatch)

    with get_session() as session:
        seed_sources(session)
        job_id = _create_annual_job(
            session,
            source_code="phac",
            year=2026,
            include_campaign_meta=False,
            tool_option_overrides={
                "initial_workers": 1,
                "stall_timeout_minutes": 60,
                "error_threshold_timeout": 50,
                "error_threshold_http": 50,
                "backoff_delay_minutes": 2,
                "max_container_restarts": 20,
                "enable_monitoring": False,
                "enable_adaptive_restart": False,
                "skip_final_build": False,
            },
        )

    out = _run_cli(
        [
            "reconcile-annual-tool-options",
            "--year",
            "2026",
            "--sources",
            "phac",
            "--apply",
        ]
    )
    assert "Mode:            APPLY" in out
    assert f"phac: UPDATED job_id={job_id}" in out
    assert "Applied reconciliation to matching annual jobs." in out

    with get_session() as session:
        job = session.get(ArchiveJob, job_id)
        assert job is not None
        cfg = dict(job.config or {})
        tool_opts = dict(cfg.get("tool_options") or {})

        assert cfg.get("campaign_kind") == "annual"
        assert cfg.get("campaign_year") == 2026
        assert cfg.get("campaign_date") == "2026-01-01"
        assert cfg.get("campaign_date_utc") == "2026-01-01T00:00:00Z"

        assert tool_opts.get("initial_workers") == 2
        assert tool_opts.get("stall_timeout_minutes") == 90
        assert tool_opts.get("error_threshold_timeout") == 65
        assert tool_opts.get("error_threshold_http") == 65
        assert tool_opts.get("backoff_delay_minutes") == 3
        assert tool_opts.get("max_container_restarts") == 30
        assert tool_opts.get("enable_monitoring") is True
        assert tool_opts.get("enable_adaptive_restart") is True
        assert tool_opts.get("skip_final_build") is True
        assert tool_opts.get("docker_shm_size") == "1g"


def test_reconcile_annual_tool_options_preserves_non_baseline_overrides_except_restart_floor(
    tmp_path, monkeypatch
) -> None:
    _init_test_db(tmp_path, monkeypatch)

    with get_session() as session:
        seed_sources(session)
        job_id = _create_annual_job(
            session,
            source_code="cihr",
            year=2026,
            tool_option_overrides={
                "initial_workers": 5,
                "stall_timeout_minutes": 30,
                "error_threshold_timeout": 10,
                "error_threshold_http": 20,
                "backoff_delay_minutes": 4,
                "max_container_restarts": 5,
            },
        )

    out = _run_cli(
        [
            "reconcile-annual-tool-options",
            "--year",
            "2026",
            "--sources",
            "cihr",
            "--apply",
        ]
    )
    assert f"cihr: UPDATED job_id={job_id}" in out

    with get_session() as session:
        job = session.get(ArchiveJob, job_id)
        assert job is not None
        tool_opts = dict((job.config or {}).get("tool_options") or {})
        assert tool_opts.get("initial_workers") == 5
        assert tool_opts.get("stall_timeout_minutes") == 30
        assert tool_opts.get("error_threshold_timeout") == 10
        assert tool_opts.get("error_threshold_http") == 20
        assert tool_opts.get("backoff_delay_minutes") == 4
        assert tool_opts.get("max_container_restarts") == 20
