"""
Tests for ha_backend.changes module.

Verifies:
- Backfill computation logic (pairwise comparison)
- Incremental change detection
- Edge cases: gaps, duplicates, first snapshot
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from ha_backend.changes import (
    CHANGE_TYPE_ERROR,
    CHANGE_TYPE_NEW_PAGE,
    CHANGE_TYPE_UNCHANGED,
    CHANGE_TYPE_UPDATED,
    _is_html_snapshot,
    compute_change_for_snapshot_pair,
    compute_changes_backfill,
    compute_changes_since,
)
from ha_backend.models import Snapshot, SnapshotChange

# Mocked HTML content
HTML_A = "<html><body><p>Content A</p></body></html>"
HTML_B = "<html><body><p>Content B</p></body></html>"
HTML_INVALID = "<html><body>...invalid...</body></html>"


@pytest.fixture
def mock_load_html(monkeypatch):
    """Mocks _load_snapshot_html to return predefined content based on snapshot ID."""
    mock = MagicMock()

    def side_effect(snapshot):
        # Use WARC record ID or some attribute to key response
        if snapshot.warc_record_id == "rec-a":
            return HTML_A
        if snapshot.warc_record_id == "rec-b":
            return HTML_B
        if snapshot.warc_record_id == "rec-error":
            raise ValueError("Read error")
        return ""

    mock.side_effect = side_effect
    monkeypatch.setattr("ha_backend.changes._load_snapshot_html", mock)
    return mock


def test_is_html_snapshot_logic():
    s = Snapshot(mime_type="text/html")
    assert _is_html_snapshot(s) is True

    s = Snapshot(mime_type="text/html; charset=utf-8")
    assert _is_html_snapshot(s) is True

    s = Snapshot(mime_type="application/pdf")
    assert _is_html_snapshot(s) is False

    s = Snapshot(mime_type=None)
    assert _is_html_snapshot(s) is False


def test_compute_change_new_page(snapshot_factory):
    snap = snapshot_factory(title="New Page")
    change = compute_change_for_snapshot_pair(snap, None)

    assert change.change_type == CHANGE_TYPE_NEW_PAGE
    assert change.from_snapshot_id is None
    assert change.to_snapshot_id == snap.id
    assert "First archived capture" in change.summary


def test_compute_change_unchanged(snapshot_factory):
    # Same content hash
    snap_a = snapshot_factory(content_hash="hash1", timestamp=datetime(2025, 1, 1))
    snap_b = snapshot_factory(content_hash="hash1", timestamp=datetime(2025, 1, 2))

    change = compute_change_for_snapshot_pair(snap_b, snap_a)

    assert change.change_type == CHANGE_TYPE_UNCHANGED
    assert change.from_snapshot_id == snap_a.id
    assert change.to_snapshot_id == snap_b.id
    assert "No text changes" in change.summary
    assert change.diff_html is None


def test_compute_change_updated(snapshot_factory, mock_load_html):
    # Different hash, mock HTML load
    snap_a = snapshot_factory(
        content_hash="hashA", timestamp=datetime(2025, 1, 1), warc_record_id="rec-a"
    )
    snap_b = snapshot_factory(
        content_hash="hashB", timestamp=datetime(2025, 1, 2), warc_record_id="rec-b"
    )

    change = compute_change_for_snapshot_pair(snap_b, snap_a)

    assert change.change_type == CHANGE_TYPE_UPDATED
    assert change.from_snapshot_id == snap_a.id
    assert change.to_snapshot_id == snap_b.id
    assert change.diff_html is not None
    assert "ha-diff-add" in change.diff_html
    assert "Content B" in change.diff_html
    assert change.added_lines > 0


def test_compute_change_non_html(snapshot_factory):
    snap_a = snapshot_factory(content_hash="hashA", mime_type="application/pdf")
    snap_b = snapshot_factory(content_hash="hashB", mime_type="application/pdf")

    change = compute_change_for_snapshot_pair(snap_b, snap_a)

    # Defaults to high-noise update without diff
    assert change.change_type == CHANGE_TYPE_UPDATED
    assert change.diff_html is None
    assert change.high_noise is True


def test_compute_change_error_handling(snapshot_factory, mock_load_html):
    snap_a = snapshot_factory(content_hash="hashA", warc_record_id="rec-a")
    snap_b = snapshot_factory(content_hash="hashB", warc_record_id="rec-error")

    change = compute_change_for_snapshot_pair(snap_b, snap_a)

    assert change.change_type == CHANGE_TYPE_ERROR
    assert change.error_message == "Read error"


def test_compute_changes_backfill_creates_events(db_session, snapshot_factory, monkeypatch):
    # Ensure tracking is enabled
    monkeypatch.setattr("ha_backend.changes.get_change_tracking_enabled", lambda: True)

    # Create a sequence of 3 snapshots for same URL
    url = "https://example.com/seq"
    s1 = snapshot_factory(url=url, timestamp=datetime(2025, 1, 1), content_hash="h1")
    s2 = snapshot_factory(url=url, timestamp=datetime(2025, 1, 2), content_hash="h1")  # Unchanged
    s3 = snapshot_factory(
        url=url, timestamp=datetime(2025, 1, 3), content_hash="h2"
    )  # Changed (simulated)

    # We need to mock _load_snapshot_html just in case compute_change actually runs it
    # But here h1==h1 so s2 should be UNCHANGED (no load needed).
    # s3 vs s2 has different hash, but we won't mock HTML so it might error or we mock compute_change?
    # Better to mock compute_change_for_snapshot_pair to avoid side effects

    with patch("ha_backend.changes.compute_change_for_snapshot_pair") as mock_compute:
        # Return a dummy change object
        def side_effect(to_snapshot, from_snapshot, **kwargs):
            return SnapshotChange(
                to_snapshot_id=to_snapshot.id, change_type="mocked", summary="mocked"
            )

        mock_compute.side_effect = side_effect

        res = compute_changes_backfill(db_session, max_events=10)

        assert res.created == 3  # s1(new), s2(unchanged), s3(change)
        assert mock_compute.call_count == 3

        # Verify call args logic (pairs)
        # Call 1: s1, None
        # Call 2: s2, s1
        # Call 3: s3, s2
        args_list = mock_compute.call_args_list
        assert args_list[0][1]["to_snapshot"].id == s1.id
        assert args_list[0][1]["from_snapshot"] is None

        assert args_list[1][1]["to_snapshot"].id == s2.id
        assert args_list[1][1]["from_snapshot"].id == s1.id

        assert args_list[2][1]["to_snapshot"].id == s3.id
        assert args_list[2][1]["from_snapshot"].id == s2.id


def test_compute_changes_backfill_skips_existing(db_session, snapshot_factory, monkeypatch):
    monkeypatch.setattr("ha_backend.changes.get_change_tracking_enabled", lambda: True)

    s1 = snapshot_factory(timestamp=datetime(2025, 1, 1), content_hash="h1")

    # Manually insert a change record for s1
    existing_change = SnapshotChange(to_snapshot_id=s1.id, change_type="manual", summary="test")
    db_session.add(existing_change)
    db_session.commit()

    res = compute_changes_backfill(db_session)
    assert res.skipped == 1
    assert res.created == 0


def test_compute_changes_since_respects_time(db_session, snapshot_factory, monkeypatch):
    monkeypatch.setattr("ha_backend.changes.get_change_tracking_enabled", lambda: True)

    # Old snapshot
    snapshot_factory(timestamp=datetime.now(timezone.utc) - timedelta(days=10))
    # New snapshot
    new_snap = snapshot_factory(timestamp=datetime.now(timezone.utc) - timedelta(hours=1))

    with patch("ha_backend.changes.compute_change_for_snapshot_pair") as mock_compute:
        mock_compute.return_value = SnapshotChange(
            to_snapshot_id=new_snap.id, change_type="mocked", summary="m"
        )

        # Since 1 day
        res = compute_changes_since(db_session, since_days=1)

        assert res.created == 1
        # Should process new_snap, but skip old_snap based on timestamp query filter
        args, kwargs = mock_compute.call_args
        assert kwargs["to_snapshot"].id == new_snap.id


def test_get_latest_job_ids_by_source_logic(db_session, snapshot_factory):
    from ha_backend.changes import get_latest_job_ids_by_source
    from ha_backend.models import ArchiveJob

    # Create two sources
    snap1 = snapshot_factory(url="http://a", timestamp=datetime(2025, 1, 1))
    source1_id = snap1.source_id

    # Update job status to 'indexed' (factory defaults to 'completed')
    job1 = db_session.query(ArchiveJob).get(snap1.job_id)
    job1.status = "indexed"
    db_session.commit()

    latest = get_latest_job_ids_by_source(db_session)
    assert latest[source1_id] == snap1.job_id

    # Add a newer job for same source
    snap2 = snapshot_factory(url="http://b", timestamp=datetime(2025, 1, 2))
    job2 = db_session.query(ArchiveJob).get(snap2.job_id)
    job2.status = "indexed"
    db_session.commit()

    latest = get_latest_job_ids_by_source(db_session)
    assert latest[source1_id] == snap2.job_id  # Should pick newest

    # Filter by source_id
    latest_filtered = get_latest_job_ids_by_source(db_session, source_id=999)
    assert 999 not in latest_filtered


def test_summarize_change_logic_branches():
    from ha_backend.changes import CHANGE_TYPE_NEW_PAGE, _summarize_change

    assert "First archived" in _summarize_change(change_type=CHANGE_TYPE_NEW_PAGE)
    assert "3 sections changed" in _summarize_change(change_type="updated", changed_sections=3)
    assert "2 added" in _summarize_change(change_type="updated", added_sections=2)
    assert "1 removed" in _summarize_change(change_type="updated", removed_sections=1)
    assert "high-noise" in _summarize_change(change_type="updated", added_lines=10, high_noise=True)
    assert "10 lines added" in _summarize_change(
        change_type="updated", added_lines=10, removed_lines=5
    )


def test_high_noise_threshold_trigger(snapshot_factory, mock_load_html, monkeypatch):
    from ha_backend.changes import compute_change_for_snapshot_pair

    snap_a = snapshot_factory(warc_record_id="rec-a", content_hash="h1")
    snap_b = snapshot_factory(warc_record_id="rec-b", content_hash="h2")

    # Mock compute_diff to return high ratio
    with patch("ha_backend.changes.compute_diff") as mock_diff:
        mock_diff.return_value = MagicMock(
            change_ratio=0.8,
            added_lines=10,
            removed_lines=10,
            diff_html="...",
            diff_truncated=False,
        )
        # Mocking normalize_html_for_diff to return dummy doc with some lines
        with patch("ha_backend.changes.normalize_html_for_diff") as mock_norm:
            mock_norm.return_value = MagicMock(sections=[], lines=["a"] * 5)

            change = compute_change_for_snapshot_pair(snap_b, snap_a)
            assert change.high_noise is True
            assert "(high-noise change)" in change.summary


def test_change_duplicate_timestamps(db_session, snapshot_factory, monkeypatch):
    """Verify that duplicate timestamps do not cause errors in backfill."""
    monkeypatch.setattr("ha_backend.changes.get_change_tracking_enabled", lambda: True)

    url = "https://example.com/dup"
    ts = datetime(2025, 1, 1)

    # Create two snapshots with same timestamp
    snapshot_factory(url=url, timestamp=ts, content_hash="h1")
    snapshot_factory(url=url, timestamp=ts, content_hash="h2")

    # Backfill should process them without crashing
    # Pairings might be (s1, None), (s2, s1) or similar depending on id order if TS matches.
    res = compute_changes_backfill(db_session)
    assert res.created == 2
