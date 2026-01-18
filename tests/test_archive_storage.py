"""
Tests for ha_backend.archive_storage module.

Verifies:
- WARC consolidation and integrity
- Manifest generation and parsing
- Storage statistics calculation
- Deduplication logic
"""

import errno
import json
import os
from unittest.mock import patch

import pytest

from ha_backend.archive_storage import (
    PROVENANCE_DIRNAME,
    STABLE_WARCS_DIRNAME,
    WARC_MANIFEST_FILENAME,
    _safe_link_or_copy,
    compute_job_storage_stats,
    consolidate_warcs,
    get_job_provenance_dir,
    get_job_warc_manifest_path,
    get_job_warcs_dir,
    snapshot_crawl_configs,
    snapshot_state_file,
)


def test_path_helpers(tmp_path):
    out_dir = tmp_path / "job_out"
    assert get_job_warcs_dir(out_dir) == out_dir / STABLE_WARCS_DIRNAME
    assert get_job_provenance_dir(out_dir) == out_dir / PROVENANCE_DIRNAME
    assert (
        get_job_warc_manifest_path(out_dir)
        == out_dir / STABLE_WARCS_DIRNAME / WARC_MANIFEST_FILENAME
    )


def test_safe_link_or_copy_hardlink(tmp_path):
    src = tmp_path / "src.warc"
    src.write_text("content")
    dest = tmp_path / "dest.warc"

    # Should use hardlink by default if possible (tmp_path usually allows it)
    link_type = _safe_link_or_copy(src, dest, allow_copy_fallback=False)

    assert dest.exists()
    assert dest.read_text() == "content"
    assert link_type == "hardlink"

    # Verify inodes match (hardlink)
    assert src.stat().st_ino == dest.stat().st_ino


def test_safe_link_or_copy_fallback(tmp_path):
    src = tmp_path / "src.warc"
    src.write_text("content")
    dest = tmp_path / "dest.warc"

    # Mock os.link to raise OSError(EXDEV) simulating cross-device link failure
    with patch("os.link", side_effect=OSError(errno.EXDEV, "Cross-device link")):
        link_type = _safe_link_or_copy(src, dest, allow_copy_fallback=True)

    assert dest.exists()
    assert dest.read_text() == "content"
    assert link_type == "copy"
    assert src.stat().st_ino != dest.stat().st_ino


def test_safe_link_or_copy_fail_no_fallback(tmp_path):
    src = tmp_path / "src.warc"
    src.write_text("content")
    dest = tmp_path / "dest.warc"

    with patch("os.link", side_effect=OSError(errno.EXDEV, "Cross-device link")):
        with pytest.raises(OSError):
            _safe_link_or_copy(src, dest, allow_copy_fallback=False)


def test_consolidate_warcs_basic(tmp_path):
    # Setup source file
    src_dir = tmp_path / "crawl_tmps" / "job_tmp"
    src_dir.mkdir(parents=True)
    warc1 = src_dir / "test.warc.gz"
    warc1.write_bytes(b"warc_data")

    output_dir = tmp_path / "job_out"

    res = consolidate_warcs(output_dir=output_dir, source_warc_paths=[warc1], dry_run=False)

    assert res.created == 1
    assert res.reused == 0
    assert len(res.stable_warcs) == 1

    stable_path = res.stable_warcs[0]
    assert stable_path.name == "warc-000001.warc.gz"
    assert stable_path.read_bytes() == b"warc_data"

    # Check manifest
    manifest_path = get_job_warc_manifest_path(output_dir)
    assert manifest_path.exists()
    data = json.loads(manifest_path.read_text())
    assert len(data["entries"]) == 1
    entry = data["entries"][0]
    assert entry["stable_name"] == "warc-000001.warc.gz"
    assert entry["source_path"] == str(warc1.resolve())


def test_consolidate_warcs_reuse_existing(tmp_path):
    src_dir = tmp_path / "crawl_tmps"
    src_dir.mkdir()
    warc1 = src_dir / "1.warc.gz"
    warc1.write_bytes(b"data1")

    output_dir = tmp_path / "job_out"

    # Run first consolidation
    res1 = consolidate_warcs(output_dir=output_dir, source_warc_paths=[warc1])
    assert res1.created == 1

    # Run second consolidation with same file
    res2 = consolidate_warcs(output_dir=output_dir, source_warc_paths=[warc1])
    assert res2.created == 0
    assert res2.reused == 1
    assert len(res2.stable_warcs) == 1
    assert res2.stable_warcs[0].name == "warc-000001.warc.gz"


def test_consolidate_warcs_multiple_files(tmp_path):
    src_dir = tmp_path / "crawl_tmps"
    src_dir.mkdir()
    files = []
    for i in range(3):
        p = src_dir / f"test_{i}.warc.gz"
        p.write_bytes(f"data_{i}".encode())
        files.append(p)

    output_dir = tmp_path / "job_out"
    res = consolidate_warcs(output_dir=output_dir, source_warc_paths=files)

    assert res.created == 3
    assert len(res.stable_warcs) == 3
    names = sorted([p.name for p in res.stable_warcs])
    assert names == ["warc-000001.warc.gz", "warc-000002.warc.gz", "warc-000003.warc.gz"]


def test_compute_job_storage_stats(tmp_path):
    out_dir = tmp_path / "job_out"
    out_dir.mkdir()

    # Create some files in output dir
    (out_dir / "a.txt").write_bytes(b"123")  # 3 bytes

    # Create a stable WARC
    warcs_dir = get_job_warcs_dir(out_dir)
    warcs_dir.mkdir(parents=True)
    stable_warc = warcs_dir / "stable.warc.gz"
    stable_warc.write_bytes(b"12345")  # 5 bytes

    # Create temp dir with hardlink to stable WARC (should be deduplicated)
    tmp_abc = tmp_path / "tmp_abc"
    tmp_abc.mkdir()
    tmp_warc = tmp_abc / "temp.warc.gz"
    os.link(stable_warc, tmp_warc)

    # Another file in temp
    (tmp_abc / "other.log").write_bytes(b"12")  # 2 bytes

    stats = compute_job_storage_stats(
        output_dir=out_dir, temp_dirs=[tmp_abc], stable_warc_paths=[stable_warc]
    )

    assert stats.warc_file_count == 1
    assert stats.warc_bytes_total == 5
    # output_bytes_total includes a.txt (3) and stable.warc.gz (5) -> 8
    assert stats.output_bytes_total == 8

    # tmp_bytes_total includes temp.warc.gz (5 but hardlinked) and other.log (2).
    # The helper `compute_tree_bytes` dedupes internal to the tree walk, but current implementation
    # merely totals the bytes seen in walk. `compute_tree_bytes` does internal deduping.
    # So tmp_abc has 5 + 2 = 7 bytes effectively.
    assert stats.tmp_bytes_total == 7

    assert stats.tmp_non_warc_bytes_total == 2  # Only other.log


def test_snapshot_state_file(tmp_path):
    out_dir = tmp_path / "job_out"
    out_dir.mkdir()

    # Provenance dir
    prov_dir = get_job_provenance_dir(out_dir)

    # No state file initially
    assert snapshot_state_file(out_dir, dest_dir=prov_dir) is None

    # Create state file
    (out_dir / ".archive_state.json").write_text("{}")

    dest = snapshot_state_file(out_dir, dest_dir=prov_dir)
    assert dest == prov_dir / "archive_state.json"
    assert dest.exists()


def test_snapshot_crawl_configs(tmp_path):
    # Setup temp structure
    tmp_dir = tmp_path / "tmp_crawl_123"
    crawls_dir = tmp_dir / "collections" / "crawl-1" / "crawls"
    crawls_dir.mkdir(parents=True)

    cfg1 = crawls_dir / "config.yaml"
    cfg1.write_text("config: 1")

    out_dir = tmp_path / "job_out"
    prov_dir = get_job_provenance_dir(out_dir)

    copied = snapshot_crawl_configs([tmp_dir], output_dir=out_dir, dest_dir=prov_dir)

    assert len(copied) >= 1
    expected_dest = (
        prov_dir
        / "crawl_configs"
        / "tmp_crawl_123"
        / "collections"
        / "crawl-1"
        / "crawls"
        / "config.yaml"
    )
    assert expected_dest.exists()
    assert expected_dest.read_text() == "config: 1"


def test_consolidate_warcs_dry_run(tmp_path):
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    warc = src_dir / "test.warc"
    warc.write_text("data")

    output_dir = tmp_path / "out"

    res = consolidate_warcs(output_dir=output_dir, source_warc_paths=[warc], dry_run=True)
    assert res.created == 1
    assert not get_job_warcs_dir(output_dir).exists()
    assert not get_job_warc_manifest_path(output_dir).exists()


def test_consolidate_warcs_indexing_gaps(tmp_path):
    output_dir = tmp_path / "out"
    warcs_dir = get_job_warcs_dir(output_dir)
    warcs_dir.mkdir(parents=True)

    # Create gaps: 1 and 3 exist
    (warcs_dir / "warc-000001.warc").write_text("1")
    (warcs_dir / "warc-000003.warc").write_text("3")

    src_warc = tmp_path / "new.warc"
    src_warc.write_text("new")

    res = consolidate_warcs(output_dir=output_dir, source_warc_paths=[src_warc])
    assert res.created == 1
    # Next index should be max(1, 3) + 1 = 4
    assert res.stable_warcs[0].name == "warc-000004.warc"


def test_consolidate_warcs_malformed_manifest(tmp_path):
    output_dir = tmp_path / "out"
    manifest_path = get_job_warc_manifest_path(output_dir)
    manifest_path.parent.mkdir(parents=True)
    manifest_path.write_text("NOT JSON")

    src_warc = tmp_path / "test.warc"
    src_warc.write_text("data")

    # Should ignore malformed manifest and start fresh
    res = consolidate_warcs(output_dir=output_dir, source_warc_paths=[src_warc])
    assert res.created == 1
    assert res.stable_warcs[0].name == "warc-000001.warc"


def test_safe_link_or_copy_overwrite_protection(tmp_path):
    src = tmp_path / "src.warc"
    src.write_text("src")
    dest = tmp_path / "dest.warc"
    dest.write_text("dest")  # Different content/inode

    # Should raise FileExistsError because dest exists but isn't a hardlink to src
    with pytest.raises(FileExistsError, match="Refusing to overwrite"):
        _safe_link_or_copy(src, dest, allow_copy_fallback=True)


def test_safe_link_or_copy_idempotent(tmp_path):
    src = tmp_path / "src.warc"
    src.write_text("data")
    dest = tmp_path / "dest.warc"
    os.link(src, dest)  # Already hardlinked

    # Should return 'hardlink' gracefully
    link_type = _safe_link_or_copy(src, dest, allow_copy_fallback=False)
    assert link_type == "hardlink"


def test_manifest_sha256_inclusion(tmp_path):
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    warc = src_dir / "test.warc"
    warc.write_text("content")  # SHA256 of "content"
    import hashlib

    expected_hash = hashlib.sha256(b"content").hexdigest()

    output_dir = tmp_path / "out"
    consolidate_warcs(output_dir=output_dir, source_warc_paths=[warc])

    manifest_path = get_job_warc_manifest_path(output_dir)
    data = json.loads(manifest_path.read_text())
    entry = data["entries"][0]
    assert entry["sha256"] == expected_hash


def test_storage_stats_handles_missing_directory(tmp_path):
    out_dir = tmp_path / "out"
    out_dir.mkdir()

    # Non-existent temp dir
    missing_dir = tmp_path / "missing"

    stats = compute_job_storage_stats(
        output_dir=out_dir, temp_dirs=[missing_dir], stable_warc_paths=[]
    )

    assert stats.tmp_bytes_total == 0
    assert stats.tmp_non_warc_bytes_total == 0
