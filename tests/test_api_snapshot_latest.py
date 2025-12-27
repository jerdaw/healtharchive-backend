from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from fastapi.testclient import TestClient

from ha_backend import db as db_module
from ha_backend.db import Base, get_engine, get_session
from ha_backend.models import Snapshot, Source


def _init_test_app(tmp_path: Path, monkeypatch) -> TestClient:
    db_path = tmp_path / "snapshot_latest.db"
    monkeypatch.setenv("HEALTHARCHIVE_DATABASE_URL", f"sqlite:///{db_path}")

    db_module._engine = None
    db_module._SessionLocal = None

    engine = get_engine()
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)

    from ha_backend.api import app

    return TestClient(app)


def _seed_source() -> int:
    with get_session() as session:
        src = Source(code="test", name="Test Source", enabled=True)
        session.add(src)
        session.flush()
        return int(src.id)


def _seed_snapshot(
    *,
    source_id: int,
    url: str,
    normalized_url_group: str,
    capture_timestamp: datetime,
    mime_type: str,
) -> int:
    with get_session() as session:
        snap = Snapshot(
            job_id=None,
            source_id=source_id,
            url=url,
            normalized_url_group=normalized_url_group,
            capture_timestamp=capture_timestamp,
            mime_type=mime_type,
            status_code=200,
            title="Test Page",
            snippet="Snippet",
            language="en",
            warc_path="/tmp/test.warc.gz",
            warc_record_id="test",
        )
        session.add(snap)
        session.flush()
        return int(snap.id)


def test_snapshot_latest_returns_latest_html_by_default(tmp_path, monkeypatch) -> None:
    client = _init_test_app(tmp_path, monkeypatch)
    source_id = _seed_source()

    url = "https://example.org/page"
    group = "https://example.org/page"
    older_html_id = _seed_snapshot(
        source_id=source_id,
        url=url,
        normalized_url_group=group,
        capture_timestamp=datetime(2025, 1, 1, 12, 0, tzinfo=timezone.utc),
        mime_type="text/html; charset=utf-8",
    )
    newer_pdf_id = _seed_snapshot(
        source_id=source_id,
        url=url,
        normalized_url_group=group,
        capture_timestamp=datetime(2025, 2, 1, 12, 0, tzinfo=timezone.utc),
        mime_type="application/pdf",
    )

    resp = client.get(f"/api/snapshots/{newer_pdf_id}/latest")
    assert resp.status_code == 200
    body = resp.json()
    assert body["found"] is True
    assert body["snapshotId"] == older_html_id


def test_snapshot_latest_can_return_non_html_when_requested(tmp_path, monkeypatch) -> None:
    client = _init_test_app(tmp_path, monkeypatch)
    source_id = _seed_source()

    url = "https://example.org/page"
    group = "https://example.org/page"
    _seed_snapshot(
        source_id=source_id,
        url=url,
        normalized_url_group=group,
        capture_timestamp=datetime(2025, 1, 1, 12, 0, tzinfo=timezone.utc),
        mime_type="text/html",
    )
    newer_pdf_id = _seed_snapshot(
        source_id=source_id,
        url=url,
        normalized_url_group=group,
        capture_timestamp=datetime(2025, 2, 1, 12, 0, tzinfo=timezone.utc),
        mime_type="application/pdf",
    )

    resp = client.get(f"/api/snapshots/{newer_pdf_id}/latest", params={"requireHtml": "0"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["found"] is True
    assert body["snapshotId"] == newer_pdf_id


def test_snapshot_latest_returns_found_false_when_no_html(tmp_path, monkeypatch) -> None:
    client = _init_test_app(tmp_path, monkeypatch)
    source_id = _seed_source()

    url = "https://example.org/page"
    group = "https://example.org/page"
    pdf_id = _seed_snapshot(
        source_id=source_id,
        url=url,
        normalized_url_group=group,
        capture_timestamp=datetime(2025, 2, 1, 12, 0, tzinfo=timezone.utc),
        mime_type="application/pdf",
    )

    resp = client.get(f"/api/snapshots/{pdf_id}/latest")
    assert resp.status_code == 200
    body = resp.json()
    assert body["found"] is False


def test_snapshot_latest_returns_404_for_missing_snapshot(tmp_path, monkeypatch) -> None:
    client = _init_test_app(tmp_path, monkeypatch)

    resp = client.get("/api/snapshots/999/latest")
    assert resp.status_code == 404
