from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Dict, Iterable, Iterator, Optional

from warcio.archiveiterator import ArchiveIterator


@dataclass
class ArchiveRecord:
    """
    Simplified representation of a WARC HTTP response record that we care about.
    """

    url: str
    capture_timestamp: datetime
    status_code: Optional[int]
    mime_type: Optional[str]
    headers: Dict[str, str]
    body_bytes: bytes
    warc_record_id: Optional[str]
    warc_path: Path


def _parse_warc_datetime(warc_date: Optional[str]) -> datetime:
    """
    Parse a WARC-Date string into a timezone-aware datetime.
    """
    if not warc_date:
        return datetime.now(timezone.utc)
    try:
        dt = parsedate_to_datetime(warc_date)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return datetime.now(timezone.utc)


def iter_html_records(warc_path: Path) -> Iterator[ArchiveRecord]:
    """
    Yield ArchiveRecord objects for HTML-like HTTP responses in a WARC file.
    """
    warc_path = warc_path.resolve()
    with warc_path.open("rb") as f:
        for record in ArchiveIterator(f):
            try:
                if record.rec_type != "response":
                    continue

                url = record.rec_headers.get_header("WARC-Target-URI")
                if not url:
                    continue

                warc_date = record.rec_headers.get_header("WARC-Date")
                capture_ts = _parse_warc_datetime(warc_date)

                http_headers = getattr(record, "http_headers", None)
                status_code: Optional[int] = None
                mime_type: Optional[str] = None
                headers: Dict[str, str] = {}

                if http_headers is not None:
                    try:
                        sc = http_headers.get_statuscode()
                        status_code = int(sc) if sc is not None else None
                    except Exception:
                        status_code = None

                    for name, value in http_headers.headers:
                        headers[name.lower()] = value

                    ct = headers.get("content-type")
                    if ct:
                        mime_type = ct.split(";", 1)[0].strip().lower()

                # Only keep HTML-like responses. If mime_type is missing but the
                # URL looks like HTML, we still accept it.
                if mime_type and "html" not in mime_type:
                    continue

                body = record.content_stream().read()
                warc_record_id = record.rec_headers.get_header("WARC-Record-ID")

                yield ArchiveRecord(
                    url=url,
                    capture_timestamp=capture_ts,
                    status_code=status_code,
                    mime_type=mime_type,
                    headers=headers,
                    body_bytes=body,
                    warc_record_id=warc_record_id,
                    warc_path=warc_path,
                )
            except Exception:
                # For robustness, skip any individual record that fails parsing.
                continue


__all__ = ["ArchiveRecord", "iter_html_records"]
