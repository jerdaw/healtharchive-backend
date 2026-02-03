from __future__ import annotations

import fcntl
import importlib.util
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ha_backend import db as db_module
from ha_backend.db import Base, get_engine, get_session
from ha_backend.models import ArchiveJob, Source


def _load_script_module() -> Any:
    repo_root = Path(__file__).resolve().parents[1]
    script_path = repo_root / "scripts" / "vps-crawl-auto-recover.py"
    spec = importlib.util.spec_from_file_location("vps_crawl_auto_recover", script_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _init_test_db(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "crawl_auto_recover_drills.db"
    monkeypatch.setenv("HEALTHARCHIVE_DATABASE_URL", f"sqlite:///{db_path}")
    db_module._engine = None
    db_module._SessionLocal = None
    engine = get_engine()
    Base.metadata.create_all(engine)


def _create_source(*, code: str, name: str) -> int:
    with get_session() as session:
        src = Source(code=code, name=name)
        session.add(src)
        session.flush()
        source_id = int(src.id)
        session.commit()
        return source_id


def _create_running_job(
    *,
    source_id: int,
    name: str,
    output_dir: Path,
    combined_log_path: Path | None,
) -> int:
    output_dir.mkdir(parents=True, exist_ok=True)
    with get_session() as session:
        job = ArchiveJob(
            source_id=source_id,
            name=name,
            output_dir=str(output_dir),
            status="running",
            started_at=datetime.now(timezone.utc),
            combined_log_path=str(combined_log_path) if combined_log_path else None,
        )
        session.add(job)
        session.flush()
        job_id = int(job.id)
        session.commit()
        return job_id


def _write_recent_crawlstatus(log_path: Path) -> None:
    ts = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    payload = {
        "timestamp": ts,
        "logLevel": "info",
        "context": "crawlStatus",
        "message": "Crawl statistics",
        "details": {"crawled": 1, "total": 2, "pending": 1, "failed": 0},
    }
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(json.dumps(payload) + "\n", encoding="utf-8")


def test_simulate_stalled_job_requires_dry_run() -> None:
    module = _load_script_module()

    rc = module.main(["--simulate-stalled-job-id", "1", "--apply"])
    assert rc == 2


def test_simulate_stalled_job_requires_non_production_paths() -> None:
    module = _load_script_module()

    # Drill mode must not be allowed to write production watchdog state/metrics by default.
    rc = module.main(["--simulate-stalled-job-id", "1"])
    assert rc == 2


def test_simulate_stalled_job_runner_requires_simulate_stalled_job_id() -> None:
    module = _load_script_module()

    rc = module.main(["--simulate-stalled-job-runner", "worker"])
    assert rc == 2


def test_simulate_stalled_job_runner_requires_single_job_id() -> None:
    module = _load_script_module()

    rc = module.main(
        [
            "--simulate-stalled-job-id",
            "1",
            "--simulate-stalled-job-id",
            "2",
            "--simulate-stalled-job-runner",
            "worker",
        ]
    )
    assert rc == 2


def test_drill_soft_recovery_plan_when_runner_none(tmp_path, monkeypatch, capsys) -> None:
    _init_test_db(tmp_path, monkeypatch)
    module = _load_script_module()

    hc_source_id = _create_source(code="hc", name="Health Canada")
    phac_source_id = _create_source(code="phac", name="PHAC")

    healthy_log = tmp_path / "healthy.combined.log"
    _write_recent_crawlstatus(healthy_log)
    _create_running_job(
        source_id=hc_source_id,
        name="hc-test",
        output_dir=tmp_path / "jobs" / "hc",
        combined_log_path=healthy_log,
    )

    stalled_job_id = _create_running_job(
        source_id=phac_source_id,
        name="phac-test",
        output_dir=tmp_path / "jobs" / "phac",
        combined_log_path=None,
    )

    rc = module.main(
        [
            "--simulate-stalled-job-id",
            str(stalled_job_id),
            "--simulate-stalled-job-runner",
            "none",
            "--state-file",
            str(tmp_path / "state.json"),
            "--lock-file",
            str(tmp_path / "lock"),
            "--textfile-out-dir",
            str(tmp_path),
            "--textfile-out-file",
            "drill.prom",
        ]
    )
    assert rc == 0
    out = capsys.readouterr().out
    assert "soft-recover stalled job_id=" in out
    assert "do not restart the worker" in out
    assert "systemctl stop" not in out


def test_guard_window_does_not_soft_recover_when_runner_worker(
    tmp_path, monkeypatch, capsys
) -> None:
    _init_test_db(tmp_path, monkeypatch)
    module = _load_script_module()

    hc_source_id = _create_source(code="hc", name="Health Canada")
    phac_source_id = _create_source(code="phac", name="PHAC")

    healthy_log = tmp_path / "healthy.combined.log"
    _write_recent_crawlstatus(healthy_log)
    _create_running_job(
        source_id=hc_source_id,
        name="hc-test",
        output_dir=tmp_path / "jobs" / "hc",
        combined_log_path=healthy_log,
    )

    stalled_job_id = _create_running_job(
        source_id=phac_source_id,
        name="phac-test",
        output_dir=tmp_path / "jobs" / "phac",
        combined_log_path=None,
    )

    rc = module.main(
        [
            "--simulate-stalled-job-id",
            str(stalled_job_id),
            "--simulate-stalled-job-runner",
            "worker",
            "--state-file",
            str(tmp_path / "state.json"),
            "--lock-file",
            str(tmp_path / "lock"),
            "--textfile-out-dir",
            str(tmp_path),
            "--textfile-out-file",
            "drill.prom",
        ]
    )
    assert rc == 0
    out = capsys.readouterr().out
    assert "guard window is active" in out
    assert "systemctl stop healtharchive-worker.service" in out
    assert "soft-recover stalled job_id=" not in out


def test_drill_full_recovery_plan_for_systemd_unit_runner(tmp_path, monkeypatch, capsys) -> None:
    _init_test_db(tmp_path, monkeypatch)
    module = _load_script_module()

    phac_source_id = _create_source(code="phac", name="PHAC")
    stalled_job_id = _create_running_job(
        source_id=phac_source_id,
        name="phac-test",
        output_dir=tmp_path / "jobs" / "phac",
        combined_log_path=None,
    )

    unit_name = "healtharchive-job7-phac-3way.service"
    rc = module.main(
        [
            "--skip-if-any-job-progress-within-seconds",
            "0",
            "--simulate-stalled-job-id",
            str(stalled_job_id),
            "--simulate-stalled-job-runner",
            "systemd_unit",
            "--simulate-stalled-job-runner-unit",
            unit_name,
            "--state-file",
            str(tmp_path / "state.json"),
            "--lock-file",
            str(tmp_path / "lock"),
            "--textfile-out-dir",
            str(tmp_path),
            "--textfile-out-file",
            "drill.prom",
        ]
    )
    assert rc == 0
    out = capsys.readouterr().out
    assert f"systemctl stop {unit_name}" in out
    assert f"systemctl start {unit_name}" in out


def test_apply_syncs_db_status_when_job_lock_held(tmp_path, monkeypatch, capsys) -> None:
    _init_test_db(tmp_path, monkeypatch)
    module = _load_script_module()

    lock_dir = tmp_path / "locks"
    monkeypatch.setenv("HEALTHARCHIVE_JOB_LOCK_DIR", str(lock_dir))
    lock_dir.mkdir(parents=True, exist_ok=True)

    hc_source_id = _create_source(code="hc", name="Health Canada")
    output_dir = tmp_path / "jobs" / "hc"
    output_dir.mkdir(parents=True, exist_ok=True)

    with get_session() as session:
        job = ArchiveJob(
            source_id=hc_source_id,
            name="hc-test",
            output_dir=str(output_dir),
            status="retryable",
        )
        session.add(job)
        session.flush()
        job_id = int(job.id)
        session.commit()

    lock_path = lock_dir / f"job-{job_id}.lock"
    fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR, 0o666)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)

        sentinel = tmp_path / "enabled"
        sentinel.write_text("", encoding="utf-8")
        rc = module.main(
            [
                "--apply",
                "--sentinel-file",
                str(sentinel),
                "--deploy-lock-file",
                str(tmp_path / "deploy.lock"),
                "--state-file",
                str(tmp_path / "state.json"),
                "--lock-file",
                str(tmp_path / "watchdog.lock"),
                "--textfile-out-dir",
                str(tmp_path),
                "--textfile-out-file",
                "metrics.prom",
            ]
        )
        assert rc == 0
        out = capsys.readouterr().out
        assert "synced 1 job(s) to status=running" in out
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        except OSError:
            pass
        os.close(fd)

    with get_session() as session:
        stored = session.get(ArchiveJob, job_id)
        assert stored is not None
        assert stored.status == "running"
