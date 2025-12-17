from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from fastapi.testclient import TestClient

from ha_backend import db as db_module
from ha_backend.db import Base, get_engine, get_session
from ha_backend.models import ArchiveJob, PageSignal, Snapshot, SnapshotOutlink, Source


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
    Seed a few sources and snapshots for search tests.
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


def _seed_replay_browse_url_data() -> int:
    """
    Seed a single snapshot with a job_id so browseUrl can be constructed.

    Returns the created snapshot id.
    """
    with get_session() as session:
        hc = Source(
            code="hc",
            name="Health Canada",
            base_url="https://canada.ca/en/health-canada.html",
            description="Health Canada",
            enabled=True,
        )
        session.add(hc)
        session.flush()

        job = ArchiveJob(
            source_id=hc.id,
            name="legacy-hc-2025-04-21",
            output_dir="/srv/healtharchive/jobs/imports/legacy-hc-2025-04-21",
            status="indexed",
        )
        session.add(job)
        session.flush()

        ts = datetime(2025, 4, 18, 12, 1, 0, tzinfo=timezone.utc)
        snap = Snapshot(
            job_id=job.id,
            source_id=hc.id,
            url="https://canada.ca/en/health-canada.html",
            normalized_url_group="https://canada.ca/en/health-canada.html",
            capture_timestamp=ts,
            mime_type="text/html",
            status_code=200,
            title="Health Canada",
            snippet="Health Canada landing page.",
            language="en",
            warc_path="/warcs/hc-home.warc.gz",
            warc_record_id="hc-home",
        )
        session.add(snap)
        session.flush()
        return snap.id


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
    assert "topics" not in first_result


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
    assert "topics" not in result


def test_search_matches_query_tokens_out_of_order(tmp_path, monkeypatch) -> None:
    client = _init_test_app(tmp_path, monkeypatch)
    _seed_search_data()

    resp = client.get("/api/search", params={"q": "guidance covid"})
    assert resp.status_code == 200
    data = resp.json()

    assert data["total"] == 1
    assert "COVID-19 guidance" in data["results"][0]["title"]


def test_search_boolean_and(tmp_path, monkeypatch) -> None:
    client = _init_test_app(tmp_path, monkeypatch)
    _seed_search_data()

    resp = client.get("/api/search", params={"q": "covid AND guidance"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 1
    assert "COVID-19 guidance" in data["results"][0]["title"]


def test_search_boolean_or(tmp_path, monkeypatch) -> None:
    client = _init_test_app(tmp_path, monkeypatch)
    _seed_search_data()

    resp = client.get("/api/search", params={"q": "covid OR flu"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 2
    titles = {r["title"] for r in data["results"]}
    assert any("COVID-19 guidance" in (t or "") for t in titles)
    assert any("Flu recommendations" in (t or "") for t in titles)


def test_search_boolean_not(tmp_path, monkeypatch) -> None:
    client = _init_test_app(tmp_path, monkeypatch)
    _seed_search_data()

    resp = client.get("/api/search", params={"q": "covid NOT guidance"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 0


def test_search_boolean_dash_not(tmp_path, monkeypatch) -> None:
    client = _init_test_app(tmp_path, monkeypatch)
    _seed_search_data()

    resp = client.get("/api/search", params={"q": "flu -covid"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 1
    assert "Flu recommendations" in data["results"][0]["title"]


def test_search_boolean_parentheses(tmp_path, monkeypatch) -> None:
    client = _init_test_app(tmp_path, monkeypatch)
    _seed_search_data()

    resp = client.get(
        "/api/search",
        params={"q": "(covid OR flu) AND recommendations"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 1
    assert "Flu recommendations" in data["results"][0]["title"]


def test_search_boolean_field_url(tmp_path, monkeypatch) -> None:
    client = _init_test_app(tmp_path, monkeypatch)
    _seed_search_data()

    resp = client.get("/api/search", params={"q": "url:flu"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 1
    assert "Flu recommendations" in data["results"][0]["title"]


def test_search_boolean_field_url_with_dot_is_not_exact_url_lookup(tmp_path, monkeypatch) -> None:
    client = _init_test_app(tmp_path, monkeypatch)
    _seed_search_data()

    resp = client.get("/api/search", params={"q": "url:covid19.html"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 1
    assert "COVID-19 guidance" in data["results"][0]["title"]


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


def test_search_view_pages_returns_canonical_original_url(tmp_path, monkeypatch) -> None:
    client = _init_test_app(tmp_path, monkeypatch)

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

        session.add(
            Snapshot(
                job_id=None,
                source_id=hc.id,
                url="https://www.canada.ca/en/public-health/services/diseases/coronavirus-disease-covid-19.html?utm_campaign=x&wbdisable=true",
                normalized_url_group="https://www.canada.ca/en/public-health/services/diseases/coronavirus-disease-covid-19.html",
                capture_timestamp=datetime(2025, 4, 1, 12, 0, tzinfo=timezone.utc),
                mime_type="text/html",
                status_code=200,
                title="Coronavirus disease (COVID-19) - Canada.ca",
                snippet="COVID-19 hub page.",
                language="en",
                warc_path="/warcs/hc-covid-hub.warc.gz",
                warc_record_id="hc-covid-hub",
            )
        )

    resp_pages = client.get("/api/search", params={"q": "covid", "view": "pages"})
    assert resp_pages.status_code == 200
    data_pages = resp_pages.json()
    assert data_pages["total"] == 1
    assert (
        data_pages["results"][0]["originalUrl"]
        == "https://www.canada.ca/en/public-health/services/diseases/coronavirus-disease-covid-19.html"
    )

    resp_snaps = client.get("/api/search", params={"q": "covid", "view": "snapshots"})
    assert resp_snaps.status_code == 200
    data_snaps = resp_snaps.json()
    assert data_snaps["total"] == 1
    assert (
        data_snaps["results"][0]["originalUrl"]
        == "https://www.canada.ca/en/public-health/services/diseases/coronavirus-disease-covid-19.html?utm_campaign=x&wbdisable=true"
    )


def test_search_view_pages_dedupes_querystring_variants_without_normalized_group(
    tmp_path, monkeypatch
) -> None:
    client = _init_test_app(tmp_path, monkeypatch)

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

        session.add_all(
            [
                Snapshot(
                    job_id=None,
                    source_id=hc.id,
                    url="https://example.org/covid?wbdisable=true",
                    normalized_url_group=None,
                    capture_timestamp=datetime(2025, 4, 1, 12, 0, tzinfo=timezone.utc),
                    mime_type="text/html",
                    status_code=200,
                    title="COVID-19: Current situation",
                    snippet="COVID-19 hub page.",
                    language="en",
                    warc_path="/warcs/hc-covid-hub-1.warc.gz",
                    warc_record_id="hc-covid-hub-1",
                ),
                Snapshot(
                    job_id=None,
                    source_id=hc.id,
                    url="https://example.org/covid?wbdisable=false",
                    normalized_url_group=None,
                    capture_timestamp=datetime(2025, 4, 2, 12, 0, tzinfo=timezone.utc),
                    mime_type="text/html",
                    status_code=200,
                    title="COVID-19: Current situation",
                    snippet="COVID-19 hub page.",
                    language="en",
                    warc_path="/warcs/hc-covid-hub-2.warc.gz",
                    warc_record_id="hc-covid-hub-2",
                ),
            ]
        )

    resp_pages = client.get("/api/search", params={"q": "covid", "view": "pages"})
    assert resp_pages.status_code == 200
    data_pages = resp_pages.json()
    assert data_pages["total"] == 1
    assert len(data_pages["results"]) == 1
    assert data_pages["results"][0]["originalUrl"] == "https://example.org/covid"


def test_search_accepts_url_queries_and_matches_common_variants(
    tmp_path, monkeypatch
) -> None:
    client = _init_test_app(tmp_path, monkeypatch)

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

        session.add(
            Snapshot(
                job_id=None,
                source_id=hc.id,
                url="https://www.canada.ca/en/public-health/services/diseases/covid.html?utm_campaign=x",
                normalized_url_group="https://www.canada.ca/en/public-health/services/diseases/covid.html",
                capture_timestamp=datetime(2025, 4, 1, 12, 0, tzinfo=timezone.utc),
                mime_type="text/html",
                status_code=200,
                title="COVID-19: Current situation - Canada.ca",
                snippet="COVID-19 hub page.",
                language="en",
                warc_path="/warcs/hc-covid.warc.gz",
                warc_record_id="hc-covid",
            )
        )

    # No scheme (should assume https://).
    resp = client.get(
        "/api/search",
        params={"q": "www.canada.ca/en/public-health/services/diseases/covid.html"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 1

    # No www (should try www and non-www variants).
    resp = client.get(
        "/api/search",
        params={"q": "canada.ca/en/public-health/services/diseases/covid.html"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 1

    # http:// variant (should try both http/https).
    resp = client.get(
        "/api/search",
        params={"q": "http://www.canada.ca/en/public-health/services/diseases/covid.html"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 1

    # Explicit url: operator.
    resp = client.get(
        "/api/search",
        params={
            "q": "url:https://www.canada.ca/en/public-health/services/diseases/covid.html?utm_campaign=x"
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 1


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
    assert "topics" not in body


def test_snapshot_detail_includes_browse_url_when_replay_configured(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("HEALTHARCHIVE_REPLAY_BASE_URL", "https://replay.healtharchive.ca/")
    client = _init_test_app(tmp_path, monkeypatch)
    snap_id = _seed_replay_browse_url_data()

    resp = client.get(f"/api/snapshot/{snap_id}")
    assert resp.status_code == 200
    body = resp.json()

    assert body["jobId"] is not None
    assert body["captureTimestamp"] == "2025-04-18T12:01:00+00:00"
    assert (
        body["browseUrl"]
        == f"https://replay.healtharchive.ca/job-1/20250418120100/https://canada.ca/en/health-canada.html#ha_snapshot={snap_id}"
    )

    resp2 = client.get("/api/search")
    assert resp2.status_code == 200
    data = resp2.json()
    assert data["results"]
    assert data["results"][0]["browseUrl"].startswith("https://replay.healtharchive.ca/job-")


def test_sources_endpoint_shape(tmp_path, monkeypatch) -> None:
    client = _init_test_app(tmp_path, monkeypatch)
    _seed_search_data()

    resp = client.get("/api/sources")
    assert resp.status_code == 200
    data = resp.json()

    assert isinstance(data, list)
    assert data
    for source in data:
        assert source["sourceCode"] in {"hc", "phac"}
        assert isinstance(source.get("baseUrl"), (str, type(None)))
        assert isinstance(source.get("description"), (str, type(None)))
        assert isinstance(source["recordCount"], int)
        assert isinstance(source["firstCapture"], str)
        assert isinstance(source["lastCapture"], str)
        assert isinstance(source.get("entryRecordId"), (int, type(None)))
        assert isinstance(source.get("entryBrowseUrl"), (str, type(None)))


def test_snapshot_detail_not_found(tmp_path, monkeypatch) -> None:
    client = _init_test_app(tmp_path, monkeypatch)

    resp = client.get("/api/snapshot/9999")
    assert resp.status_code == 404


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

    # source must match slug regex.
    resp_bad_source = client.get("/api/search", params={"source": "HC!"})
    assert resp_bad_source.status_code == 422
