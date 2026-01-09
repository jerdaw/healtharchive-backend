from __future__ import annotations

import gzip
import io
import sys
from io import StringIO
from pathlib import Path

from warcio.statusandheaders import StatusAndHeaders
from warcio.warcwriter import WARCWriter

from ha_backend import cli as cli_module
from ha_backend import db as db_module
from ha_backend.db import Base, get_engine, get_session
from ha_backend.indexing.warc_verify import (
    WarcVerificationOptions,
    quarantine_warcs,
    verify_single_warc,
)
from ha_backend.models import ArchiveJob, Source


def _write_test_warc_gz(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wb") as gz:
        writer = WARCWriter(gz, gzip=False)
        http_headers = StatusAndHeaders(
            "200 OK",
            [("Content-Type", "text/html; charset=utf-8")],
            protocol="HTTP/1.0",
        )
        record = writer.create_warc_record(
            "http://example.com/",
            "response",
            payload=io.BytesIO(b"<html><body>ok</body></html>"),
            http_headers=http_headers,
        )
        writer.write_record(record)


def test_verify_single_warc_level1_detects_truncated_gzip(tmp_path: Path) -> None:
    good = tmp_path / "good.warc.gz"
    _write_test_warc_gz(good)

    bad = tmp_path / "bad.warc.gz"
    data = good.read_bytes()
    assert len(data) > 16
    bad.write_bytes(data[:-8])

    opts = WarcVerificationOptions(level=1)
    ok_res = verify_single_warc(good, options=opts)
    bad_res = verify_single_warc(bad, options=opts)

    assert ok_res.ok is True
    assert ok_res.gzip_ok is True

    assert bad_res.ok is False
    assert bad_res.gzip_ok is False
    assert bad_res.error_kind == "corrupt_or_unreadable"


def test_quarantine_warcs_preserves_relative_paths(tmp_path: Path) -> None:
    output_dir = tmp_path / "job-output"
    warcs_dir = output_dir / "warcs"
    warcs_dir.mkdir(parents=True)

    warc = warcs_dir / "a.warc.gz"
    _write_test_warc_gz(warc)

    quarantine_root = output_dir / "warcs_quarantine" / "test"
    moved = quarantine_warcs([warc], quarantine_root=quarantine_root, relative_to=output_dir)
    assert len(moved) == 1

    assert not warc.exists()
    dest = quarantine_root / "warcs" / "a.warc.gz"
    assert dest.is_file()


def _init_test_db(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "warc_verify.db"
    monkeypatch.setenv("HEALTHARCHIVE_DATABASE_URL", f"sqlite:///{db_path}")

    db_module._engine = None
    db_module._SessionLocal = None

    engine = get_engine()
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)


def test_cli_verify_warcs_can_quarantine_and_mark_retryable(tmp_path: Path, monkeypatch) -> None:
    _init_test_db(tmp_path, monkeypatch)

    output_dir = tmp_path / "job-output"
    warcs_dir = output_dir / "warcs"
    warcs_dir.mkdir(parents=True, exist_ok=True)

    good = warcs_dir / "good.warc.gz"
    _write_test_warc_gz(good)
    corrupt = warcs_dir / "corrupt.warc.gz"
    data = good.read_bytes()
    corrupt.write_bytes(data[:-8])

    with get_session() as session:
        src = Source(code="hc", name="Health Canada", enabled=True)
        session.add(src)
        session.flush()

        created_job = ArchiveJob(
            source_id=src.id,
            name="hc-verify",
            output_dir=str(output_dir),
            status="completed",
            retry_count=2,
        )
        session.add(created_job)
        session.flush()
        job_id = created_job.id

    report_path = tmp_path / "report.json"
    metrics_path = tmp_path / "metrics.prom"

    parser = cli_module.build_parser()
    args = parser.parse_args(
        [
            "verify-warcs",
            "--job-id",
            str(job_id),
            "--level",
            "1",
            "--apply-quarantine",
            "--json-out",
            str(report_path),
            "--metrics-file",
            str(metrics_path),
        ]
    )

    stdout = StringIO()
    old_stdout = sys.stdout
    try:
        sys.stdout = stdout
        try:
            args.func(args)
        except SystemExit as exc:
            # Verification should still report failures (even if quarantined).
            assert exc.code == 1
    finally:
        sys.stdout = old_stdout

    assert report_path.is_file()
    assert metrics_path.is_file()
    assert "healtharchive_warc_verify_job_ok" in metrics_path.read_text(encoding="utf-8")

    # Corrupt file should be moved under warcs_quarantine/<timestamp>/warcs/.
    moved_corrupt = list(output_dir.glob("warcs_quarantine/*/warcs/corrupt.warc.gz"))
    assert len(moved_corrupt) == 1
    assert not corrupt.exists()

    # Job should be reset for a retry.
    with get_session() as session:
        stored_job = session.get(ArchiveJob, job_id)
        assert stored_job is not None
        assert stored_job.status == "retryable"
        assert stored_job.retry_count == 0
