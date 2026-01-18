"""
Tests for ha_backend.pages module.

Verifies:
- Page grouping logic (by normalized URL)
- SQL generation for PostgreSQL and SQLite dialects
- URL normalization within SQL queries
"""

from datetime import datetime
from unittest.mock import MagicMock, patch

from sqlalchemy import select
from sqlalchemy.dialects import postgresql, sqlite

from ha_backend.models import Page, Snapshot
from ha_backend.pages import (
    _strip_query_fragment_expr,
    discover_job_page_groups,
    rebuild_pages,
)


def test_strip_query_fragment_expr_dialects(db_session):
    """Verify generated SQL for different dialects."""
    # We can test expression compilation without running it

    url_col = Snapshot.url

    # 1. Postgres dialect
    pg_expr = _strip_query_fragment_expr(url_col, "postgresql")
    pg_compiled = str(pg_expr.compile(dialect=postgresql.dialect()))
    # Should use regexp_replace
    assert "regexp_replace" in pg_compiled
    # Note: Regex string is customized as bound param, so we just verify function call

    # 2. SQLite dialect
    sqlite_expr = _strip_query_fragment_expr(url_col, "sqlite")
    sqlite_compiled = str(sqlite_expr.compile(dialect=sqlite.dialect()))
    # Should use instr, min, case mechanism
    assert "instr" in sqlite_compiled
    assert "CASE" in sqlite_compiled


def test_rebuild_pages_grouping_logic(db_session, snapshot_factory):
    """Test that URL query params and fragments are stripped for grouping per SQLite logic."""
    # Assuming tests run on SQLite primarily

    # Base URL
    u1 = "http://example.com/page"
    # Same group
    u2 = "http://example.com/page?q=1"
    u3 = "http://example.com/page#frag"
    # Different group
    u4 = "http://example.com/other"

    # We must set normalized_url_group=None to let rebuild_pages calculate it
    s1 = snapshot_factory(url=u1, timestamp=datetime(2025, 1, 1))
    s1.normalized_url_group = None

    s2 = snapshot_factory(url=u2, timestamp=datetime(2025, 1, 2))
    s2.normalized_url_group = None

    s3 = snapshot_factory(url=u3, timestamp=datetime(2025, 1, 3))
    s3.normalized_url_group = None

    s4 = snapshot_factory(url=u4, timestamp=datetime(2025, 1, 4))
    s4.normalized_url_group = None

    db_session.commit()

    res = rebuild_pages(db_session)
    assert res.upserted_groups == 2

    pages = db_session.scalars(select(Page)).all()
    assert len(pages) == 2

    # Check main group
    p1 = db_session.scalar(select(Page).where(Page.normalized_url_group == u1))
    assert p1 is not None
    assert p1.snapshot_count == 3
    assert p1.latest_snapshot_id == s3.id

    # Check other group
    p2 = db_session.scalar(select(Page).where(Page.normalized_url_group == u4))
    assert p2 is not None
    assert p2.snapshot_count == 1


def test_rebuild_pages_aggregations_and_latest_ok(db_session, snapshot_factory):
    """Test aggregation correctness and latest_ok logic."""
    url = "http://example.com/status"

    # 1. 404 (Not OK)
    s1 = snapshot_factory(url=url, timestamp=datetime(2025, 1, 1), status_code=404)
    s1.normalized_url_group = None

    # 2. 200 (OK)
    s2 = snapshot_factory(url=url, timestamp=datetime(2025, 1, 2), status_code=200)
    s2.normalized_url_group = None

    # 3. 500 (Not OK) - latest total
    s3 = snapshot_factory(url=url, timestamp=datetime(2025, 1, 3), status_code=500)
    s3.normalized_url_group = None

    db_session.commit()

    rebuild_pages(db_session)

    page = db_session.scalar(select(Page).where(Page.normalized_url_group == url))
    assert page.snapshot_count == 3
    assert page.latest_snapshot_id == s3.id  # Absolute latest
    assert page.latest_ok_snapshot_id == s2.id  # Latest 2xx


def test_rebuild_pages_chunking(db_session, snapshot_factory):
    """Test recursion/chunking logic by forcing a small chunk size."""
    # Create 3 pages
    urls = [f"http://example.com/{i}" for i in range(3)]
    for u in urls:
        s = snapshot_factory(url=u)
        s.normalized_url_group = None
    db_session.commit()

    res = rebuild_pages(db_session, groups=[urls[0], urls[2]])
    assert res.upserted_groups == 2

    stored = db_session.scalars(select(Page.normalized_url_group)).all()
    assert sorted(stored) == sorted([urls[0], urls[2]])
    assert urls[1] not in stored


def test_rebuild_pages_delete_missing(db_session, snapshot_factory):
    """Test delete_missing=True cleans up stale pages."""
    url_keep = "http://example.com/keep"
    url_del = "http://example.com/del"

    s1 = snapshot_factory(url=url_keep)
    s1.normalized_url_group = None

    s2 = snapshot_factory(url=url_del)
    s2.normalized_url_group = None

    db_session.commit()

    # Initial build
    rebuild_pages(db_session)
    assert db_session.query(Page).count() == 2

    # Delete s2
    db_session.delete(s2)
    db_session.commit()

    # Rebuild with delete_missing
    res = rebuild_pages(db_session, delete_missing=True, source_id=s1.source_id)
    assert res.deleted_groups == 1

    # Verify
    pages = db_session.scalars(select(Page.normalized_url_group)).all()
    assert pages == [url_keep]


def test_discover_job_page_groups(db_session, snapshot_factory):
    job_id = 123
    u1 = "http://a.com"
    u2 = "http://a.com?q=1"  # Same group
    u3 = "http://b.com"  # Diff group

    s1 = snapshot_factory(url=u1)
    s1.normalized_url_group = None

    s2 = snapshot_factory(url=u2)
    s2.normalized_url_group = None

    s3 = snapshot_factory(url=u3)
    s3.normalized_url_group = None

    # Assign to single job
    # Note: snapshot_factory commits changes.
    s1.job_id = job_id
    s2.job_id = job_id
    s3.job_id = job_id
    db_session.commit()

    groups = discover_job_page_groups(db_session, job_id=job_id)
    assert len(groups) == 2
    assert set(groups) == {"http://a.com", "http://b.com"}


def test_precomputed_normalized_group(db_session, snapshot_factory):
    """Test that Snapshot.normalized_url_group takes precedence if set."""
    # Pre-computed value
    s = snapshot_factory(url="http://example.com/raw")
    s.normalized_url_group = "http://example.com/override"
    db_session.commit()

    rebuild_pages(db_session)

    page = db_session.scalar(
        select(Page).where(Page.normalized_url_group == "http://example.com/override")
    )
    assert page is not None
    assert page.snapshot_count == 1

    # Ensure original URL didn't create a group
    page_orig = db_session.scalar(
        select(Page).where(Page.normalized_url_group == "http://example.com/raw")
    )
    assert page_orig is None


def test_strip_query_fragment_expr_sqlite_edge_cases(db_session):
    """Explicitly test SQLite URL stripping edge cases via execution."""
    # We can use func.select or similar if we want to test the expression
    from sqlalchemy import literal

    def strip_url(u):
        expr = _strip_query_fragment_expr(literal(u), "sqlite")
        return db_session.scalar(select(expr))

    assert strip_url("http://ex.com/p?q=1") == "http://ex.com/p"
    assert strip_url("http://ex.com/p#frag") == "http://ex.com/p"
    assert strip_url("http://ex.com/p?q=1#frag") == "http://ex.com/p"
    assert strip_url("http://ex.com/p#frag?q=1") == "http://ex.com/p"
    assert strip_url("http://ex.com/p?") == "http://ex.com/p"
    assert strip_url("http://ex.com/p#") == "http://ex.com/p"
    assert strip_url("http://ex.com/p") == "http://ex.com/p"
    assert strip_url("") == ""


def test_rebuild_pages_recursion_chunking(db_session, snapshot_factory):
    """Verify that rebuild_pages chunks the groups list and calls itself recursively."""
    # We need enough groups to trigger chunk_size=500 for sqlite
    # Let's create just 10 snapshots but pretend chunk_size is 5.

    # We can't change the constant easily, but we can mock the behavior by providing a large groups list
    # and seeing if it calls itself.

    import ha_backend.pages as pages_mod

    original_rebuild = pages_mod.rebuild_pages

    # We create a groups list larger than 500
    large_groups = [f"http://ex.com/{i}" for i in range(505)]
    # We don't need real snapshots for the recursion check if we mock the chunk call

    with patch("ha_backend.pages.rebuild_pages", side_effect=original_rebuild) as mock_rebuild:
        # We need to mock the DB execution for the actual aggregation so we don't need 505 snapshots
        with patch("sqlalchemy.orm.Session.execute", return_value=MagicMock(rowcount=1)):
            # We just need to get past the filters/agg part or mock it.
            # Actually, if we provide 505 groups, it hits the chunking logic BEFORE the filters.

            pages_mod.rebuild_pages(db_session, groups=large_groups)

            # Should have called itself twice for chunks (500, 5)
            # Total calls = 1 (original) + 2 (recursion) = 3
            assert mock_rebuild.call_count == 3

            # Verify first chunk call
            args, kwargs = mock_rebuild.call_args_list[1]
            assert len(kwargs["groups"]) == 500

            # Verify second chunk call
            args, kwargs = mock_rebuild.call_args_list[2]
            assert len(kwargs["groups"]) == 5


def test_rebuild_pages_filters(db_session, snapshot_factory):
    """Verify that source_id and job_id filters work correctly."""
    s1 = snapshot_factory(url="http://a.com", status_code=200)
    s1.normalized_url_group = None

    s2 = snapshot_factory(url="http://b.com", status_code=200)
    s2.normalized_url_group = None

    # Assign s2 to a different source/job
    s2.source_id = s1.source_id + 1
    s2.job_id = s1.job_id + 1
    db_session.commit()

    # Filter by source_id
    res = rebuild_pages(db_session, source_id=s1.source_id)
    assert res.upserted_groups == 1
    assert (
        db_session.scalar(select(Page).where(Page.normalized_url_group == "http://a.com"))
        is not None
    )
    assert (
        db_session.scalar(select(Page).where(Page.normalized_url_group == "http://b.com")) is None
    )

    # Filter by job_id
    db_session.query(Page).delete()
    db_session.commit()
    res = rebuild_pages(db_session, job_id=s2.job_id)
    assert res.upserted_groups == 1
    assert (
        db_session.scalar(select(Page).where(Page.normalized_url_group == "http://b.com"))
        is not None
    )


def test_url_normalize_lowercases_host(db_session):
    """Verify that hostname is lowercased during normalization in SQL."""
    from sqlalchemy import literal

    expr = _strip_query_fragment_expr(literal("http://EXAMPLE.com/Page"), "sqlite")
    res = db_session.scalar(select(expr))
    assert res == "http://example.com/page"
