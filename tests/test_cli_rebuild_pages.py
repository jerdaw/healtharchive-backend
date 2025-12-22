from __future__ import annotations

import sys
from datetime import datetime, timezone
from io import StringIO
from pathlib import Path

from ha_backend import cli as cli_module
from ha_backend import db as db_module
from ha_backend.db import Base, get_engine, get_session
from ha_backend.models import Snapshot, Source


def _init_test_db(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "cli_rebuild_pages.db"
    monkeypatch.setenv("HEALTHARCHIVE_DATABASE_URL", f"sqlite:///{db_path}")

    db_module._engine = None
    db_module._SessionLocal = None

    engine = get_engine()
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)


def _seed_snapshot(tmp_path: Path) -> None:
    with get_session() as session:
        src = Source(
            code="hc",
            name="Health Canada",
            base_url="https://www.canada.ca/en/health-canada.html",
            description="HC",
            enabled=True,
        )
        session.add(src)
        session.flush()

        session.add(
            Snapshot(
                job_id=None,
                source_id=src.id,
                url="https://www.canada.ca/en/health-canada/covid19.html",
                normalized_url_group="https://www.canada.ca/en/health-canada/covid19.html",
                capture_timestamp=datetime(2025, 4, 1, 12, 0, tzinfo=timezone.utc),
                mime_type="text/html",
                status_code=200,
                title="COVID-19",
                snippet="COVID-19 guidance",
                language="en",
                warc_path=str(tmp_path / "stub.warc.gz"),
                warc_record_id="stub",
            )
        )


def test_rebuild_pages_dry_run_truncate_message(tmp_path, monkeypatch) -> None:
    _init_test_db(tmp_path, monkeypatch)
    _seed_snapshot(tmp_path)

    parser = cli_module.build_parser()
    args = parser.parse_args(["rebuild-pages", "--truncate", "--dry-run"])

    stdout = StringIO()
    old_stdout = sys.stdout
    try:
        sys.stdout = stdout
        args.func(args)
    finally:
        sys.stdout = old_stdout

    out = stdout.getvalue()
    assert "DRY RUN: would truncate pages table" in out
    assert "Truncated pages table" not in out


def test_rebuild_pages_formats_negative_rowcount_as_unknown(tmp_path, monkeypatch) -> None:
    _init_test_db(tmp_path, monkeypatch)

    import ha_backend.pages as pages_module
    from ha_backend.pages import PagesRebuildResult

    def fake_rebuild_pages(*_args, **_kwargs):
        return PagesRebuildResult(upserted_groups=-1, deleted_groups=0)

    monkeypatch.setattr(pages_module, "rebuild_pages", fake_rebuild_pages)

    parser = cli_module.build_parser()
    args = parser.parse_args(["rebuild-pages"])

    stdout = StringIO()
    old_stdout = sys.stdout
    try:
        sys.stdout = stdout
        args.func(args)
    finally:
        sys.stdout = old_stdout

    out = stdout.getvalue()
    assert "upserted unknown page group(s)" in out
