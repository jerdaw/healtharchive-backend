from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from io import StringIO
from pathlib import Path

from archive_tool.state import CrawlState
from ha_backend import cli as cli_module
from ha_backend import db as db_module
from ha_backend.db import Base, get_engine, get_session
from ha_backend.models import ArchiveJob, Snapshot, Source
from ha_backend.url_normalization import normalize_url_for_grouping


def _init_test_db(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "cli_replay_reconcile.db"
    monkeypatch.setenv("HEALTHARCHIVE_DATABASE_URL", f"sqlite:///{db_path}")

    db_module._engine = None
    db_module._SessionLocal = None

    engine = get_engine()
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)


def _run_cli(args_list: list[str]) -> str:
    parser = cli_module.build_parser()
    args = parser.parse_args(args_list)

    stdout = StringIO()
    old_stdout = sys.stdout
    try:
        sys.stdout = stdout
        args.func(args)
    finally:
        sys.stdout = old_stdout

    return stdout.getvalue()


def _seed_indexed_job_with_warcs(tmp_path: Path, *, source_code: str = "hc") -> int:
    output_dir = tmp_path / "job-output"
    output_dir.mkdir(parents=True, exist_ok=True)

    temp_dir = output_dir / ".tmp1234"
    warc_dir = temp_dir / "collections" / "crawl-test" / "archive"
    warc_dir.mkdir(parents=True, exist_ok=True)

    state = CrawlState(output_dir, initial_workers=1)
    state.add_temp_dir(temp_dir)

    (warc_dir / "a.warc.gz").write_bytes(b"fake-warc-a")
    (warc_dir / "b.warc.gz").write_bytes(b"fake-warc-b")

    with get_session() as session:
        src = Source(
            code=source_code,
            name="Health Canada",
            base_url="https://www.canada.ca/en/health-canada.html",
            description="HC",
            enabled=True,
        )
        session.add(src)
        session.flush()

        job = ArchiveJob(
            source_id=src.id,
            name="job-reconcile",
            output_dir=str(output_dir),
            status="indexed",
        )
        session.add(job)
        session.flush()
        return job.id


def _seed_entry_snapshot(session, *, job_id: int, source: Source) -> None:
    url = source.base_url
    assert url
    group = normalize_url_for_grouping(url)
    assert group

    snap = Snapshot(
        job_id=job_id,
        source_id=source.id,
        url=url,
        normalized_url_group=group,
        capture_timestamp=datetime(2027, 1, 1, 0, 0, 0, tzinfo=timezone.utc),
        warc_path="warcs/fake.warc.gz",
        warc_record_id="rec-1",
        status_code=200,
        title="HC",
        snippet="HC",
        language="en",
    )
    session.add(snap)


def test_replay_reconcile_dry_run_plans_indexing(tmp_path, monkeypatch) -> None:
    _init_test_db(tmp_path, monkeypatch)
    job_id = _seed_indexed_job_with_warcs(tmp_path)

    collections_dir = tmp_path / "replay" / "collections"
    lock_file = tmp_path / "replay.lock"

    out = _run_cli(
        [
            "replay-reconcile",
            "--collections-dir",
            str(collections_dir),
            "--warcs-host-root",
            str(tmp_path),
            "--lock-file",
            str(lock_file),
            "--max-jobs",
            "1",
        ]
    )

    assert "Mode:            DRY-RUN" in out
    assert f"WOULD INDEX: job_id={job_id}" in out
    assert "Previews: disabled" in out


def test_replay_reconcile_apply_calls_replay_index_job(tmp_path, monkeypatch) -> None:
    _init_test_db(tmp_path, monkeypatch)
    job_id = _seed_indexed_job_with_warcs(tmp_path)

    calls: list[int] = []

    def fake_replay_index_job(args: argparse.Namespace) -> None:
        calls.append(int(args.id))

    monkeypatch.setattr(cli_module, "cmd_replay_index_job", fake_replay_index_job)

    collections_dir = tmp_path / "replay" / "collections"
    lock_file = tmp_path / "replay.lock"

    out = _run_cli(
        [
            "replay-reconcile",
            "--apply",
            "--collections-dir",
            str(collections_dir),
            "--warcs-host-root",
            str(tmp_path),
            "--lock-file",
            str(lock_file),
            "--max-jobs",
            "1",
        ]
    )

    assert calls == [job_id]
    assert "Replay indexing applied: ok=1 failed=0" in out


def test_replay_reconcile_previews_dry_run_plans_generation(tmp_path, monkeypatch) -> None:
    _init_test_db(tmp_path, monkeypatch)
    job_id = _seed_indexed_job_with_warcs(tmp_path, source_code="hc")

    monkeypatch.setenv("HEALTHARCHIVE_REPLAY_BASE_URL", "https://replay.example")
    monkeypatch.setenv("HEALTHARCHIVE_REPLAY_PREVIEW_DIR", str(tmp_path / "previews"))

    with get_session() as session:
        src = session.query(Source).filter_by(code="hc").one()
        _seed_entry_snapshot(session, job_id=job_id, source=src)
        session.commit()

    collections_dir = tmp_path / "replay" / "collections"
    lock_file = tmp_path / "replay.lock"

    out = _run_cli(
        [
            "replay-reconcile",
            "--collections-dir",
            str(collections_dir),
            "--warcs-host-root",
            str(tmp_path),
            "--lock-file",
            str(lock_file),
            "--previews",
            "--max-previews",
            "1",
        ]
    )

    assert "Preview status" in out
    assert "WOULD GENERATE previews for: hc" in out


def test_replay_reconcile_previews_apply_calls_generator(tmp_path, monkeypatch) -> None:
    _init_test_db(tmp_path, monkeypatch)
    job_id = _seed_indexed_job_with_warcs(tmp_path, source_code="hc")

    monkeypatch.setenv("HEALTHARCHIVE_REPLAY_BASE_URL", "https://replay.example")
    preview_dir = tmp_path / "previews"
    monkeypatch.setenv("HEALTHARCHIVE_REPLAY_PREVIEW_DIR", str(preview_dir))

    with get_session() as session:
        src = session.query(Source).filter_by(code="hc").one()
        _seed_entry_snapshot(session, job_id=job_id, source=src)
        session.commit()

    # Mark replay as ready so previews aren't blocked when --max-jobs=0.
    collections_dir = tmp_path / "replay" / "collections"
    archive_dir = collections_dir / f"job-{job_id}" / "archive"
    indexes_dir = collections_dir / f"job-{job_id}" / "indexes"
    archive_dir.mkdir(parents=True, exist_ok=True)
    indexes_dir.mkdir(parents=True, exist_ok=True)
    (archive_dir / "warc-000001.warc.gz").write_bytes(b"link")
    (indexes_dir / "index.cdxj").write_text("cdx", encoding="utf-8")

    calls: list[list[str]] = []

    def fake_generate_previews(args: argparse.Namespace) -> None:
        calls.append(list(args.source or []))

    monkeypatch.setattr(cli_module, "cmd_replay_generate_previews", fake_generate_previews)

    lock_file = tmp_path / "replay.lock"

    out = _run_cli(
        [
            "replay-reconcile",
            "--apply",
            "--collections-dir",
            str(collections_dir),
            "--warcs-host-root",
            str(tmp_path),
            "--lock-file",
            str(lock_file),
            "--max-jobs",
            "0",
            "--previews",
            "--max-previews",
            "1",
        ]
    )

    assert calls == [["hc"]]
    assert "Replay indexing status" in out
