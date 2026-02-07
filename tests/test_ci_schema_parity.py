from __future__ import annotations

import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from fastapi.testclient import TestClient

from ha_backend import db as db_module
from ha_backend.db import get_session
from ha_backend.models import ArchiveJob, Snapshot, Source


def _reset_db_handles() -> None:
    db_module._engine = None
    db_module._SessionLocal = None


def _apply_alembic_head(*, repo_root: Path) -> None:
    subprocess.run(  # nosec: B603 - trusted local test command
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=str(repo_root),
        env=os.environ.copy(),
        check=True,
        capture_output=True,
        text=True,
    )


def _init_test_app() -> TestClient:
    from ha_backend.api import app

    try:
        import uvloop  # noqa: F401
    except Exception:
        return TestClient(app)
    return TestClient(app, backend_options={"use_uvloop": True})


def _seed_minimal_public_data() -> None:
    with get_session() as session:
        source = Source(
            code="hc",
            name="Health Canada",
            base_url="https://www.canada.ca/en/health-canada.html",
            description="Health Canada",
            enabled=True,
        )
        session.add(source)
        session.flush()

        job = ArchiveJob(
            source_id=source.id,
            name="hc-schema-parity",
            output_dir="/tmp/hc-schema-parity",
            status="indexed",
            queued_at=datetime.now(timezone.utc),
            started_at=datetime.now(timezone.utc),
            finished_at=datetime.now(timezone.utc),
        )
        session.add(job)
        session.flush()

        session.add(
            Snapshot(
                job_id=job.id,
                source_id=source.id,
                url="https://www.canada.ca/en/health-canada/covid19.html",
                normalized_url_group="https://www.canada.ca/en/health-canada/covid19.html",
                capture_timestamp=datetime.now(timezone.utc),
                mime_type="text/html",
                status_code=200,
                title="COVID-19 guidance",
                snippet="Latest COVID-19 guidance from Health Canada.",
                language="en",
                warc_path="/warcs/hc-covid.warc.gz",
                warc_record_id="hc-covid",
            )
        )


def test_public_api_queries_work_against_alembic_head_schema(tmp_path, monkeypatch) -> None:
    """
    Guardrail against schema drift:
    - Build schema using Alembic migrations only.
    - Exercise high-risk public query paths that previously regressed when a
      model/query referenced a column missing from migrations.
    """
    db_path = tmp_path / "schema_parity.db"
    monkeypatch.setenv("HEALTHARCHIVE_DATABASE_URL", f"sqlite:///{db_path}")
    monkeypatch.setenv("HEALTHARCHIVE_CHANGE_TRACKING_ENABLED", "1")

    repo_root = Path(__file__).resolve().parents[1]
    _reset_db_handles()
    _apply_alembic_head(repo_root=repo_root)
    _reset_db_handles()

    _seed_minimal_public_data()
    client = _init_test_app()

    search_resp = client.get("/api/search", params={"q": "covid", "pageSize": 1})
    assert search_resp.status_code == 200
    search_body = search_resp.json()
    assert isinstance(search_body.get("results"), list)
    assert isinstance(search_body.get("total"), int)

    changes_resp = client.get("/api/changes", params={"source": "hc", "pageSize": 1})
    assert changes_resp.status_code == 200
    changes_body = changes_resp.json()
    assert changes_body.get("enabled") is True
    assert isinstance(changes_body.get("results"), list)
