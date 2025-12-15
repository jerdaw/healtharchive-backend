from __future__ import annotations

from pathlib import Path
from datetime import datetime, timezone

from fastapi.testclient import TestClient

from ha_backend import db as db_module
from ha_backend.db import Base, get_engine
from ha_backend.db import get_session
from ha_backend.models import Snapshot, Source
from ha_backend.search_ranking import classify_query_mode, get_ranking_config


def _init_test_app(tmp_path: Path, monkeypatch) -> TestClient:
    db_path = tmp_path / "admin_search_debug.db"
    monkeypatch.setenv("HEALTHARCHIVE_DATABASE_URL", f"sqlite:///{db_path}")

    db_module._engine = None
    db_module._SessionLocal = None

    engine = get_engine()
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)

    from ha_backend.api import app

    return TestClient(app)


def test_admin_search_debug_endpoint_shape(tmp_path, monkeypatch) -> None:
    client = _init_test_app(tmp_path, monkeypatch)

    # With an empty DB, endpoint should still succeed and return stable metadata.
    resp = client.get("/api/admin/search-debug", params={"q": "covid", "ranking": "v2"})
    assert resp.status_code == 200
    body = resp.json()

    assert body["dialect"] == "sqlite"
    assert body["rankingVersion"] == "v2"
    assert body["view"] in {"snapshots", "pages"}
    assert body["sort"] in {"relevance", "newest"}
    assert isinstance(body["results"], list)


def test_admin_search_debug_v2_pages_uses_group_key_for_url_penalties(
    tmp_path, monkeypatch
) -> None:
    client = _init_test_app(tmp_path, monkeypatch)

    with get_session() as session:
        src = Source(
            code="hc",
            name="Health Canada",
            base_url="https://www.canada.ca/en/health-canada.html",
            description="Health Canada",
            enabled=True,
        )
        session.add(src)
        session.flush()

        session.add(
            Snapshot(
                job_id=None,
                source_id=src.id,
                # Canonical group key has no querystring.
                normalized_url_group="https://example.org/covid",
                # Latest snapshot URL includes tracking/query params, which should NOT
                # penalize view=pages in ranking=v2.
                url="https://example.org/covid?utm_campaign=x&wbdisable=true",
                capture_timestamp=datetime(2025, 1, 1, 0, 0, tzinfo=timezone.utc),
                mime_type="text/html",
                status_code=200,
                title="COVID hub",
                snippet="COVID overview page.",
                language="en",
                warc_path="/warcs/covid.warc.gz",
                warc_record_id="covid",
            )
        )

    resp = client.get(
        "/api/admin/search-debug",
        params={"q": "covid", "view": "pages", "ranking": "v2", "pageSize": 10},
    )
    assert resp.status_code == 200
    results = resp.json()["results"]
    assert len(results) == 1
    assert results[0]["queryPenalty"] == 0.0
    assert results[0]["trackingPenalty"] == 0.0


def test_admin_search_debug_v2_archived_penalty_triggers_on_archived_banner_in_snippet(
    tmp_path, monkeypatch
) -> None:
    client = _init_test_app(tmp_path, monkeypatch)

    with get_session() as session:
        src = Source(
            code="hc",
            name="Health Canada",
            base_url="https://www.canada.ca/en/health-canada.html",
            description="Health Canada",
            enabled=True,
        )
        session.add(src)
        session.flush()

        session.add(
            Snapshot(
                job_id=None,
                source_id=src.id,
                normalized_url_group="https://example.org/interim-order",
                url="https://example.org/interim-order",
                capture_timestamp=datetime(2025, 1, 1, 0, 0, tzinfo=timezone.utc),
                mime_type="text/html",
                status_code=200,
                # Title does NOT start with "Archived", but snippet includes an archived banner.
                title="Interim Order Respecting Something in Relation to COVID-19",
                snippet="We have archived this page and will not be updating it. COVID-19 reference.",
                language="en",
                warc_path="/warcs/interim-order.warc.gz",
                warc_record_id="interim-order",
            )
        )

    resp = client.get(
        "/api/admin/search-debug",
        params={"q": "covid", "view": "pages", "ranking": "v2", "pageSize": 10},
    )
    assert resp.status_code == 200
    results = resp.json()["results"]
    assert len(results) == 1

    expected_penalty = get_ranking_config(mode=classify_query_mode("covid")).archived_penalty
    assert results[0]["archivedPenalty"] == expected_penalty
