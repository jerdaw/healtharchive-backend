from __future__ import annotations

from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path

from fastapi.testclient import TestClient
from warcio.warcwriter import WARCWriter

from ha_backend import db as db_module
from ha_backend.db import Base, get_engine, get_session
from ha_backend.live_compare import LiveFetchBlocked, LiveFetchNotHtml, LiveFetchResult
from ha_backend.models import Snapshot, Source


def _init_test_app(tmp_path: Path, monkeypatch) -> TestClient:
    db_path = tmp_path / "compare_live.db"
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
    warc_path.parent.mkdir(parents=True, exist_ok=True)
    with warc_path.open("wb") as f:
        writer = WARCWriter(f, gzip=True)
        payload = BytesIO(
            (
                "HTTP/1.1 200 OK\r\n"
                "Content-Type: text/html; charset=utf-8\r\n"
                f"Content-Length: {len(html.encode('utf-8'))}\r\n"
                "\r\n"
                f"{html}"
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


def _seed_snapshot_with_warc(
    tmp_path: Path, *, url: str, html: str, mime_type: str = "text/html"
) -> int:
    warc_dir = tmp_path / "warcs"
    warc_file = warc_dir / "test.warc.gz"
    record_id = _write_test_warc(warc_file, url, html)

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
            mime_type=mime_type,
            status_code=200,
            title="Test Page",
            snippet="Snippet",
            language="en",
            warc_path=str(warc_file),
            warc_record_id=record_id,
        )
        session.add(snap)
        session.flush()
        return int(snap.id)


def test_compare_live_disabled_returns_404(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("HEALTHARCHIVE_COMPARE_LIVE_ENABLED", "0")
    client = _init_test_app(tmp_path, monkeypatch)

    resp = client.get("/api/snapshots/1/compare-live")
    assert resp.status_code == 404


def test_compare_live_non_html_snapshot_returns_422(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("HEALTHARCHIVE_COMPARE_LIVE_ENABLED", "1")
    client = _init_test_app(tmp_path, monkeypatch)

    snapshot_id = _seed_snapshot_with_warc(
        tmp_path,
        url="https://example.org/page",
        html="<html><body>ignored</body></html>",
        mime_type="application/pdf",
    )

    resp = client.get(f"/api/snapshots/{snapshot_id}/compare-live")
    assert resp.status_code == 422


def test_compare_live_success(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("HEALTHARCHIVE_COMPARE_LIVE_ENABLED", "1")
    client = _init_test_app(tmp_path, monkeypatch)

    snapshot_id = _seed_snapshot_with_warc(
        tmp_path,
        url="https://example.org/page",
        html="<html><main><h1>Title</h1><p>Old text</p></main></html>",
        mime_type="text/html; charset=utf-8",
    )

    def _fake_fetch_live_html(*_args, **_kwargs):
        return LiveFetchResult(
            requested_url="https://example.org/page",
            final_url="https://example.org/page",
            status_code=200,
            content_type="text/html; charset=utf-8",
            bytes_read=123,
            fetched_at=datetime(2025, 12, 25, 12, 0, tzinfo=timezone.utc),
            html="<html><main><h1>Title</h1><p>New text</p></main></html>",
        )

    monkeypatch.setattr("ha_backend.api.routes_public.fetch_live_html", _fake_fetch_live_html)

    resp = client.get(f"/api/snapshots/{snapshot_id}/compare-live")
    assert resp.status_code == 200
    assert resp.headers.get("cache-control") == "no-store"
    assert resp.headers.get("x-robots-tag") == "noindex, nofollow"

    body = resp.json()
    assert body["archivedSnapshot"]["snapshotId"] == snapshot_id
    assert body["liveFetch"]["statusCode"] == 200
    assert "diffHtml" in body["diff"]
    assert "New text" in body["diff"]["diffHtml"]
    assert "render" in body
    assert body["textModeRequested"] == "main"
    assert body["textModeUsed"] == "main"
    assert body["textModeFallback"] is False
    assert "Old text" in body["render"]["archivedLines"]
    assert "New text" in body["render"]["liveLines"]
    assert any(
        instruction["type"] == "replace" for instruction in body["render"]["renderInstructions"]
    )


def test_compare_live_full_mode_includes_page_chrome(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("HEALTHARCHIVE_COMPARE_LIVE_ENABLED", "1")
    client = _init_test_app(tmp_path, monkeypatch)

    snapshot_id = _seed_snapshot_with_warc(
        tmp_path,
        url="https://example.org/page",
        html="<html><body><header>Banner</header><main><h1>Title</h1><p>Old text</p></main></body></html>",
        mime_type="text/html; charset=utf-8",
    )

    def _fake_fetch_live_html(*_args, **_kwargs):
        return LiveFetchResult(
            requested_url="https://example.org/page",
            final_url="https://example.org/page",
            status_code=200,
            content_type="text/html; charset=utf-8",
            bytes_read=123,
            fetched_at=datetime(2025, 12, 25, 12, 0, tzinfo=timezone.utc),
            html="<html><body><header>Banner</header><main><h1>Title</h1><p>New text</p></main></body></html>",
        )

    monkeypatch.setattr("ha_backend.api.routes_public.fetch_live_html", _fake_fetch_live_html)

    resp = client.get(f"/api/snapshots/{snapshot_id}/compare-live", params={"mode": "full"})
    assert resp.status_code == 200

    body = resp.json()
    assert body["textModeRequested"] == "full"
    assert body["textModeUsed"] == "full"
    assert body["textModeFallback"] is False
    assert "Banner" in body["render"]["archivedLines"]


def test_compare_live_falls_back_to_full_page_when_main_is_empty(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("HEALTHARCHIVE_COMPARE_LIVE_ENABLED", "1")
    client = _init_test_app(tmp_path, monkeypatch)

    snapshot_id = _seed_snapshot_with_warc(
        tmp_path,
        url="https://example.org/page",
        html="<html><body><header><h1>Title</h1><p>Old text</p></header></body></html>",
        mime_type="text/html; charset=utf-8",
    )

    def _fake_fetch_live_html(*_args, **_kwargs):
        return LiveFetchResult(
            requested_url="https://example.org/page",
            final_url="https://example.org/page",
            status_code=200,
            content_type="text/html; charset=utf-8",
            bytes_read=123,
            fetched_at=datetime(2025, 12, 25, 12, 0, tzinfo=timezone.utc),
            html="<html><body><header><h1>Title</h1><p>New text</p></header></body></html>",
        )

    monkeypatch.setattr("ha_backend.api.routes_public.fetch_live_html", _fake_fetch_live_html)

    resp = client.get(f"/api/snapshots/{snapshot_id}/compare-live")
    assert resp.status_code == 200

    body = resp.json()
    assert body["textModeRequested"] == "main"
    assert body["textModeUsed"] == "full"
    assert body["textModeFallback"] is True
    assert "Old text" in body["render"]["archivedLines"]
    assert "New text" in body["render"]["liveLines"]


def test_compare_live_live_not_html_maps_to_422(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("HEALTHARCHIVE_COMPARE_LIVE_ENABLED", "1")
    client = _init_test_app(tmp_path, monkeypatch)

    snapshot_id = _seed_snapshot_with_warc(
        tmp_path,
        url="https://example.org/page",
        html="<html><body>Old</body></html>",
    )

    def _fake_fetch_live_html(*_args, **_kwargs):
        raise LiveFetchNotHtml("Live URL is not HTML.")

    monkeypatch.setattr("ha_backend.api.routes_public.fetch_live_html", _fake_fetch_live_html)

    resp = client.get(f"/api/snapshots/{snapshot_id}/compare-live")
    assert resp.status_code == 422


def test_compare_live_blocked_maps_to_400(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("HEALTHARCHIVE_COMPARE_LIVE_ENABLED", "1")
    client = _init_test_app(tmp_path, monkeypatch)

    snapshot_id = _seed_snapshot_with_warc(
        tmp_path,
        url="https://example.org/page",
        html="<html><body>Old</body></html>",
    )

    def _fake_fetch_live_html(*_args, **_kwargs):
        raise LiveFetchBlocked("Live fetch blocked by safety rules.")

    monkeypatch.setattr("ha_backend.api.routes_public.fetch_live_html", _fake_fetch_live_html)

    resp = client.get(f"/api/snapshots/{snapshot_id}/compare-live")
    assert resp.status_code == 400


def test_compare_live_archived_too_large_maps_to_413(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("HEALTHARCHIVE_COMPARE_LIVE_ENABLED", "1")
    monkeypatch.setenv("HEALTHARCHIVE_COMPARE_LIVE_MAX_ARCHIVE_BYTES", "100000")
    client = _init_test_app(tmp_path, monkeypatch)

    def _should_not_fetch_live(*_args, **_kwargs):
        raise AssertionError(
            "fetch_live_html should not be called when archived HTML exceeds limit"
        )

    monkeypatch.setattr("ha_backend.api.routes_public.fetch_live_html", _should_not_fetch_live)

    snapshot_id = _seed_snapshot_with_warc(
        tmp_path,
        url="https://example.org/page",
        html="<html><body>" + ("A" * 150_000) + "</body></html>",
    )

    resp = client.get(f"/api/snapshots/{snapshot_id}/compare-live")
    assert resp.status_code == 413
