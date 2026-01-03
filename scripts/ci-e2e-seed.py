#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path

from warcio.warcwriter import WARCWriter

from ha_backend.db import Base, get_engine, get_session
from ha_backend.models import ArchiveJob, Snapshot, Source
from ha_backend.seeds import seed_sources


def _write_test_warc(warc_path: Path, url: str, html: str, *, warc_date: str) -> str:
    warc_path.parent.mkdir(parents=True, exist_ok=True)
    body = html.encode("utf-8")
    payload = BytesIO(
        (
            "HTTP/1.1 200 OK\r\n"
            "Content-Type: text/html; charset=utf-8\r\n"
            f"Content-Length: {len(body)}\r\n"
            "\r\n"
        ).encode("utf-8")
        + body
    )
    with warc_path.open("wb") as f:
        writer = WARCWriter(f, gzip=True)
        record = writer.create_warc_record(
            uri=url,
            record_type="response",
            payload=payload,
            warc_headers_dict={"WARC-Date": warc_date},
        )
        writer.write_record(record)

    record_id = record.rec_headers.get_header("WARC-Record-ID")
    if not record_id:
        raise RuntimeError("WARC writer did not provide a record id")
    return str(record_id)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Seed a minimal dataset for CI e2e smoke tests.")
    parser.add_argument("--db-path", required=True, help="SQLite database file path.")
    parser.add_argument("--warc-path", required=True, help="Output WARC (.warc.gz) path.")
    parser.add_argument(
        "--source-code",
        default="hc",
        help="Source code to attach the seeded snapshot to (default: hc).",
    )
    parser.add_argument(
        "--url",
        default="https://example.org/healtharchive-ci-e2e",
        help="Snapshot original URL (default: example.org/healtharchive-ci-e2e).",
    )
    parser.add_argument(
        "--title",
        default="HealthArchive CI E2E Seed",
        help="Snapshot title.",
    )
    args = parser.parse_args(argv)

    db_path = Path(str(args.db_path)).resolve()
    warc_path = Path(str(args.warc_path)).resolve()
    db_path.parent.mkdir(parents=True, exist_ok=True)

    os.environ["HEALTHARCHIVE_DATABASE_URL"] = f"sqlite:///{db_path}"

    engine = get_engine()
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)

    captured_at = datetime(2025, 1, 1, 12, 0, tzinfo=timezone.utc)
    warc_date = captured_at.strftime("%Y-%m-%dT%H:%M:%SZ")
    html = "<html><body><h1>Hello from HealthArchive CI E2E</h1></body></html>"
    record_id = _write_test_warc(warc_path, str(args.url), html, warc_date=warc_date)

    with get_session() as session:
        seed_sources(session)
        session.flush()

        source = session.query(Source).filter(Source.code == str(args.source_code)).one_or_none()
        if source is None:
            source = Source(code=str(args.source_code), name=str(args.source_code), enabled=True)
            session.add(source)
            session.flush()

        job = ArchiveJob(
            source_id=source.id,
            name=f"ci-e2e-{source.code}-2025-01-01",
            output_dir=str((db_path.parent / "ci-e2e-job").resolve()),
            status="indexed",
            config={"seeds": [str(args.url)]},
        )
        session.add(job)
        session.flush()

        snapshot = Snapshot(
            job_id=job.id,
            source_id=source.id,
            url=str(args.url),
            normalized_url_group=str(args.url),
            capture_timestamp=captured_at,
            mime_type="text/html",
            status_code=200,
            title=str(args.title),
            snippet="CI seeded snapshot (smoke test).",
            language="en",
            warc_path=str(warc_path),
            warc_record_id=record_id,
        )
        session.add(snapshot)
        session.commit()

    print(f"Seeded db={db_path} warc={warc_path} source={args.source_code} url={args.url}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
