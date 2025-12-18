from __future__ import annotations

import os
import hashlib
import json
import subprocess
import sys
from io import StringIO
from pathlib import Path

from archive_tool.state import CrawlState
from ha_backend import cli as cli_module
from ha_backend import db as db_module
from ha_backend.db import Base, get_engine, get_session
from ha_backend.models import ArchiveJob, Source


def _init_test_db(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "cli_replay_index.db"
    monkeypatch.setenv("HEALTHARCHIVE_DATABASE_URL", f"sqlite:///{db_path}")

    db_module._engine = None
    db_module._SessionLocal = None

    engine = get_engine()
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)


def _seed_indexed_job_with_warcs(tmp_path: Path) -> tuple[int, list[Path]]:
    """
    Create a Source and an indexed ArchiveJob with a temp dir containing WARCs.
    Returns (job_id, warc_paths).
    """
    output_dir = tmp_path / "job-output"
    output_dir.mkdir(parents=True, exist_ok=True)

    temp_dir = output_dir / ".tmp1234"
    warc_dir = temp_dir / "collections" / "crawl-test" / "archive"
    warc_dir.mkdir(parents=True, exist_ok=True)

    # Ensure discovery sees the temp dir reliably.
    state = CrawlState(output_dir, initial_workers=1)
    state.add_temp_dir(temp_dir)

    warc_a = warc_dir / "a.warc.gz"
    warc_b = warc_dir / "b.warc.gz"
    warc_a.write_bytes(b"fake-warc-a")
    warc_b.write_bytes(b"fake-warc-b")

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

        job = ArchiveJob(
            source_id=src.id,
            name="job-replay",
            output_dir=str(output_dir),
            status="indexed",
        )
        session.add(job)
        session.flush()
        return job.id, [warc_a, warc_b]


def test_replay_index_job_creates_symlinks_and_runs_docker(tmp_path, monkeypatch) -> None:
    _init_test_db(tmp_path, monkeypatch)
    job_id, warcs = _seed_indexed_job_with_warcs(tmp_path)

    collections_dir = tmp_path / "replay" / "collections"
    container_name = "healtharchive-replay-test"

    calls: list[list[str]] = []

    def fake_run(args_list, text=True, capture_output=True):  # type: ignore[no-untyped-def]
        calls.append(list(args_list))
        return subprocess.CompletedProcess(args_list, 0, stdout="", stderr="")

    monkeypatch.setattr(cli_module.subprocess, "run", fake_run)

    parser = cli_module.build_parser()
    args = parser.parse_args(
        [
            "replay-index-job",
            "--id",
            str(job_id),
            "--container",
            container_name,
            "--collections-dir",
            str(collections_dir),
            "--warcs-host-root",
            str(tmp_path),
            "--warcs-container-root",
            "/warcs",
        ]
    )

    stdout = StringIO()
    old_stdout = sys.stdout
    try:
        sys.stdout = stdout
        args.func(args)
    finally:
        sys.stdout = old_stdout

    assert calls == [
        ["docker", "exec", container_name, "wb-manager", "init", f"job-{job_id}"],
        ["docker", "exec", container_name, "wb-manager", "reindex", f"job-{job_id}"],
    ]

    archive_dir = collections_dir / f"job-{job_id}" / "archive"
    link1 = archive_dir / "warc-000001.warc.gz"
    link2 = archive_dir / "warc-000002.warc.gz"
    assert link1.is_symlink()
    assert link2.is_symlink()

    target1 = os.readlink(link1)
    target2 = os.readlink(link2)

    rel_a = warcs[0].resolve().relative_to(tmp_path.resolve())
    rel_b = warcs[1].resolve().relative_to(tmp_path.resolve())
    assert target1 == f"/warcs/{rel_a}"
    assert target2 == f"/warcs/{rel_b}"

    marker_path = collections_dir / f"job-{job_id}" / "replay-index.meta.json"
    assert marker_path.is_file()

    marker = json.loads(marker_path.read_text(encoding="utf-8"))
    assert marker["jobId"] == job_id
    assert marker["collectionName"] == f"job-{job_id}"
    assert marker["warcCount"] == 2
    assert marker["warcsHostRoot"] == str(tmp_path.resolve())
    assert marker["warcsContainerRoot"] == "/warcs"

    rels = sorted([rel_a.as_posix(), rel_b.as_posix()])
    expected_hash = hashlib.sha256("\n".join(rels).encode("utf-8")).hexdigest()
    assert marker["warcListHash"] == expected_hash


def test_replay_index_job_dry_run_does_not_modify_files_or_run_docker(
    tmp_path, monkeypatch
) -> None:
    _init_test_db(tmp_path, monkeypatch)
    job_id, _warcs = _seed_indexed_job_with_warcs(tmp_path)

    collections_dir = tmp_path / "replay" / "collections"

    calls: list[list[str]] = []

    def fake_run(args_list, text=True, capture_output=True):  # type: ignore[no-untyped-def]
        calls.append(list(args_list))
        return subprocess.CompletedProcess(args_list, 0, stdout="", stderr="")

    monkeypatch.setattr(cli_module.subprocess, "run", fake_run)

    parser = cli_module.build_parser()
    args = parser.parse_args(
        [
            "replay-index-job",
            "--id",
            str(job_id),
            "--collections-dir",
            str(collections_dir),
            "--warcs-host-root",
            str(tmp_path),
            "--dry-run",
        ]
    )

    args.func(args)

    assert calls == []
    assert not (collections_dir / f"job-{job_id}").exists()
