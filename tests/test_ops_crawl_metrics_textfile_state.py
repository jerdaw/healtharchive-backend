from __future__ import annotations

import importlib.util
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ha_backend import db as db_module
from ha_backend.db import Base, get_engine, get_session
from ha_backend.models import ArchiveJob, Source
from ha_backend.seeds import seed_sources


def _load_script_module() -> Any:
    repo_root = Path(__file__).resolve().parents[1]
    script_path = repo_root / "scripts" / "vps-crawl-metrics-textfile.py"
    spec = importlib.util.spec_from_file_location("vps_crawl_metrics_textfile", script_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _init_test_db(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "crawl_metrics.db"
    monkeypatch.setenv("HEALTHARCHIVE_DATABASE_URL", f"sqlite:///{db_path}")

    db_module._engine = None
    db_module._SessionLocal = None

    engine = get_engine()
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)


def test_metrics_emits_archive_state_counters(tmp_path, monkeypatch) -> None:
    _init_test_db(tmp_path, monkeypatch)

    out_dir = tmp_path / "textfile"
    out_file = "healtharchive_crawl.prom"

    job_dir = tmp_path / "jobdir"
    job_dir.mkdir(parents=True, exist_ok=True)
    state_path = job_dir / ".archive_state.json"
    state_path.write_text(
        json.dumps(
            {
                "current_workers": 2,
                "worker_reductions_done": 1,
                "container_restarts_done": 3,
                "vpn_rotations_done": 0,
                "temp_dirs_host_paths": ["/tmp/a", "/tmp/b"],
            }
        )
        + "\n",
        encoding="utf-8",
    )
    (job_dir / "archive_new_crawl_phase_-_attempt_1_20260206_000001.combined.log").write_text(
        "\n".join(
            [
                "2026-02-06 00:00:01 [INFO] --- Starting Loop Iteration: Stage 'Initial Crawl - Attempt 1' ---",
                "2026-02-06 00:10:01 [INFO] --- Starting Loop Iteration: Stage 'New Crawl Phase - Attempt 2' ---",
                "2026-02-06 00:20:01 [INFO] --- Starting Loop Iteration: Stage 'Resume Crawl - Attempt 3' ---",
                "2026-02-06 00:30:01 [INFO] --- Starting Loop Iteration: Stage 'New Crawl Phase - Attempt 4' ---",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    with get_session() as session:
        seed_sources(session)
        session.flush()
        source = session.query(Source).filter_by(code="hc").one()
        job = ArchiveJob(
            source=source,
            name="hc-test",
            output_dir=str(job_dir),
            status="running",
            started_at=datetime.now(timezone.utc),
        )
        session.add(job)
        session.commit()
        job_id = int(job.id)

    module = _load_script_module()
    rc = int(module.main(["--out-dir", str(out_dir), "--out-file", out_file]))
    assert rc == 0

    content = (out_dir / out_file).read_text(encoding="utf-8")
    labels = f'job_id="{job_id}",source="hc"'

    assert f"healtharchive_crawl_running_job_state_file_ok{{{labels}}} 1" in content
    assert f"healtharchive_crawl_running_job_state_parse_ok{{{labels}}} 1" in content
    assert f"healtharchive_crawl_running_job_current_workers{{{labels}}} 2" in content
    assert f"healtharchive_crawl_running_job_worker_reductions_done{{{labels}}} 1" in content
    assert f"healtharchive_crawl_running_job_container_restarts_done{{{labels}}} 3" in content
    assert f"healtharchive_crawl_running_job_vpn_rotations_done{{{labels}}} 0" in content
    assert f"healtharchive_crawl_running_job_temp_dirs_count{{{labels}}} 2" in content
    assert f"healtharchive_crawl_running_job_new_crawl_phase_count{{{labels}}} 2" in content


def test_metrics_emits_indexing_pending_job_age(tmp_path, monkeypatch) -> None:
    _init_test_db(tmp_path, monkeypatch)

    out_dir = tmp_path / "textfile"
    out_file = "healtharchive_crawl.prom"

    with get_session() as session:
        seed_sources(session)
        session.flush()
        source = session.query(Source).filter_by(code="hc").one()
        job = ArchiveJob(
            source=source,
            name="hc-completed",
            output_dir=str(tmp_path / "jobdir"),
            status="completed",
            finished_at=datetime.now(timezone.utc),
        )
        session.add(job)
        session.commit()

    module = _load_script_module()
    rc = int(module.main(["--out-dir", str(out_dir), "--out-file", out_file]))
    assert rc == 0

    content = (out_dir / out_file).read_text(encoding="utf-8")
    assert "healtharchive_indexing_pending_jobs 1" in content
    assert 'healtharchive_indexing_pending_jobs_by_source{source="hc"} 1' in content
