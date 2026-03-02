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
    db_path = tmp_path / "crawl_auto_recover_degraded.db"
    monkeypatch.setenv("HEALTHARCHIVE_DATABASE_URL", f"sqlite:///{db_path}")
    db_module._engine = None
    db_module._SessionLocal = None
    engine = get_engine()
    Base.metadata.create_all(engine)


def _write_crawl_status_log(log_path: Path, *, now: datetime) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    t0 = now.replace(second=max(0, now.second - 70)).isoformat().replace("+00:00", "Z")
    t1 = now.replace(second=max(0, now.second - 10)).isoformat().replace("+00:00", "Z")
    payload0 = {
        "timestamp": t0,
        "logLevel": "info",
        "context": "crawlStatus",
        "message": "Crawl statistics",
        "details": {"crawled": 100, "total": 1000, "pending": 1, "failed": 0},
    }
    payload1 = {
        "timestamp": t1,
        "logLevel": "info",
        "context": "crawlStatus",
        "message": "Crawl statistics",
        "details": {"crawled": 101, "total": 1000, "pending": 1, "failed": 0},
    }
    log_path.write_text(
        json.dumps(payload0) + "\n" + json.dumps(payload1) + "\n",
        encoding="utf-8",
    )


def test_degraded_rate_streak_metrics_and_reason(tmp_path, monkeypatch) -> None:
    _init_test_db(tmp_path, monkeypatch)
    module = _load_script_module()
    fixed_now = datetime(2026, 3, 1, 0, 0, 50, tzinfo=timezone.utc)
    monkeypatch.setattr(module, "_utc_now", lambda: fixed_now)
    monkeypatch.setattr(module, "_ps_snapshot", lambda: [])

    log_path = tmp_path / "jobs" / "hc" / "archive_test.combined.log"
    _write_crawl_status_log(log_path, now=fixed_now)

    with get_session() as session:
        source = Source(code="hc", name="Health Canada")
        session.add(source)
        session.flush()
        job = ArchiveJob(
            source_id=int(source.id),
            name="hc-20260101",
            output_dir=str(log_path.parent),
            status="running",
            combined_log_path=str(log_path),
            started_at=fixed_now,
            config={},
        )
        session.add(job)
        session.commit()

    sentinel = tmp_path / "enabled"
    sentinel.write_text("", encoding="utf-8")
    state_path = tmp_path / "state.json"
    metrics_path = tmp_path / "metrics.prom"

    common_args = [
        "--sentinel-file",
        str(sentinel),
        "--deploy-lock-file",
        str(tmp_path / "deploy.lock"),
        "--state-file",
        str(state_path),
        "--lock-file",
        str(tmp_path / "watchdog.lock"),
        "--textfile-out-dir",
        str(tmp_path),
        "--textfile-out-file",
        metrics_path.name,
        "--degraded-rate-threshold-ppm",
        "2.0",
        "--degraded-min-consecutive-runs",
        "2",
        "--degraded-max-progress-age-seconds",
        "300",
        "--degraded-sources",
        "hc,phac",
    ]

    rc1 = module.main(common_args)
    assert rc1 == 0
    metrics1 = metrics_path.read_text(encoding="utf-8")
    assert "healtharchive_crawl_auto_recover_degraded_jobs 1" in metrics1
    assert 'healtharchive_crawl_auto_recover_degraded_streak{job_id="1",source="hc"} 1' in metrics1
    assert (
        'healtharchive_crawl_auto_recover_last_result{result="skip",reason="no_stalled_jobs"} 1'
        in metrics1
    )

    rc2 = module.main(common_args)
    assert rc2 == 0
    metrics2 = metrics_path.read_text(encoding="utf-8")
    assert 'healtharchive_crawl_auto_recover_degraded_streak{job_id="1",source="hc"} 2' in metrics2
    assert (
        'healtharchive_crawl_auto_recover_last_result{result="skip",reason="degraded_observe"} 1'
        in metrics2
    )
