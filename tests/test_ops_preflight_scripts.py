from __future__ import annotations

import importlib.util
import json
import sys
from collections import namedtuple
from datetime import datetime, timezone
from pathlib import Path
from types import ModuleType

from ha_backend import db as db_module
from ha_backend.db import Base, get_engine, get_session
from ha_backend.models import ArchiveJob, Source
from ha_backend.seeds import seed_sources


def _load_script_module(script_filename: str, module_name: str) -> ModuleType:
    repo_root = Path(__file__).resolve().parents[1]
    script_path = repo_root / "scripts" / script_filename
    spec = importlib.util.spec_from_file_location(module_name, script_path)
    assert spec is not None
    assert spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = mod
    spec.loader.exec_module(mod)
    return mod


def _init_test_db(tmp_path: Path, monkeypatch, name: str) -> None:
    db_path = tmp_path / name
    monkeypatch.setenv("HEALTHARCHIVE_DATABASE_URL", f"sqlite:///{db_path}")

    db_module._engine = None
    db_module._SessionLocal = None

    engine = get_engine()
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)


def test_campaign_storage_forecast_uses_proxy_for_phac(tmp_path, monkeypatch, capsys) -> None:
    mod = _load_script_module(
        "campaign_storage_forecast.py",
        module_name="ha_test_campaign_storage_forecast",
    )
    _init_test_db(tmp_path, monkeypatch, "forecast.db")

    with get_session() as session:
        seed_sources(session)
        session.flush()
        hc = session.query(Source).filter_by(code="hc").one()
        cihr = session.query(Source).filter_by(code="cihr").one()
        session.add(
            ArchiveJob(
                source=hc,
                name="hc-20250101",
                output_dir="/tmp/hc-20250101",
                status="indexed",
                output_bytes_total=10 * mod.GiB,
                storage_scanned_at=datetime.now(timezone.utc),
                config={},
            )
        )
        session.add(
            ArchiveJob(
                source=cihr,
                name="cihr-20250101",
                output_dir="/tmp/cihr-20250101",
                status="indexed",
                output_bytes_total=5 * mod.GiB,
                storage_scanned_at=datetime.now(timezone.utc),
                config={},
            )
        )

    DU = namedtuple("DU", ["total", "used", "free"])
    monkeypatch.setattr(
        mod.shutil,
        "disk_usage",
        lambda _p: DU(total=100 * mod.GiB, used=50 * mod.GiB, free=50 * mod.GiB),
    )

    rc = mod.main(
        ["--year", "2026", "--archive-root", str(tmp_path), "--growth-factor", "1.0", "--json"]
    )
    assert rc == 0

    payload = json.loads(capsys.readouterr().out)
    methods = {e["sourceCode"]: e["estimateMethod"] for e in payload["estimates"]}
    assert methods["phac"] == "proxy:hc"


def test_campaign_storage_forecast_json_exit_code_fails_on_overflow(
    tmp_path, monkeypatch, capsys
) -> None:
    mod = _load_script_module(
        "campaign_storage_forecast.py",
        module_name="ha_test_campaign_storage_forecast_fail",
    )
    _init_test_db(tmp_path, monkeypatch, "forecast_fail.db")

    with get_session() as session:
        seed_sources(session)
        session.flush()
        hc = session.query(Source).filter_by(code="hc").one()
        session.add(
            ArchiveJob(
                source=hc,
                name="hc-20250101",
                output_dir="/tmp/hc-20250101",
                status="indexed",
                output_bytes_total=10 * mod.GiB,
                storage_scanned_at=datetime.now(timezone.utc),
                config={},
            )
        )

    DU = namedtuple("DU", ["total", "used", "free"])
    monkeypatch.setattr(
        mod.shutil,
        "disk_usage",
        lambda _p: DU(total=20 * mod.GiB, used=19 * mod.GiB, free=1 * mod.GiB),
    )

    rc = mod.main(
        ["--year", "2026", "--archive-root", str(tmp_path), "--growth-factor", "1.0", "--json"]
    )
    assert rc == 1

    payload = json.loads(capsys.readouterr().out)
    assert payload["summary"]["expectedAdditionalBytes"] > payload["disk"]["freeBytes"]


def test_vps_resource_headroom_passes_with_good_inputs(monkeypatch) -> None:
    mod = _load_script_module(
        "vps_resource_headroom.py",
        module_name="ha_test_vps_resource_headroom",
    )

    monkeypatch.setattr(mod.os, "cpu_count", lambda: 4)
    monkeypatch.setattr(
        mod,
        "_read_kv_kib",
        lambda _p: {
            "MemTotal": int(8 * 1024 * 1024),
            "MemAvailable": int(4 * 1024 * 1024),
            "SwapTotal": 0,
            "SwapFree": 0,
        },
    )
    monkeypatch.setattr(mod, "_read_loadavg", lambda: (0.1, 0.1, 0.2))
    monkeypatch.setattr(
        mod,
        "_read_psi",
        lambda kind: {"some": {"avg10": 0.0}, "full": {"avg10": 0.0}}
        if kind == "memory"
        else {"some": {"avg10": 0.0}},
    )

    assert mod.main([]) == 0


def test_vps_resource_headroom_fails_with_low_mem(monkeypatch) -> None:
    mod = _load_script_module(
        "vps_resource_headroom.py",
        module_name="ha_test_vps_resource_headroom_fail",
    )

    monkeypatch.setattr(mod.os, "cpu_count", lambda: 4)
    monkeypatch.setattr(
        mod,
        "_read_kv_kib",
        lambda _p: {
            "MemTotal": int(8 * 1024 * 1024),
            "MemAvailable": int(1 * 1024 * 1024),
            "SwapTotal": int(2 * 1024 * 1024),
            "SwapFree": int(1 * 1024 * 1024),
        },
    )
    monkeypatch.setattr(mod, "_read_loadavg", lambda: (6.0, 6.0, 6.0))
    monkeypatch.setattr(mod, "_read_psi", lambda _kind: None)

    assert mod.main([]) == 1


def test_vps_job_queue_hygiene_exits_nonzero_when_running_jobs(tmp_path, monkeypatch) -> None:
    mod = _load_script_module(
        "vps_job_queue_hygiene.py",
        module_name="ha_test_vps_job_queue_hygiene",
    )
    _init_test_db(tmp_path, monkeypatch, "queue.db")

    with get_session() as session:
        seed_sources(session)
        session.flush()
        hc = session.query(Source).filter_by(code="hc").one()
        session.add(
            ArchiveJob(
                source=hc,
                name="hc-queued",
                output_dir=str(tmp_path / "hc-queued"),
                status="running",
                retry_count=0,
                config={},
            )
        )

    assert mod.main(["--json"]) == 1


def test_vps_temp_cleanup_candidates_reports_indexed_jobs_with_tmp_dirs(
    tmp_path, monkeypatch, capsys
) -> None:
    mod = _load_script_module(
        "vps_temp_cleanup_candidates.py",
        module_name="ha_test_vps_temp_cleanup_candidates",
    )
    _init_test_db(tmp_path, monkeypatch, "tmp_cleanup.db")

    out_dir = tmp_path / "jobdir"
    tmp_dir = out_dir / ".tmpabc"
    tmp_dir.mkdir(parents=True)
    (tmp_dir / "a.txt").write_text("hello", encoding="utf-8")

    with get_session() as session:
        seed_sources(session)
        session.flush()
        hc = session.query(Source).filter_by(code="hc").one()
        job = ArchiveJob(
            source=hc,
            name="hc-idx",
            output_dir=str(out_dir),
            status="indexed",
            cleanup_status="none",
            config={},
        )
        session.add(job)
        session.flush()
        job_id = int(job.id)

    assert mod.main(["--json", "--limit", "5"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["candidates"]
    assert payload["candidates"][0]["jobId"] == job_id
