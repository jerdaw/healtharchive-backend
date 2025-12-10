from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from fastapi.testclient import TestClient

from ha_backend import db as db_module
from ha_backend.db import Base, get_engine, get_session
from ha_backend.models import Snapshot, Source, Topic


def _init_test_app(tmp_path: Path, monkeypatch):
    """
    Configure a temporary SQLite DB and return a FastAPI TestClient.
    """
    db_path = tmp_path / "api_search.db"
    monkeypatch.setenv("HEALTHARCHIVE_DATABASE_URL", f"sqlite:///{db_path}")

    db_module._engine = None
    db_module._SessionLocal = None

    engine = get_engine()
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)

    from ha_backend.api import app

    return TestClient(app)


def _seed_search_data() -> None:
    """
    Seed a few sources, topics, and snapshots for search tests.
    """
    with get_session() as session:
        hc = Source(
            code="hc",
            name="Health Canada",
            base_url="https://www.canada.ca/en/health-canada.html",
            description="Health Canada",
            enabled=True,
        )
        phac = Source(
            code="phac",
            name="Public Health Agency of Canada",
            base_url="https://www.canada.ca/en/public-health.html",
            description="PHAC",
            enabled=True,
        )
        session.add_all([hc, phac])
        session.flush()

        topic_covid = Topic(slug="covid-19", label="COVID-19")
        topic_flu = Topic(slug="flu", label="Influenza")
        session.add_all([topic_covid, topic_flu])
        session.flush()

        ts1 = datetime(2025, 1, 1, 12, 0, tzinfo=timezone.utc)
        ts2 = datetime(2025, 2, 15, 12, 0, tzinfo=timezone.utc)
        ts3 = datetime(2025, 3, 10, 12, 0, tzinfo=timezone.utc)

        s1 = Snapshot(
            job_id=None,
            source_id=hc.id,
            url="https://www.canada.ca/en/health-canada/covid19.html",
            normalized_url_group="https://www.canada.ca/en/health-canada/covid19.html",
            capture_timestamp=ts1,
            mime_type="text/html",
            status_code=200,
            title="COVID-19 guidance",
            snippet="Latest COVID-19 guidance from Health Canada.",
            language="en",
            warc_path="/warcs/hc-covid.warc.gz",
            warc_record_id="hc-covid",
        )
        s1.topics.append(topic_covid)

        s2 = Snapshot(
            job_id=None,
            source_id=phac.id,
            url="https://www.canada.ca/en/public-health/flu.html",
            normalized_url_group="https://www.canada.ca/en/public-health/flu.html",
            capture_timestamp=ts2,
            mime_type="text/html",
            status_code=200,
            title="Flu recommendations",
            snippet="Seasonal flu vaccine recommendations.",
            language="en",
            warc_path="/warcs/phac-flu.warc.gz",
            warc_record_id="phac-flu",
        )
        s2.topics.append(topic_flu)

        s3 = Snapshot(
            job_id=None,
            source_id=hc.id,
            url="https://www.canada.ca/en/health-canada/general-health.html",
            normalized_url_group="https://www.canada.ca/en/health-canada/general-health.html",
            capture_timestamp=ts3,
            mime_type="text/html",
            status_code=200,
            title="General health advice",
            snippet="General guidance for staying healthy.",
            language="en",
            warc_path="/warcs/hc-general.warc.gz",
            warc_record_id="hc-general",
        )

        session.add_all([s1, s2, s3])


def test_search_endpoint_basic(tmp_path, monkeypatch) -> None:
    client = _init_test_app(tmp_path, monkeypatch)
    _seed_search_data()

    resp = client.get("/api/search")
    assert resp.status_code == 200
    data = resp.json()

    assert "results" in data
    assert "total" in data
    assert data["total"] == 3
    assert len(data["results"]) == 3


def test_search_filters_by_source(tmp_path, monkeypatch) -> None:
    client = _init_test_app(tmp_path, monkeypatch)
    _seed_search_data()

    resp = client.get("/api/search", params={"source": "hc"})
    assert resp.status_code == 200
    data = resp.json()

    assert data["total"] == 2
    assert all(r["sourceCode"] == "hc" for r in data["results"])


def test_search_filters_by_query(tmp_path, monkeypatch) -> None:
    client = _init_test_app(tmp_path, monkeypatch)
    _seed_search_data()

    resp = client.get("/api/search", params={"q": "COVID-19"})
    assert resp.status_code == 200
    data = resp.json()

    assert data["total"] == 1
    result = data["results"][0]
    assert "COVID-19 guidance" in result["title"]


def test_snapshot_detail_endpoint(tmp_path, monkeypatch) -> None:
    client = _init_test_app(tmp_path, monkeypatch)
    _seed_search_data()

    # Grab a snapshot id directly from the DB.
    with get_session() as session:
        snap = session.query(Snapshot).first()
        snap_id = snap.id

    resp = client.get(f"/api/snapshot/{snap_id}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == snap_id
    assert body["originalUrl"]
    assert body["sourceCode"] in {"hc", "phac"}


def test_snapshot_detail_not_found(tmp_path, monkeypatch) -> None:
    client = _init_test_app(tmp_path, monkeypatch)

    resp = client.get("/api/snapshot/9999")
    assert resp.status_code == 404
