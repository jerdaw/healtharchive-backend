from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

import pytest

from ha_backend import db as db_module
from ha_backend.db import Base, get_engine, get_session
from ha_backend.models import ArchiveJob, Source


def _init_test_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db_path = tmp_path / "replay_smoke.db"
    monkeypatch.setenv("HEALTHARCHIVE_DATABASE_URL", f"sqlite:///{db_path}")

    db_module._engine = None
    db_module._SessionLocal = None

    engine = get_engine()
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)


def _load_script_module() -> Any:
    repo_root = Path(__file__).resolve().parents[1]
    script_path = repo_root / "scripts" / "vps-replay-smoke-textfile.py"
    spec = importlib.util.spec_from_file_location("vps_replay_smoke_textfile", script_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_replay_smoke_falls_back_to_registry_seed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _init_test_db(tmp_path, monkeypatch)
    module = _load_script_module()

    with get_session() as session:
        source = Source(code="hc", name="Health Canada", enabled=True)
        session.add(source)
        session.commit()

        # Deliberately omit job.config seeds (simulates legacy/imported jobs).
        job = ArchiveJob(
            source_id=source.id,
            name="legacy-hc-2025-04-21",
            output_dir="/tmp/legacy-hc",
            status="indexed",
            config={},
        )
        session.add(job)
        session.commit()
        job_id = int(job.id)

        targets = module._pick_latest_indexed_jobs(session, ["hc"])

    assert len(targets) == 1
    assert targets[0].source_code == "hc"
    assert targets[0].job_id == job_id
    assert targets[0].seed_url.startswith("https://www.canada.ca/")
