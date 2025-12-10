from __future__ import annotations

from pathlib import Path
from typing import Optional

from ha_backend.indexing.warc_reader import ArchiveRecord, iter_html_records
from ha_backend.models import Snapshot


def find_record_for_snapshot(snapshot: Snapshot) -> Optional[ArchiveRecord]:
    """
    Locate the WARC response record corresponding to a Snapshot.

    For now we primarily rely on the stored warc_record_id. If that is not
    available, we fall back to the first HTML response in the WARC that matches
    the snapshot URL.
    """
    warc_path = Path(snapshot.warc_path)
    if not warc_path.is_file():
        return None

    # Prefer exact record ID match when we have one.
    target_id = snapshot.warc_record_id
    if target_id:
        for rec in iter_html_records(warc_path):
            if rec.warc_record_id == target_id:
                return rec

    # Fallback: first record matching URL.
    for rec in iter_html_records(warc_path):
        if rec.url == snapshot.url:
            return rec

    return None


__all__ = ["find_record_for_snapshot"]
