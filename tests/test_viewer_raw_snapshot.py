from __future__ import annotations

from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path

from fastapi.testclient import TestClient
from warcio.warcwriter import WARCWriter

from ha_backend import db as db_module
from ha_backend.db import Base, get_engine, get_session
from ha_backend.models import Snapshot, Source


def _init_test_app(tmp_path: Path, monkeypatch):
    """
    Configure a temporary SQLite DB and return a FastAPI TestClient.
    """
    db_path = tmp_path / "viewer.db"
    monkeypatch.setenv("HEALTHARCHIVE_DATABASE_URL", f"sqlite:///{db_path}")

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


def _write_test_warc(warc_path: Path, url: str, html: str) -> str:
    """
    Create a tiny WARC file with a single HTML response and return its record ID.
    """
    warc_path.parent.mkdir(parents=True, exist_ok=True)
    with warc_path.open("wb") as f:
        writer = WARCWriter(f, gzip=True)
        payload = BytesIO(
            (
                "HTTP/1.1 200 OK\r\n"
                "Content-Type: text/html; charset=utf-8\r\n"
                "Content-Length: " + str(len(html.encode("utf-8"))) + "\r\n"
                "\r\n" + html
            ).encode("utf-8")
        )
        record = writer.create_warc_record(
            uri=url,
            record_type="response",
            payload=payload,
            warc_headers_dict={"WARC-Date": "2025-01-01T12:00:00Z"},
        )
        writer.write_record(record)
        return record.rec_headers.get_header("WARC-Record-ID")


def test_raw_snapshot_route_serves_html(tmp_path, monkeypatch) -> None:
    client = _init_test_app(tmp_path, monkeypatch)

    warc_dir = tmp_path / "warcs"
    warc_file = warc_dir / "test.warc.gz"
    url = "https://example.org/page"
    html_body = "<html><body><h1>Hello from WARC</h1></body></html>"
    record_id = _write_test_warc(warc_file, url, html_body)

    with get_session() as session:
        src = Source(
            code="test",
            name="Test Source",
            base_url="https://example.org",
            description="Test",
            enabled=True,
        )
        session.add(src)
        session.flush()

        snap = Snapshot(
            job_id=None,
            source_id=src.id,
            url=url,
            normalized_url_group=url,
            capture_timestamp=datetime(2025, 1, 1, 12, 0, tzinfo=timezone.utc),
            mime_type="text/html",
            status_code=200,
            title="Test Page",
            snippet="Snippet",
            language="en",
            warc_path=str(warc_file),
            warc_record_id=record_id,
        )
        session.add(snap)
        session.flush()
        snapshot_id = snap.id

    resp = client.get(f"/api/snapshots/raw/{snapshot_id}")
    assert resp.status_code == 200
    assert "Hello from WARC" in resp.text


def test_raw_snapshot_missing_warc_returns_404(tmp_path, monkeypatch) -> None:
    """
    When the underlying WARC file is missing, the viewer should return 404
    with a meaningful error message.
    """
    client = _init_test_app(tmp_path, monkeypatch)

    missing_warc = tmp_path / "warcs" / "missing.warc.gz"
    url = "https://example.org/missing"

    with get_session() as session:
        src = Source(
            code="test",
            name="Test Source",
            base_url="https://example.org",
            description="Test",
            enabled=True,
        )
        session.add(src)
        session.flush()

        snap = Snapshot(
            job_id=None,
            source_id=src.id,
            url=url,
            normalized_url_group=url,
            capture_timestamp=datetime(2025, 1, 1, 12, 0, tzinfo=timezone.utc),
            mime_type="text/html",
            status_code=200,
            title="Missing WARC Page",
            snippet="Snapshot with missing WARC",
            language="en",
            warc_path=str(missing_warc),
            warc_record_id="missing-id",
        )
        session.add(snap)
        session.flush()
        snapshot_id = snap.id

    resp = client.get(f"/api/snapshots/raw/{snapshot_id}")
    assert resp.status_code == 404
    body = resp.json()
    assert "Underlying WARC file" in body["detail"]
