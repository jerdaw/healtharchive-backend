from __future__ import annotations

import pathlib
from pathlib import Path

from ha_backend import db as db_module
from ha_backend.db import Base, get_engine, get_session
from ha_backend.indexing.pipeline import index_job
from ha_backend.models import ArchiveJob, Source


def _init_test_db(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "indexing_infra.db"
    monkeypatch.setenv("HEALTHARCHIVE_DATABASE_URL", f"sqlite:///{db_path}")

    db_module._engine = None
    db_module._SessionLocal = None

    engine = get_engine()
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)


def test_index_job_marks_index_failed_on_storage_infra_errno_107(tmp_path, monkeypatch) -> None:
    _init_test_db(tmp_path, monkeypatch)

    output_dir = tmp_path / "stale-mount"

    with get_session() as session:
        source = Source(
            code="hc",
            name="Health Canada",
            base_url="https://www.canada.ca/en/health-canada.html",
            description="HC",
            enabled=True,
        )
        session.add(source)
        session.flush()

        job = ArchiveJob(
            source_id=source.id,
            name="indexing-infra",
            output_dir=str(output_dir),
            status="completed",
        )
        session.add(job)
        session.flush()
        job_id = job.id

    orig_stat = pathlib.Path.stat

    def raising_stat(self: pathlib.Path, *args, **kwargs):
        if Path(self) == output_dir:
            raise OSError(107, "Transport endpoint is not connected", str(self))
        return orig_stat(self, *args, **kwargs)

    monkeypatch.setattr(pathlib.Path, "stat", raising_stat)

    rc = index_job(job_id)
    assert rc != 0

    with get_session() as session:
        stored = session.get(ArchiveJob, job_id)
        assert stored is not None
        assert stored.status == "index_failed"
