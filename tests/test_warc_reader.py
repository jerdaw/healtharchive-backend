from __future__ import annotations

from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path

from warcio.warcwriter import WARCWriter

from ha_backend.indexing.warc_reader import iter_html_records


def _write_test_warc(warc_path: Path, url: str, html: str, warc_date: str) -> None:
    warc_path.parent.mkdir(parents=True, exist_ok=True)
    with warc_path.open("wb") as f:
        writer = WARCWriter(f, gzip=True)
        payload = BytesIO(
            (
                "HTTP/1.1 200 OK\r\n"
                "Content-Type: text/html; charset=utf-8\r\n"
                "Content-Length: " + str(len(html.encode("utf-8"))) + "\r\n"
                "\r\n" + html
            ).encode("utf-8")
        )
        record = writer.create_warc_record(
            uri=url,
            record_type="response",
            payload=payload,
            warc_headers_dict={"WARC-Date": warc_date},
        )
        writer.write_record(record)


def test_iter_html_records_parses_iso8601_warc_date(tmp_path: Path) -> None:
    warc_file = tmp_path / "test.warc.gz"
    _write_test_warc(
        warc_file,
        url="https://example.org/page",
        html="<html><body>ok</body></html>",
        warc_date="2025-01-01T12:00:00Z",
    )

    rec = next(iter_html_records(warc_file))
    assert rec.capture_timestamp == datetime(2025, 1, 1, 12, 0, tzinfo=timezone.utc)
