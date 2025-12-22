from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from fastapi.testclient import TestClient

from ha_backend import db as db_module
from ha_backend.db import Base, get_engine, get_session
from ha_backend.models import IssueReport


def _init_test_app(tmp_path: Path, monkeypatch) -> TestClient:
    """
    Configure a temporary SQLite DB and return a FastAPI TestClient.
    """
    db_path = tmp_path / "api_reports_test.db"
    monkeypatch.setenv("HEALTHARCHIVE_DATABASE_URL", f"sqlite:///{db_path}")

    # Reset cached engine/session so we pick up the new URL.
    db_module._engine = None
    db_module._SessionLocal = None

    engine = get_engine()
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)

    from ha_backend.api import app

    try:
        import uvloop  # noqa: F401
    except Exception:
        return TestClient(app)
    return TestClient(app, backend_options={"use_uvloop": True})


def test_public_report_submission_creates_row(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("HEALTHARCHIVE_ADMIN_TOKEN", raising=False)
    monkeypatch.delenv("HEALTHARCHIVE_ENV", raising=False)
    client = _init_test_app(tmp_path, monkeypatch)

    payload = {
        "category": "broken_snapshot",
        "description": "The snapshot iframe shows an error for ID 12345.",
        "snapshotId": 12345,
        "originalUrl": "https://www.canada.ca/en/example.html",
        "reporterEmail": "reporter@example.com",
        "pageUrl": "https://healtharchive.ca/snapshot/12345",
    }

    resp = client.post("/api/reports", json=payload)
    assert resp.status_code == 201
    body = resp.json()
    assert body["status"] == "new"
    assert body["reportId"] is not None

    with get_session() as session:
        report = session.query(IssueReport).first()
        assert report is not None
        assert report.category == "broken_snapshot"
        assert "snapshot iframe" in report.description


def test_report_honeypot_is_accepted_without_storage(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("HEALTHARCHIVE_ADMIN_TOKEN", raising=False)
    monkeypatch.delenv("HEALTHARCHIVE_ENV", raising=False)
    client = _init_test_app(tmp_path, monkeypatch)

    payload = {
        "category": "general_feedback",
        "description": "This should be ignored because the bot field is set.",
        "website": "https://spam.example.com",
    }

    resp = client.post("/api/reports", json=payload)
    assert resp.status_code == 201
    body = resp.json()
    assert body["reportId"] is None

    with get_session() as session:
        assert session.query(IssueReport).count() == 0


def test_admin_reports_list(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("HEALTHARCHIVE_ADMIN_TOKEN", raising=False)
    monkeypatch.delenv("HEALTHARCHIVE_ENV", raising=False)
    client = _init_test_app(tmp_path, monkeypatch)

    now = datetime(2025, 1, 1, 12, 0, tzinfo=timezone.utc)
    with get_session() as session:
        session.add(
            IssueReport(
                category="incorrect_metadata",
                description="The capture date is wrong.",
                snapshot_id=999,
                original_url="https://www.canada.ca/en/test.html",
                reporter_email=None,
                page_url="https://healtharchive.ca/snapshot/999",
                status="new",
                created_at=now,
                updated_at=now,
            )
        )
        session.commit()

    resp = client.get("/api/admin/reports")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] >= 1
    assert body["items"]
    assert body["items"][0]["category"] == "incorrect_metadata"
