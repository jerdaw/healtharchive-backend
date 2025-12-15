from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from fastapi.testclient import TestClient

from ha_backend import db as db_module
from ha_backend.db import Base, get_engine, get_session
from ha_backend.models import PageSignal, Snapshot, SnapshotOutlink, Source, Topic


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


def _seed_search_quality_data() -> None:
    """
    Seed extra snapshots to validate ordering and default quality filters.
    """
    _seed_search_data()

    with get_session() as session:
        hc = session.query(Source).filter(Source.code == "hc").one()

        ts4 = datetime(2025, 4, 1, 12, 0, tzinfo=timezone.utc)
        s4 = Snapshot(
            job_id=None,
            source_id=hc.id,
            url="https://www.canada.ca/en/health-canada/updates.html",
            normalized_url_group="https://www.canada.ca/en/health-canada/updates.html",
            capture_timestamp=ts4,
            mime_type="text/html",
            status_code=200,
            title="Latest public health updates",
            snippet="Bulletin: COVID-19 guidance updates and notices.",
            language="en",
            warc_path="/warcs/hc-updates.warc.gz",
            warc_record_id="hc-updates",
        )

        ts5 = datetime(2025, 5, 1, 12, 0, tzinfo=timezone.utc)
        s5 = Snapshot(
            job_id=None,
            source_id=hc.id,
            url="https://www.canada.ca/en/health-canada/missing/covid-19.html",
            normalized_url_group="https://www.canada.ca/en/health-canada/missing/covid-19.html",
            capture_timestamp=ts5,
            mime_type="text/html",
            status_code=404,
            title="COVID-19 not found (404)",
            snippet="Not Found: COVID-19 resource could not be located.",
            language="en",
            warc_path="/warcs/hc-missing.warc.gz",
            warc_record_id="hc-missing",
        )

        session.add_all([s4, s5])


def _seed_pages_view_data() -> None:
    """
    Seed multiple snapshots for the same normalized_url_group to validate view=pages.
    """
    _seed_search_data()

    with get_session() as session:
        hc = session.query(Source).filter(Source.code == "hc").one()

        # A newer capture of the same page as s1.
        ts4 = datetime(2025, 4, 15, 12, 0, tzinfo=timezone.utc)
        s4 = Snapshot(
            job_id=None,
            source_id=hc.id,
            url="https://www.canada.ca/en/health-canada/covid19.html",
            normalized_url_group="https://www.canada.ca/en/health-canada/covid19.html",
            capture_timestamp=ts4,
            mime_type="text/html",
            status_code=200,
            title="COVID-19 guidance (updated)",
            snippet="Updated COVID-19 guidance from Health Canada.",
            language="en",
            warc_path="/warcs/hc-covid-2.warc.gz",
            warc_record_id="hc-covid-2",
        )
        session.add(s4)


def _seed_authority_ranking_data() -> None:
    """
    Seed a minimal dataset to validate PageSignal boosts tie-break relevance.
    """
    with get_session() as session:
        hc = Source(
            code="hc",
            name="Health Canada",
            base_url="https://www.canada.ca/en/health-canada.html",
            description="Health Canada",
            enabled=True,
        )
        session.add(hc)
        session.flush()

        ts_old = datetime(2025, 1, 1, 12, 0, tzinfo=timezone.utc)
        ts_new = datetime(2025, 2, 1, 12, 0, tzinfo=timezone.utc)

        authoritative = Snapshot(
            job_id=None,
            source_id=hc.id,
            url="https://example.org/hub",
            normalized_url_group="https://example.org/hub",
            capture_timestamp=ts_old,
            mime_type="text/html",
            status_code=200,
            title="COVID-19 hub page",
            snippet="COVID-19 overview and resources.",
            language="en",
            warc_path="/warcs/hub.warc.gz",
            warc_record_id="hub",
        )
        newer = Snapshot(
            job_id=None,
            source_id=hc.id,
            url="https://example.org/updates",
            normalized_url_group="https://example.org/updates",
            capture_timestamp=ts_new,
            mime_type="text/html",
            status_code=200,
            title="COVID-19 updates",
            snippet="COVID-19 updates and notices.",
            language="en",
            warc_path="/warcs/updates.warc.gz",
            warc_record_id="updates",
        )

        session.add_all([authoritative, newer])
        session.flush()

        session.add(
            PageSignal(
                normalized_url_group="https://example.org/hub",
                inlink_count=100,
            )
        )


def _seed_pages_view_best_match_ranking_data() -> None:
    """
    Seed data that distinguishes v1 vs v2 ranking for view=pages.

    Scenario:
    - Group A has two matching snapshots:
      - Older snapshot matches query in title (strong match).
      - Newer snapshot matches only in snippet (weak match) and is the displayed "latest" snapshot.
    - Group B has a single matching snapshot that matches only in snippet and is newer than Group A's latest.

    Under v1 pages ranking (latest snapshot scored), Group B should appear first (recency tie-break).
    Under v2 pages ranking (group scored by best snapshot), Group A should appear first while still
    returning the latest snapshot for that group.
    """
    with get_session() as session:
        hc = Source(
            code="hc",
            name="Health Canada",
            base_url="https://www.canada.ca/en/health-canada.html",
            description="Health Canada",
            enabled=True,
        )
        session.add(hc)
        session.flush()

        ts_old = datetime(2025, 1, 1, 12, 0, tzinfo=timezone.utc)
        ts_latest_a = datetime(2025, 2, 1, 12, 0, tzinfo=timezone.utc)
        ts_b = datetime(2025, 3, 1, 12, 0, tzinfo=timezone.utc)

        # Group A: older title match.
        a1 = Snapshot(
            job_id=None,
            source_id=hc.id,
            url="https://example.org/a",
            normalized_url_group="https://example.org/a",
            capture_timestamp=ts_old,
            mime_type="text/html",
            status_code=200,
            title="COVID-19 guidance hub",
            snippet="General guidance.",
            language="en",
            warc_path="/warcs/a1.warc.gz",
            warc_record_id="a1",
        )
        # Group A: newer snippet-only match (display snapshot under view=pages).
        a2 = Snapshot(
            job_id=None,
            source_id=hc.id,
            url="https://example.org/a",
            normalized_url_group="https://example.org/a",
            capture_timestamp=ts_latest_a,
            mime_type="text/html",
            status_code=200,
            title="Latest updates",
            snippet="COVID-19 update bulletin.",
            language="en",
            warc_path="/warcs/a2.warc.gz",
            warc_record_id="a2",
        )
        # Group B: snippet-only match, but newer than Group A's latest.
        b1 = Snapshot(
            job_id=None,
            source_id=hc.id,
            url="https://example.org/b",
            normalized_url_group="https://example.org/b",
            capture_timestamp=ts_b,
            mime_type="text/html",
            status_code=200,
            title="Updates",
            snippet="COVID-19 bulletin.",
            language="en",
            warc_path="/warcs/b1.warc.gz",
            warc_record_id="b1",
        )

        session.add_all([a1, a2, b1])


def _seed_hubness_ranking_data() -> None:
    """
    Seed data to validate v2 "hubness" boost for broad (1-token) queries.

    - Two pages match "covid" equally via title/snippet/url.
    - The "hub" page has many outlinks and is older.
    - The "non-hub" page has no outlinks and is newer.

    Under v1 relevance, the newer page should win (tie-break by recency).
    Under v2 relevance, the hub page should win due to hubness boost.
    """
    with get_session() as session:
        hc = Source(
            code="hc",
            name="Health Canada",
            base_url="https://www.canada.ca/en/health-canada.html",
            description="Health Canada",
            enabled=True,
        )
        session.add(hc)
        session.flush()

        ts_old = datetime(2025, 1, 1, 12, 0, tzinfo=timezone.utc)
        ts_new = datetime(2025, 2, 1, 12, 0, tzinfo=timezone.utc)

        hub = Snapshot(
            job_id=None,
            source_id=hc.id,
            url="https://example.org/covid",
            normalized_url_group="https://example.org/covid",
            capture_timestamp=ts_old,
            mime_type="text/html",
            status_code=200,
            title="COVID-19 hub",
            snippet="COVID-19 overview and resources.",
            language="en",
            warc_path="/warcs/hub.warc.gz",
            warc_record_id="hub",
        )
        non_hub = Snapshot(
            job_id=None,
            source_id=hc.id,
            url="https://example.org/covid-updates",
            normalized_url_group="https://example.org/covid-updates",
            capture_timestamp=ts_new,
            mime_type="text/html",
            status_code=200,
            title="COVID-19 updates",
            snippet="COVID-19 overview and resources.",
            language="en",
            warc_path="/warcs/updates.warc.gz",
            warc_record_id="updates",
        )
        session.add_all([hub, non_hub])
        session.flush()

        session.add_all(
            [
                SnapshotOutlink(
                    snapshot_id=hub.id,
                    to_normalized_url_group=f"https://example.org/child/{i}",
                )
                for i in range(60)
            ]
        )


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
    first_result = data["results"][0]
    assert isinstance(first_result["topics"], list)
    if first_result["topics"]:
        first_topic = first_result["topics"][0]
        assert "slug" in first_topic
        assert "label" in first_topic


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
    assert any(
        t["slug"] == "covid-19" and t["label"] == "COVID-19"
        for t in result["topics"]
    )


def test_search_default_relevance_sort_and_filters_non_2xx(tmp_path, monkeypatch) -> None:
    client = _init_test_app(tmp_path, monkeypatch)
    _seed_search_quality_data()

    resp = client.get("/api/search", params={"q": "COVID-19"})
    assert resp.status_code == 200
    data = resp.json()

    # The seeded 404 page should be filtered out by default.
    assert data["total"] == 2
    titles = [r["title"] for r in data["results"]]
    assert titles[0] == "COVID-19 guidance"
    assert "COVID-19 not found (404)" not in titles


def test_search_include_non_2xx_includes_error_pages_but_ranks_lower(
    tmp_path, monkeypatch
) -> None:
    client = _init_test_app(tmp_path, monkeypatch)
    _seed_search_quality_data()

    resp = client.get(
        "/api/search", params={"q": "COVID-19", "includeNon2xx": True}
    )
    assert resp.status_code == 200
    data = resp.json()

    assert data["total"] == 3
    titles = [r["title"] for r in data["results"]]
    assert titles[0] == "COVID-19 guidance"
    assert "COVID-19 not found (404)" in titles


def test_search_sort_newest(tmp_path, monkeypatch) -> None:
    client = _init_test_app(tmp_path, monkeypatch)
    _seed_search_quality_data()

    resp = client.get("/api/search", params={"q": "COVID-19", "sort": "newest"})
    assert resp.status_code == 200
    data = resp.json()

    assert data["total"] == 2
    titles = [r["title"] for r in data["results"]]
    assert titles[0] == "Latest public health updates"


def test_search_view_pages_dedupes_by_normalized_url_group(tmp_path, monkeypatch) -> None:
    client = _init_test_app(tmp_path, monkeypatch)
    _seed_pages_view_data()

    resp = client.get("/api/search", params={"view": "pages"})
    assert resp.status_code == 200
    data = resp.json()

    # We seeded 4 snapshots but only 3 distinct page groups.
    assert data["total"] == 3
    assert len(data["results"]) == 3

    urls = [r["originalUrl"] for r in data["results"]]
    assert urls.count("https://www.canada.ca/en/health-canada/covid19.html") == 1


def test_search_view_pages_returns_latest_snapshot_for_group(tmp_path, monkeypatch) -> None:
    client = _init_test_app(tmp_path, monkeypatch)
    _seed_pages_view_data()

    resp = client.get("/api/search", params={"q": "COVID-19", "view": "pages"})
    assert resp.status_code == 200
    data = resp.json()

    assert data["total"] == 1
    assert data["results"][0]["title"] == "COVID-19 guidance (updated)"


def test_search_view_pages_ranking_v2_scores_best_snapshot_for_group(
    tmp_path, monkeypatch
) -> None:
    client = _init_test_app(tmp_path, monkeypatch)
    _seed_pages_view_best_match_ranking_data()

    # v1 (default): both displayed pages match only in snippet, so tie-break by recency → group B first.
    resp_v1 = client.get("/api/search", params={"q": "COVID-19", "view": "pages"})
    assert resp_v1.status_code == 200
    data_v1 = resp_v1.json()
    assert data_v1["total"] == 2
    assert data_v1["results"][0]["originalUrl"] == "https://example.org/b"

    # v2: group A should be boosted because an older snapshot matched in title (max score per group).
    resp_v2 = client.get(
        "/api/search",
        params={"q": "COVID-19", "view": "pages", "ranking": "v2"},
    )
    assert resp_v2.status_code == 200
    data_v2 = resp_v2.json()
    assert data_v2["total"] == 2

    # The displayed snapshot for group A should still be the latest snapshot in that group (a2).
    assert data_v2["results"][0]["originalUrl"] == "https://example.org/a"
    assert data_v2["results"][0]["title"] == "Latest updates"


def test_search_relevance_uses_page_signal_boost_for_tie_breaks(
    tmp_path, monkeypatch
) -> None:
    client = _init_test_app(tmp_path, monkeypatch)
    _seed_authority_ranking_data()

    resp = client.get("/api/search", params={"q": "COVID-19", "sort": "relevance"})
    assert resp.status_code == 200
    data = resp.json()

    assert data["total"] == 2
    titles = [r["title"] for r in data["results"]]
    # The authoritative page is older, but should be boosted ahead of the newer one.
    assert titles[0] == "COVID-19 hub page"


def test_search_ranking_v2_hubness_boosts_hub_pages_for_broad_queries(
    tmp_path, monkeypatch
) -> None:
    client = _init_test_app(tmp_path, monkeypatch)
    _seed_hubness_ranking_data()

    resp_v1 = client.get(
        "/api/search",
        params={"q": "covid", "sort": "relevance", "view": "snapshots", "ranking": "v1"},
    )
    assert resp_v1.status_code == 200
    results_v1 = resp_v1.json()["results"]
    assert results_v1
    assert results_v1[0]["originalUrl"] == "https://example.org/covid-updates"

    resp_v2 = client.get(
        "/api/search",
        params={"q": "covid", "sort": "relevance", "view": "snapshots", "ranking": "v2"},
    )
    assert resp_v2.status_code == 200
    results_v2 = resp_v2.json()["results"]
    assert results_v2
    assert results_v2[0]["originalUrl"] == "https://example.org/covid"


def test_search_filters_by_topic_slug(tmp_path, monkeypatch) -> None:
    client = _init_test_app(tmp_path, monkeypatch)
    _seed_search_data()

    resp = client.get("/api/search", params={"topic": "covid-19"})
    assert resp.status_code == 200
    data = resp.json()

    assert data["total"] == 1
    result = data["results"][0]
    assert any(
        t["slug"] == "covid-19" and t["label"] == "COVID-19"
        for t in result["topics"]
    )


def test_search_returns_empty_for_unknown_topic_slug(tmp_path, monkeypatch) -> None:
    client = _init_test_app(tmp_path, monkeypatch)
    _seed_search_data()

    resp = client.get("/api/search", params={"topic": "non-existent-topic"})
    assert resp.status_code == 200
    data = resp.json()

    assert data["total"] == 0
    assert data["results"] == []


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
    assert isinstance(body["topics"], list)
    if body["topics"]:
        first_topic = body["topics"][0]
        assert "slug" in first_topic
        assert "label" in first_topic


def test_sources_endpoint_topics_shape(tmp_path, monkeypatch) -> None:
    client = _init_test_app(tmp_path, monkeypatch)
    _seed_search_data()

    resp = client.get("/api/sources")
    assert resp.status_code == 200
    data = resp.json()

    assert isinstance(data, list)
    assert data
    for source in data:
        assert "topics" in source
        assert isinstance(source["topics"], list)
        for topic in source["topics"]:
            assert "slug" in topic
            assert "label" in topic


def test_snapshot_detail_not_found(tmp_path, monkeypatch) -> None:
    client = _init_test_app(tmp_path, monkeypatch)

    resp = client.get("/api/snapshot/9999")
    assert resp.status_code == 404


def test_topics_endpoint(tmp_path, monkeypatch) -> None:
    client = _init_test_app(tmp_path, monkeypatch)
    _seed_search_data()

    resp = client.get("/api/topics")
    assert resp.status_code == 200
    data = resp.json()

    # We seeded two topics: COVID-19 and Influenza.
    slugs = {t["slug"] for t in data}
    labels = {t["label"] for t in data}

    assert "covid-19" in slugs
    assert "flu" in slugs
    assert "COVID-19" in labels
    assert "Influenza" in labels


def test_search_pagination_defaults(tmp_path, monkeypatch) -> None:
    client = _init_test_app(tmp_path, monkeypatch)
    _seed_search_data()

    resp = client.get("/api/search")
    assert resp.status_code == 200
    data = resp.json()

    assert data["page"] == 1
    assert data["pageSize"] == 20
    assert data["total"] == 3
    assert len(data["results"]) == 3


def test_search_pagination_custom_and_out_of_range(tmp_path, monkeypatch) -> None:
    client = _init_test_app(tmp_path, monkeypatch)
    _seed_search_data()

    # With 3 rows, page 1 size 2 should return 2 results.
    resp_page1 = client.get("/api/search", params={"page": 1, "pageSize": 2})
    assert resp_page1.status_code == 200
    data_page1 = resp_page1.json()
    assert data_page1["page"] == 1
    assert data_page1["pageSize"] == 2
    assert data_page1["total"] == 3
    assert len(data_page1["results"]) == 2

    # Page 2 with size 2 should return the remaining 1 result.
    resp_page2 = client.get("/api/search", params={"page": 2, "pageSize": 2})
    assert resp_page2.status_code == 200
    data_page2 = resp_page2.json()
    assert data_page2["page"] == 2
    assert data_page2["pageSize"] == 2
    assert data_page2["total"] == 3
    assert len(data_page2["results"]) == 1

    # Page 3 with size 2 is out-of-range for 3 total rows: empty results, total unchanged.
    resp_page3 = client.get("/api/search", params={"page": 3, "pageSize": 2})
    assert resp_page3.status_code == 200
    data_page3 = resp_page3.json()
    assert data_page3["page"] == 3
    assert data_page3["pageSize"] == 2
    assert data_page3["total"] == 3
    assert data_page3["results"] == []


def test_search_invalid_page_and_page_size(tmp_path, monkeypatch) -> None:
    client = _init_test_app(tmp_path, monkeypatch)
    _seed_search_data()

    # page must be >= 1
    resp_page0 = client.get("/api/search", params={"page": 0})
    assert resp_page0.status_code == 422

    # pageSize must be between 1 and 100
    resp_negative_page_size = client.get("/api/search", params={"pageSize": 0})
    assert resp_negative_page_size.status_code == 422

    resp_too_large_page_size = client.get("/api/search", params={"pageSize": 101})
    assert resp_too_large_page_size.status_code == 422


def test_search_invalid_query_params(tmp_path, monkeypatch) -> None:
    client = _init_test_app(tmp_path, monkeypatch)
    _seed_search_data()

    # q longer than 256 characters should be rejected.
    long_q = "a" * 300
    resp_long_q = client.get("/api/search", params={"q": long_q})
    assert resp_long_q.status_code == 422

    # source and topic must match slug regex.
    resp_bad_source = client.get("/api/search", params={"source": "HC!"})
    assert resp_bad_source.status_code == 422

    resp_bad_topic = client.get("/api/search", params={"topic": "covid 19"})
    assert resp_bad_topic.status_code == 422
