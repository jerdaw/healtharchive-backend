from __future__ import annotations

import hashlib

from ha_backend.indexing.warc_reader import ArchiveRecord
from ha_backend.models import ArchiveJob, Snapshot, Source
from ha_backend.url_normalization import normalize_url_for_grouping


def compute_content_hash(body_bytes: bytes) -> str:
    """
    Compute a simple SHA-256 hash of the response body.
    """
    return hashlib.sha256(body_bytes).hexdigest()


def record_to_snapshot(
    job: ArchiveJob,
    source: Source,
    rec: ArchiveRecord,
    *,
    title: str | None,
    snippet: str,
    language: str,
) -> Snapshot:
    """
    Map an ArchiveRecord plus extracted metadata into a Snapshot ORM object.
    """
    normalized_group = normalize_url_for_grouping(rec.url)
    content_hash = compute_content_hash(rec.body_bytes)

    snapshot = Snapshot(
        job=job,
        source=source,
        url=rec.url,
        normalized_url_group=normalized_group,
        capture_timestamp=rec.capture_timestamp,
        mime_type=rec.mime_type,
        status_code=rec.status_code,
        title=title,
        snippet=snippet,
        language=language,
        warc_path=str(rec.warc_path),
        warc_record_id=rec.warc_record_id,
        raw_snapshot_path=None,
        content_hash=content_hash,
    )
    return snapshot


__all__ = ["normalize_url_for_grouping", "compute_content_hash", "record_to_snapshot"]
