from __future__ import annotations

import argparse
import re
import subprocess  # nosec: B404 - controlled CLI invocation of external tool
import sys
from pathlib import Path
from typing import Sequence

from sqlalchemy import text

from .config import (
    REPO_ROOT,
    get_archive_tool_config,
    get_database_config,
    get_replay_base_url,
    get_replay_preview_dir,
)
from .db import get_engine, get_session
from .indexing import index_job
from .job_registry import create_job_for_source
from .jobs import create_job, run_persistent_job
from .logging_config import configure_logging
from .seeds import seed_sources
from .worker import run_worker_loop

# === Command implementations ===


def cmd_check_env(args: argparse.Namespace) -> None:
    cfg = get_archive_tool_config()

    print("HealthArchive Backend – Environment Check")
    print("-----------------------------------------")
    print(f"Archive root:     {cfg.archive_root}")
    print(f"Archive tool cmd: {cfg.archive_tool_cmd}")
    print("")

    try:
        cfg.ensure_archive_root()
    except Exception as exc:  # broad, just for a simple health check
        print(f"ERROR: Failed to ensure archive root: {exc}")
        sys.exit(1)
    else:
        print("Archive root exists and is (likely) writable.")


def cmd_check_archive_tool(args: argparse.Namespace) -> None:
    cfg = get_archive_tool_config()

    print("Running 'archive-tool --help' to verify archive_tool...")
    print("-----------------------------------------")

    # We intentionally call the configured command; default is 'archive-tool'.
    # Command and arguments are controlled by configuration, not end-user input.
    result = subprocess.run(  # nosec: B603 - subprocess is used for a CLI helper
        [cfg.archive_tool_cmd, "--help"],
        capture_output=True,
        text=True,
    )

    print("--- STDOUT ---")
    print(result.stdout)
    print("--- STDERR ---")
    print(result.stderr)
    print(f"\nExit code: {result.returncode}")

    if result.returncode != 0:
        sys.exit(result.returncode)


def cmd_check_db(args: argparse.Namespace) -> None:
    """
    Simple connectivity check for the configured database.
    """
    db_cfg = get_database_config()
    print("HealthArchive Backend – Database Check")
    print("--------------------------------------")
    print(f"Database URL: {db_cfg.database_url}")

    try:
        engine = get_engine()
        with get_session() as session:
            # Issue a trivial query to ensure the connection is usable.
            session.execute(text("SELECT 1"))
    except Exception as exc:  # broad by design for a health check
        print(f"ERROR: Failed to connect to database: {exc}")
        sys.exit(1)
    else:
        print("Database connection OK.")


def cmd_run_job(args: argparse.Namespace) -> None:
    """
    Run a single archive_tool job immediately.

    Example:

        ha-backend run-job \\
          --name restoredcdc-2025-12-09 \\
          --seeds https://www.cdc.gov \\
          --initial-workers 2 \\
          --log-level INFO \\
          --cleanup --overwrite \\
          -- --workers 4 --some-zimit-arg foo
    """
    if not args.seeds:
        print("ERROR: At least one --seeds URL is required.", file=sys.stderr)
        sys.exit(1)

    job = create_job(name=args.name, seeds=args.seeds)

    extra_args: Sequence[str] | None = args.passthrough
    if extra_args and extra_args[0] == "--":
        # If argparse left a leading '--' in the passthrough, strip it.
        extra_args = extra_args[1:]

    rc = job.run(
        initial_workers=args.initial_workers,
        cleanup=args.cleanup,
        overwrite=args.overwrite,
        log_level=args.log_level,
        extra_args=extra_args,
        stream_output=True,
    )

    if rc != 0:
        sys.exit(rc)


def cmd_create_job(args: argparse.Namespace) -> None:
    """
    Create a persistent ArchiveJob for a given source using the job registry.
    """
    source_code = args.source

    from .models import \
        ArchiveJob as ORMArchiveJob  # local import to avoid cycles

    # Build any per-job Zimit passthrough args for dev/testing (page limit,
    # crawl depth, etc.).
    extra_zimit_args: list[str] = []
    page_limit = getattr(args, "page_limit", None)
    if page_limit is not None:
        extra_zimit_args.extend(["--pageLimit", str(page_limit)])

    depth = getattr(args, "depth", None)
    if depth is not None:
        extra_zimit_args.extend(["--depth", str(depth)])

    with get_session() as session:
        job_row: ORMArchiveJob = create_job_for_source(
            source_code,
            session=session,
            extra_zimit_args=extra_zimit_args or None,
        )

        # Capture fields before the session is closed to avoid accessing
        # a detached instance after commit.
        job_id = job_row.id
        job_name = job_row.name
        job_output_dir = job_row.output_dir

    print("Created job:")
    print(f"  ID:         {job_id}")
    print(f"  Source:     {source_code}")
    print(f"  Name:       {job_name}")
    print(f"  Output dir: {job_output_dir}")


def cmd_run_db_job(args: argparse.Namespace) -> None:
    """
    Run a database-backed ArchiveJob by ID.
    """
    job_id = args.id
    try:
        rc = run_persistent_job(job_id)
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)

    if rc != 0:
        sys.exit(rc)


def cmd_index_job(args: argparse.Namespace) -> None:
    """
    Index a completed ArchiveJob into Snapshot rows.
    """
    job_id = args.id
    try:
        rc = index_job(job_id)
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)

    if rc != 0:
        sys.exit(rc)


def cmd_seed_sources(args: argparse.Namespace) -> None:
    """
    Insert initial Source rows (hc, phac) if they are missing.
    """
    created_count = 0
    with get_session() as session:
        created_count = seed_sources(session)

    print(f"Seeded {created_count} source(s).")


def cmd_backfill_search_vector(args: argparse.Namespace) -> None:
    """
    Backfill Snapshot.search_vector for Postgres FTS.

    This is safe to run multiple times. By default it only fills rows where
    search_vector is NULL.
    """
    from sqlalchemy import update

    from .models import Snapshot
    from .search import build_search_vector

    batch_size: int = args.batch_size
    start_id: int = args.start_id
    job_id: int | None = args.job_id
    force: bool = args.force

    with get_session() as session:
        dialect_name = session.get_bind().dialect.name
        if dialect_name != "postgresql":
            print(
                f"Database dialect is {dialect_name!r}; Postgres FTS backfill is skipped."
            )
            return

        last_id = start_id
        total_updated = 0

        while True:
            batch_filters = [Snapshot.id > last_id]
            if job_id is not None:
                batch_filters.append(Snapshot.job_id == job_id)
            if not force:
                batch_filters.append(Snapshot.search_vector.is_(None))

            ids = (
                session.query(Snapshot.id)
                .filter(*batch_filters)
                .order_by(Snapshot.id)
                .limit(batch_size)
                .all()
            )

            if not ids:
                break

            max_id = ids[-1][0]
            update_filters = [Snapshot.id > last_id, Snapshot.id <= max_id]
            if job_id is not None:
                update_filters.append(Snapshot.job_id == job_id)
            if not force:
                update_filters.append(Snapshot.search_vector.is_(None))

            stmt = (
                update(Snapshot)
                .where(*update_filters)
                .values(
                    search_vector=build_search_vector(
                        Snapshot.title, Snapshot.snippet, Snapshot.url
                    )
                )
            )
            result = session.execute(stmt)
            session.commit()

            batch_updated = int(result.rowcount or 0)
            total_updated += batch_updated
            print(
                f"Backfilled search_vector for ids ({last_id}, {max_id}] "
                f"({batch_updated} rows; total {total_updated})."
            )
            last_id = max_id

    print(f"Done. Total rows updated: {total_updated}")


def cmd_refresh_snapshot_metadata(args: argparse.Namespace) -> None:
    """
    Refresh title/snippet/language for snapshots of a job by re-reading WARCs.

    This updates rows in place (snapshot IDs remain stable).
    """
    from pathlib import Path

    from sqlalchemy import update

    from .indexing.text_extraction import (
        detect_language,
        extract_text,
        extract_title,
        make_snippet,
    )
    from .indexing.warc_reader import iter_html_records
    from .models import ArchiveJob as ORMArchiveJob
    from .models import Snapshot
    from .search import build_search_vector

    job_id: int = args.job_id
    batch_size: int = args.batch_size
    dry_run: bool = args.dry_run
    limit: int | None = args.limit

    with get_session() as session:
        job = session.get(ORMArchiveJob, job_id)
        if job is None:
            print(f"ERROR: Job {job_id} not found.", file=sys.stderr)
            sys.exit(1)

        dialect_name = session.get_bind().dialect.name
        use_postgres_fts = dialect_name == "postgresql"

        rows = (
            session.query(
                Snapshot.id,
                Snapshot.warc_path,
                Snapshot.warc_record_id,
                Snapshot.url,
            )
            .filter(Snapshot.job_id == job_id)
            .order_by(Snapshot.id)
            .all()
        )
        if not rows:
            print(f"No snapshots found for job {job_id}.")
            return

        by_record_id: dict[tuple[str, str], int] = {}
        by_url: dict[tuple[str, str], list[int]] = {}
        warc_paths: set[str] = set()

        for snap_id, warc_path, warc_record_id, url in rows:
            warc_paths.add(warc_path)
            if warc_record_id:
                by_record_id[(warc_path, warc_record_id)] = snap_id
            else:
                by_url.setdefault((warc_path, url), []).append(snap_id)

        updates: list[dict[str, object]] = []
        updated_count = 0
        processed_records = 0

        warc_paths_sorted = sorted(warc_paths)
        print(f"Refreshing snapshot metadata for job {job_id} ({len(warc_paths_sorted)} WARC(s))…")

        for warc_path_str in warc_paths_sorted:
            warc_path = Path(warc_path_str)
            if not warc_path.is_file():
                print(f"WARNING: WARC not found: {warc_path}", file=sys.stderr)
                continue

            for rec in iter_html_records(warc_path):
                processed_records += 1
                if limit is not None and processed_records > limit:
                    break

                target_id = None
                if rec.warc_record_id is not None:
                    target_id = by_record_id.get((warc_path_str, rec.warc_record_id))

                target_ids: list[int] = []
                if target_id is not None:
                    target_ids = [target_id]
                else:
                    target_ids = by_url.get((warc_path_str, rec.url), [])

                if not target_ids:
                    continue

                html = rec.body_bytes.decode("utf-8", errors="replace")
                title = extract_title(html)
                text = extract_text(html)
                snippet = make_snippet(text)
                language = detect_language(text, rec.headers)

                for sid in target_ids:
                    updates.append(
                        {
                            "id": sid,
                            "title": title,
                            "snippet": snippet,
                            "language": language,
                        }
                    )

                if len(updates) >= batch_size:
                    session.bulk_update_mappings(Snapshot, updates)
                    if not dry_run:
                        session.commit()
                    updated_count += len(updates)
                    updates.clear()

            if limit is not None and processed_records > limit:
                break

        if updates:
            session.bulk_update_mappings(Snapshot, updates)
            if not dry_run:
                session.commit()
            updated_count += len(updates)
            updates.clear()

        if use_postgres_fts and not dry_run:
            session.execute(
                update(Snapshot)
                .where(Snapshot.job_id == job_id)
                .values(
                    search_vector=build_search_vector(
                        Snapshot.title, Snapshot.snippet, Snapshot.url
                    )
                )
            )
            session.commit()

        if dry_run:
            session.rollback()
            print("Dry run complete (rolled back changes).")
        else:
            print(f"Updated {updated_count} snapshot(s).")


def cmd_backfill_outlinks(args: argparse.Namespace) -> None:
    """
    Backfill SnapshotOutlink rows for snapshots by re-reading WARCs.

    This is intended for production backfills after deploying the authority
    schema, and can also be used locally for debugging.
    """
    from pathlib import Path

    from sqlalchemy import inspect

    from .authority import recompute_page_signals
    from .indexing.mapping import normalize_url_for_grouping
    from .indexing.text_extraction import extract_outlink_groups
    from .indexing.warc_reader import iter_html_records
    from .models import ArchiveJob as ORMArchiveJob
    from .models import Snapshot, SnapshotOutlink

    job_id: int = args.job_id
    batch_size: int = args.batch_size
    max_links_per_snapshot: int = args.max_links_per_snapshot
    dry_run: bool = args.dry_run
    limit: int | None = args.limit
    update_signals: bool = args.update_signals

    with get_session() as session:
        inspector = inspect(session.get_bind())
        if not inspector.has_table("snapshot_outlinks"):
            print(
                "ERROR: snapshot_outlinks table not found; run 'alembic upgrade head' first.",
                file=sys.stderr,
            )
            sys.exit(1)

        has_page_signals = inspector.has_table("page_signals")
        if update_signals and not has_page_signals:
            print(
                "WARNING: page_signals table not found; PageSignal updates will be skipped.",
                file=sys.stderr,
            )
            update_signals = False

        job = session.get(ORMArchiveJob, job_id)
        if job is None:
            print(f"ERROR: Job {job_id} not found.", file=sys.stderr)
            sys.exit(1)

        rows = (
            session.query(
                Snapshot.id,
                Snapshot.warc_path,
                Snapshot.warc_record_id,
                Snapshot.url,
            )
            .filter(Snapshot.job_id == job_id)
            .order_by(Snapshot.id)
            .all()
        )
        if not rows:
            print(f"No snapshots found for job {job_id}.")
            return

        impacted_groups: set[str] = set()
        existing_groups = (
            session.query(SnapshotOutlink.to_normalized_url_group)
            .join(Snapshot, Snapshot.id == SnapshotOutlink.snapshot_id)
            .filter(Snapshot.job_id == job_id)
            .distinct()
            .all()
        )
        impacted_groups.update({g for (g,) in existing_groups if g})

        snapshot_ids_subq = session.query(Snapshot.id).filter(Snapshot.job_id == job_id)
        deleted = (
            session.query(SnapshotOutlink)
            .filter(SnapshotOutlink.snapshot_id.in_(snapshot_ids_subq))
            .delete(synchronize_session=False)
        )
        if deleted:
            print(f"Deleted {int(deleted)} existing outlink row(s) for job {job_id}.")

        by_record_id: dict[tuple[str, str], list[int]] = {}
        by_url: dict[tuple[str, str], list[int]] = {}
        warc_paths: set[str] = set()

        for snap_id, warc_path, warc_record_id, url in rows:
            warc_paths.add(warc_path)
            if warc_record_id:
                by_record_id.setdefault((warc_path, warc_record_id), []).append(snap_id)
            else:
                by_url.setdefault((warc_path, url), []).append(snap_id)

        warc_paths_sorted = sorted(warc_paths)
        print(
            f"Backfilling outlinks for job {job_id} ({len(rows)} snapshot(s), {len(warc_paths_sorted)} WARC(s))…"
        )

        pending: list[dict[str, object]] = []
        inserted_rows = 0
        processed_records = 0

        for warc_path_str in warc_paths_sorted:
            warc_path = Path(warc_path_str)
            if not warc_path.is_file():
                print(f"WARNING: WARC not found: {warc_path}", file=sys.stderr)
                continue

            for rec in iter_html_records(warc_path):
                processed_records += 1
                if limit is not None and processed_records > limit:
                    break

                if rec.status_code is None or not (200 <= rec.status_code < 300):
                    continue

                target_ids: list[int] = []
                if rec.warc_record_id:
                    target_ids = by_record_id.get((warc_path_str, rec.warc_record_id), [])
                if not target_ids:
                    target_ids = by_url.get((warc_path_str, rec.url), [])
                if not target_ids:
                    continue

                html = rec.body_bytes.decode("utf-8", errors="replace")
                from_group = normalize_url_for_grouping(rec.url)
                impacted_groups.add(from_group)
                outlink_groups = extract_outlink_groups(
                    html,
                    base_url=rec.url,
                    from_group=from_group,
                    max_links=max_links_per_snapshot,
                )
                if not outlink_groups:
                    continue

                impacted_groups.update(outlink_groups)

                for snap_id in target_ids:
                    for group in outlink_groups:
                        pending.append(
                            {
                                "snapshot_id": snap_id,
                                "to_normalized_url_group": group,
                            }
                        )

                if len(pending) >= batch_size:
                    session.bulk_insert_mappings(SnapshotOutlink, pending)
                    if not dry_run:
                        session.flush()
                    inserted_rows += len(pending)
                    pending.clear()

            if limit is not None and processed_records > limit:
                break

        if pending:
            session.bulk_insert_mappings(SnapshotOutlink, pending)
            if not dry_run:
                session.flush()
            inserted_rows += len(pending)
            pending.clear()

        print(f"Inserted {inserted_rows} outlink row(s) for job {job_id}.")

        if update_signals and impacted_groups:
            recompute_page_signals(session, groups=tuple(impacted_groups))

        if dry_run:
            session.rollback()
            print("Dry run complete (rolled back changes).")


def cmd_recompute_page_signals(args: argparse.Namespace) -> None:
    """
    Rebuild PageSignal rows from SnapshotOutlink edges.
    """
    from sqlalchemy import inspect

    from .authority import recompute_page_signals

    dry_run: bool = args.dry_run

    with get_session() as session:
        inspector = inspect(session.get_bind())
        if not inspector.has_table("snapshot_outlinks") or not inspector.has_table(
            "page_signals"
        ):
            print(
                "ERROR: authority tables not found; run 'alembic upgrade head' first.",
                file=sys.stderr,
            )
            sys.exit(1)

        updated = recompute_page_signals(session, groups=None)
        print(f"Recomputed page signals ({updated} change(s)).")

        if dry_run:
            session.rollback()
            print("Dry run complete (rolled back changes).")


def cmd_list_jobs(args: argparse.Namespace) -> None:
    """
    List recent ArchiveJob rows with optional filters.
    """
    from .models import ArchiveJob as ORMArchiveJob
    from .models import Source

    rows_data = []
    with get_session() as session:
        query = session.query(ORMArchiveJob).join(Source)

        if args.source:
            query = query.filter(Source.code == args.source.lower())

        if args.status:
            query = query.filter(ORMArchiveJob.status.in_(args.status))

        query = query.order_by(ORMArchiveJob.created_at.desc()).limit(args.limit)

        for job in query.all():
            src_code = job.source.code if job.source else "?"
            rows_data.append(
                (
                    job.id,
                    src_code,
                    job.status,
                    job.retry_count,
                    job.created_at,
                    job.started_at,
                    job.finished_at,
                    job.indexed_page_count,
                    job.name,
                )
            )

    if not rows_data:
        print("No jobs found.")
        return

    print(
        "ID  Source  Status       Retries  Created_at           Started_at           Finished_at          Indexed"
    )
    for (
        job_id,
        src,
        status,
        retry_count,
        created_at,
        started_at,
        finished_at,
        indexed_page_count,
        name,
    ) in rows_data:
        print(
            f"{job_id:<3} {src:<6} {status:<11} {retry_count:<7} "
            f"{str(created_at)[:19]:<19} {str(started_at)[:19]:<19} "
            f"{str(finished_at)[:19]:<19} {indexed_page_count} {name}"
        )


def cmd_show_job(args: argparse.Namespace) -> None:
    """
    Show detailed information about a single job.
    """
    from .models import ArchiveJob as ORMArchiveJob

    with get_session() as session:
        job = session.get(ORMArchiveJob, args.id)
        if job is None:
            print(f"ERROR: Job {args.id} not found.", file=sys.stderr)
            sys.exit(1)

        job_id = job.id
        name = job.name
        status = job.status
        retry_count = job.retry_count
        created_at = job.created_at
        queued_at = job.queued_at
        started_at = job.started_at
        finished_at = job.finished_at
        output_dir = job.output_dir
        crawler_exit_code = job.crawler_exit_code
        crawler_status = job.crawler_status
        warc_file_count = job.warc_file_count
        indexed_page_count = job.indexed_page_count

        source_code = job.source.code if job.source else "?"
        source_name = job.source.name if job.source else "?"

        config = job.config or {}
        seeds = config.get("seeds") or []
        tool_opts = config.get("tool_options") or {}
        zimit_args = config.get("zimit_passthrough_args") or []

    print(f"ID:              {job_id}")
    print(f"Source:          {source_code} ({source_name})")
    print(f"Name:            {name}")
    print(f"Status:          {status}")
    print(f"Retry count:     {retry_count}")
    print(f"Created at:      {created_at}")
    print(f"Queued at:       {queued_at}")
    print(f"Started at:      {started_at}")
    print(f"Finished at:     {finished_at}")
    print(f"Output dir:      {output_dir}")
    print(f"Crawler RC:      {crawler_exit_code}")
    print(f"Crawler status:  {crawler_status}")
    print(f"WARC files:      {warc_file_count}")
    print(f"Indexed pages:   {indexed_page_count}")
    print("")
    print("Config:")
    print(f"  Seeds:               {', '.join(seeds) if seeds else '(none)'}")
    print(f"  Tool options:        {tool_opts}")
    print(f"  Zimit passthrough:   {zimit_args}")


def cmd_retry_job(args: argparse.Namespace) -> None:
    """
    Mark a failed job as retryable for crawl or re-indexing as appropriate.
    """
    from .models import ArchiveJob as ORMArchiveJob

    with get_session() as session:
        job = session.get(ORMArchiveJob, args.id)

        if job is None:
            print(f"ERROR: Job {args.id} not found.", file=sys.stderr)
            sys.exit(1)

        if job.status == "failed":
            job.status = "retryable"
            print(f"Job {job.id} marked as retryable for crawl.")
        elif job.status == "index_failed":
            job.status = "completed"
            print(f"Job {job.id} marked for re-indexing (status set to completed).")
        else:
            print(
                f"Job {job.id} is in status {job.status!r}; nothing to retry.",
                file=sys.stderr,
            )


def cmd_validate_job_config(args: argparse.Namespace) -> None:
    """
    Validate a job's configuration by running archive_tool in dry-run mode.

    This does not change the job's status or timestamps; it simply exercises
    the CLI argument construction and lets archive_tool validate and print a
    configuration summary.
    """
    from pathlib import Path

    from .archive_contract import ArchiveJobConfig
    from .jobs import RuntimeArchiveJob, _build_tool_extra_args
    from .models import ArchiveJob as ORMArchiveJob

    job_id = args.id

    with get_session() as session:
        job = session.get(ORMArchiveJob, job_id)
        if job is None:
            print(f"ERROR: Job {job_id} not found.", file=sys.stderr)
            sys.exit(1)

        raw_config = job.config or {}
        job_cfg = ArchiveJobConfig.from_dict(raw_config)
        seeds = list(job_cfg.seeds)
        if not seeds:
            print(
                f"ERROR: Job {job_id} has no seeds configured; cannot validate.",
                file=sys.stderr,
            )
            sys.exit(1)

        tool_options = job_cfg.tool_options
        zimit_args = list(job_cfg.zimit_passthrough_args)
        output_dir = Path(job.output_dir)
        job_name = job.name

    runtime_job = RuntimeArchiveJob(name=job_name, seeds=seeds)

    initial_workers = int(tool_options.initial_workers)
    cleanup = bool(tool_options.cleanup)
    overwrite = bool(tool_options.overwrite)
    log_level = str(tool_options.log_level)

    extra_tool_args: list[str] = _build_tool_extra_args(tool_options)
    # Prepend --dry-run so archive_tool validates config without running Docker.
    full_extra_args: list[str] = ["--dry-run"]
    full_extra_args.extend(extra_tool_args)
    if zimit_args:
        full_extra_args.extend(zimit_args)

    print("HealthArchive Backend – Validate Job Config")
    print("------------------------------------------")
    print(f"Job ID:      {job_id}")
    print(f"Job name:    {job_name}")
    print(f"Output dir:  {output_dir}")
    print(f"Seeds:       {', '.join(seeds)}")
    print("")

    rc = runtime_job.run(
        initial_workers=initial_workers,
        cleanup=cleanup,
        overwrite=overwrite,
        log_level=log_level,
        extra_args=full_extra_args,
        stream_output=True,
        output_dir_override=output_dir,
    )

    if rc != 0:
        sys.exit(rc)


def cmd_cleanup_job(args: argparse.Namespace) -> None:
    """
    Cleanup temporary directories and state for a completed/indexed job.

    Currently only 'temp' mode is supported, which removes archive_tool's
    temp dirs (including WARCs) and the state file, but leaves the job
    output directory and any final ZIM in place. The underlying helpers
    (CrawlState, cleanup_temp_dirs) live in the in-repo ``archive_tool``
    package and should be kept in sync with this command.

    Safety: When HEALTHARCHIVE_REPLAY_BASE_URL is set (replay is enabled),
    this command refuses to run in 'temp' mode unless --force is provided,
    because deleting temp dirs also deletes WARCs required for replay.
    """
    from datetime import datetime, timezone
    from pathlib import Path

    from archive_tool.state import CrawlState
    from archive_tool.utils import (cleanup_temp_dirs,
                                    find_latest_temp_dir_fallback)

    from .models import ArchiveJob as ORMArchiveJob

    job_id = args.id
    mode = args.mode

    if mode != "temp":
        print(
            f"Unsupported cleanup mode {mode!r}; only 'temp' is currently supported.",
            file=sys.stderr,
        )
        sys.exit(1)

    if get_replay_base_url() and not args.force:
        print(
            "Refusing to run cleanup-job --mode temp because replay is enabled "
            "(HEALTHARCHIVE_REPLAY_BASE_URL is set). This mode deletes WARCs "
            "needed for replay. Re-run with --force to override.",
            file=sys.stderr,
        )
        sys.exit(1)

    with get_session() as session:
        job = session.get(ORMArchiveJob, job_id)
        if job is None:
            print(f"ERROR: Job {job_id} not found.", file=sys.stderr)
            sys.exit(1)

        if job.status not in ("indexed", "index_failed"):
            print(
                f"Job {job.id} is in status {job.status!r}; cleanup is only "
                "allowed for jobs in status 'indexed' or 'index_failed'.",
                file=sys.stderr,
            )
            sys.exit(1)

        output_dir = Path(job.output_dir)
        if not output_dir.is_dir():
            print(
                f"ERROR: Output directory {output_dir} does not exist or is not a directory.",
                file=sys.stderr,
            )
            sys.exit(1)

        # Discover temp dirs via CrawlState and archive_tool utils.
        state = CrawlState(output_dir, initial_workers=1)
        temp_dirs = state.get_temp_dir_paths()
        if not temp_dirs:
            latest = find_latest_temp_dir_fallback(output_dir)
            if latest is not None:
                temp_dirs = [latest]

        had_state_file = state.state_file_path.exists()

        if not temp_dirs and not had_state_file:
            print(
                f"No temp dirs or state file discovered for job {job.id}; "
                "nothing to cleanup.",
                file=sys.stderr,
            )
            return

        cleanup_temp_dirs(temp_dirs, state.state_file_path)

        job.cleanup_status = "temp_cleaned"
        job.cleaned_at = datetime.now(timezone.utc)
        job.state_file_path = None


def cmd_replay_index_job(args: argparse.Namespace) -> None:
    """
    Create or refresh a pywb replay collection for an existing ArchiveJob.

    This command:
    - discovers WARCs for the job using the same discovery logic as indexing
    - creates stable symlinks under the pywb collection's archive directory
    - runs `wb-manager reindex` inside the configured replay container

    It is designed for the production deployment described in
    `docs/deployment/replay-service-pywb.md`.
    """
    from pathlib import Path

    from .indexing.warc_discovery import discover_warcs_for_job
    from .models import ArchiveJob as ORMArchiveJob

    job_id = args.id
    dry_run = args.dry_run

    container_name = args.container
    collection_name = args.collection or f"job-{job_id}"

    collections_dir = Path(args.collections_dir).expanduser()
    warcs_host_root = Path(args.warcs_host_root).expanduser()

    warcs_container_root = args.warcs_container_root.strip()
    if not warcs_container_root:
        warcs_container_root = "/warcs"
    warcs_container_root = warcs_container_root.rstrip("/")
    if not warcs_container_root.startswith("/"):
        warcs_container_root = f"/{warcs_container_root}"

    limit_warcs = args.limit_warcs
    if limit_warcs is not None and limit_warcs < 1:
        print("ERROR: --limit-warcs must be >= 1.", file=sys.stderr)
        sys.exit(1)

    with get_session() as session:
        job = session.get(ORMArchiveJob, job_id)
        if job is None:
            print(f"ERROR: Job {job_id} not found.", file=sys.stderr)
            sys.exit(1)

        output_dir = Path(job.output_dir).resolve()
        if not output_dir.is_dir():
            print(
                f"ERROR: Output directory {output_dir} does not exist or is not a directory.",
                file=sys.stderr,
            )
            sys.exit(1)

        warc_paths = discover_warcs_for_job(job)

    if not warc_paths:
        print(
            f"ERROR: No WARCs discovered for job {job_id}. "
            "Ensure the job output dir contains a .tmp*/collections/crawl-*/archive layout.",
            file=sys.stderr,
        )
        sys.exit(1)

    if limit_warcs is not None:
        warc_paths = warc_paths[:limit_warcs]

    collection_root = collections_dir / collection_name
    archive_dir = collection_root / "archive"
    indexes_dir = collection_root / "indexes"

    def run_docker(args_list: list[str]) -> None:
        result = subprocess.run(  # nosec: B603 - operator-controlled CLI invocation
            args_list,
            text=True,
            capture_output=True,
        )
        if result.returncode != 0:
            if result.stdout:
                print(result.stdout)
            if result.stderr:
                print(result.stderr, file=sys.stderr)
            sys.exit(result.returncode)

    print("Replay indexing – plan")
    print("----------------------")
    print(f"Job ID:           {job_id}")
    print(f"Collection:       {collection_name}")
    print(f"Discovered WARCs: {len(warc_paths)}")
    print(f"Collections dir:  {collections_dir}")
    print(f"Archive dir:      {archive_dir}")
    print(f"Container:        {container_name}")
    print(f"WARCs host root:  {warcs_host_root}")
    print(f"WARCs in container: {warcs_container_root}")
    print("")

    if dry_run:
        print("Dry run: no filesystem changes and no docker commands will run.")
        print("")

    if not dry_run:
        collections_dir.mkdir(parents=True, exist_ok=True)

        if not collection_root.exists():
            print(f"Initializing collection via wb-manager: {collection_name}")
            run_docker(
                ["docker", "exec", container_name, "wb-manager", "init", collection_name]
            )

        archive_dir.mkdir(parents=True, exist_ok=True)
        indexes_dir.mkdir(parents=True, exist_ok=True)

    # Remove existing stable WARC links for idempotency.
    existing_links = sorted(archive_dir.glob("warc-*"))
    if existing_links:
        print(
            f"Removing {len(existing_links)} existing WARC link(s) from {archive_dir}"
        )
    for path in existing_links:
        if dry_run:
            print(f"  would remove {path}")
            continue
        if path.is_dir():
            print(
                f"ERROR: Unexpected directory in archive dir: {path}",
                file=sys.stderr,
            )
            sys.exit(1)
        path.unlink()

    host_root_resolved = warcs_host_root.resolve()
    if not host_root_resolved.is_dir():
        print(
            f"ERROR: WARCs host root {host_root_resolved} does not exist or is not a directory.",
            file=sys.stderr,
        )
        sys.exit(1)

    print(f"Linking {len(warc_paths)} WARC(s) into {archive_dir}")
    for idx, host_warc_path in enumerate(warc_paths, start=1):
        suffix = "".join(host_warc_path.suffixes) or host_warc_path.suffix
        link_name = f"warc-{idx:06d}{suffix}"

        resolved_warc = host_warc_path.resolve()
        try:
            rel = resolved_warc.relative_to(host_root_resolved)
        except ValueError:
            print(
                f"ERROR: WARC path {resolved_warc} is not under host root {host_root_resolved}. "
                "Use --warcs-host-root/--warcs-container-root to configure path translation.",
                file=sys.stderr,
            )
            sys.exit(1)

        target_in_container = str(Path(warcs_container_root) / rel)
        link_path = archive_dir / link_name

        if dry_run:
            print(f"  would link {link_path} -> {target_in_container}")
            continue

        link_path.symlink_to(target_in_container)

    if dry_run:
        print("")
        print(
            f"Would run: docker exec {container_name} wb-manager reindex {collection_name}"
        )
        return

    print("Rebuilding pywb CDX index (wb-manager reindex)...")
    run_docker(
        ["docker", "exec", container_name, "wb-manager", "reindex", collection_name]
    )
    print("Replay indexing complete.")


def cmd_replay_generate_previews(args: argparse.Namespace) -> None:
    """
    Generate cached replay preview images for source entry pages.

    Previews are stored on disk under HEALTHARCHIVE_REPLAY_PREVIEW_DIR and
    served by the public API at:

      /api/sources/{source_code}/preview?jobId=<id>

    This command uses a Playwright Docker image to render each source's
    `entryBrowseUrl` and take a small screenshot (JPEG by default). The URL is
    loaded with `#ha_nobanner=1` so the pywb banner is not captured.
    """
    import shlex
    from urllib.parse import urlsplit, urlunsplit

    from .api.routes_public import list_sources

    preview_dir = get_replay_preview_dir()
    if preview_dir is None:
        print(
            "ERROR: HEALTHARCHIVE_REPLAY_PREVIEW_DIR is not set. "
            "Configure a preview directory before generating previews.",
            file=sys.stderr,
        )
        sys.exit(1)

    preview_dir = preview_dir.expanduser().resolve()

    script_path = (REPO_ROOT / "scripts" / "generate_replay_preview.js").resolve()
    if not script_path.is_file():
        print(
            f"ERROR: Missing preview generator script at {script_path}",
            file=sys.stderr,
        )
        sys.exit(1)

    if preview_dir.exists() and not preview_dir.is_dir():
        print(
            f"ERROR: Preview path {preview_dir} exists but is not a directory.",
            file=sys.stderr,
        )
        sys.exit(1)

    if not preview_dir.exists():
        if args.dry_run:
            print(f"Would create preview directory: {preview_dir}")
        else:
            preview_dir.mkdir(parents=True, exist_ok=True)

    requested_sources: list[str] = []
    if args.source:
        requested_sources = [code.strip().lower() for code in args.source if code.strip()]

    with get_session() as session:
        sources = list_sources(db=session)

    if requested_sources:
        wanted = set(requested_sources)
        sources = [source for source in sources if source.sourceCode in wanted]
        found = {source.sourceCode for source in sources}
        missing = sorted(wanted - found)
        if missing:
            print(
                "WARNING: Requested source code(s) not found (or excluded from public API): "
                + ", ".join(missing),
                file=sys.stderr,
            )

    if not sources:
        print("No sources selected; nothing to generate.")
        return

    image = args.playwright_image
    width = args.width
    height = args.height
    timeout_ms = args.timeout_ms
    settle_ms = args.settle_ms
    output_format = args.format
    jpeg_quality = args.jpeg_quality

    def parse_playwright_npm_version(image_name: str) -> str:
        """
        Best-effort extraction of the Playwright npm version from the Docker image tag.

        Example: mcr.microsoft.com/playwright:v1.50.1-jammy -> 1.50.1
        """
        match = re.search(r":v(\d+\.\d+\.\d+)(?:-|$)", image_name)
        if match:
            return match.group(1)
        return "1.50.1"

    playwright_npm_version = parse_playwright_npm_version(image)

    # Cache Playwright's node module install across runs so we don't re-download
    # it for every screenshot container invocation.
    node_cache_dir = (preview_dir.parent / ".preview-node").resolve()
    if not node_cache_dir.exists():
        if args.dry_run:
            print(f"Would create node cache directory: {node_cache_dir}")
        else:
            node_cache_dir.mkdir(parents=True, exist_ok=True)

    if width < 200 or height < 200:
        print("ERROR: --width/--height must be >= 200.", file=sys.stderr)
        sys.exit(1)

    format_normalized = (output_format or "").strip().lower()
    if format_normalized not in {"jpeg", "jpg", "png"}:
        print("ERROR: --format must be 'jpeg' or 'png'.", file=sys.stderr)
        sys.exit(1)
    if format_normalized == "jpg":
        format_normalized = "jpeg"

    if jpeg_quality < 20 or jpeg_quality > 95:
        print("ERROR: --jpeg-quality must be between 20 and 95.", file=sys.stderr)
        sys.exit(1)

    def should_use_host_network(url: str) -> bool:
        if args.network == "host":
            return True
        if args.network == "bridge":
            return False
        parts = urlsplit(url)
        host = (parts.hostname or "").lower()
        return host in {"127.0.0.1", "localhost"}

    failures: list[str] = []
    generated = 0
    skipped = 0

    print("Replay preview generation")
    print("------------------------")
    print(f"Preview dir:      {preview_dir}")
    print(f"Playwright image: {image}")
    print(f"Viewport:         {width}x{height}")
    print(f"Timeout:          {timeout_ms}ms")
    print(f"Format:           {format_normalized}")
    print(f"Playwright npm:   {playwright_npm_version} (cached under {node_cache_dir.name}/)")
    print("")

    supported_exts = (".webp", ".jpg", ".jpeg", ".png")

    for source in sources:
        browse_url = source.entryBrowseUrl
        if not browse_url:
            skipped += 1
            print(f"- {source.sourceCode}: skipping (no entryBrowseUrl)")
            continue

        match = re.search(r"/job-(\d+)(?:/|$)", browse_url)
        if not match:
            skipped += 1
            print(
                f"- {source.sourceCode}: skipping (could not parse job id from entryBrowseUrl)",
                file=sys.stderr,
            )
            continue

        job_id = int(match.group(1))
        base_name = f"source-{source.sourceCode}-job-{job_id}"
        existing = next(
            (
                preview_dir / f"{base_name}{ext}"
                for ext in supported_exts
                if (preview_dir / f"{base_name}{ext}").exists()
            ),
            None,
        )
        if existing is not None and not args.overwrite:
            skipped += 1
            print(
                f"- {source.sourceCode}: exists ({existing.name}); use --overwrite to regenerate"
            )
            continue

        ext = ".jpg" if format_normalized == "jpeg" else ".png"
        filename = f"{base_name}{ext}"

        parts = urlsplit(browse_url)
        screenshot_url = urlunsplit(
            (parts.scheme, parts.netloc, parts.path, parts.query, "ha_nobanner=1")
        )

        docker_cmd = ["docker", "run", "--rm"]
        if should_use_host_network(screenshot_url):
            docker_cmd.extend(["--network", "host"])

        node_args = [
            "node",
            "/ha-scripts/generate_replay_preview.js",
            "--url",
            screenshot_url,
            "--out",
            f"/out/{filename}",
            "--format",
            format_normalized,
            "--quality",
            str(jpeg_quality),
            "--width",
            str(width),
            "--height",
            str(height),
            "--timeout-ms",
            str(timeout_ms),
            "--settle-ms",
            str(settle_ms),
        ]

        node_cmd = " ".join(shlex.quote(part) for part in node_args)
        install_cmd = (
            "set -euo pipefail; "
            f"if [ ! -d node_modules/playwright ]; then "
            "npm init -y >/dev/null 2>&1; "
            'npm install --silent --no-progress --no-audit --no-fund '
            f"playwright@{shlex.quote(playwright_npm_version)}; "
            "fi; "
            f"{node_cmd}"
        )

        docker_cmd.extend(
            [
                "-e",
                "PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1",
                "-v",
                f"{script_path}:/ha-scripts/generate_replay_preview.js:ro",
                "-v",
                f"{preview_dir}:/out:rw",
                "-v",
                f"{node_cache_dir}:/ha-node:rw",
                "-w",
                "/ha-node",
                image,
                "bash",
                "-lc",
                install_cmd,
            ]
        )

        if args.dry_run:
            generated += 1
            print(f"- {source.sourceCode}: would generate {filename}")
            continue

        print(f"- {source.sourceCode}: generating {filename}...")
        result = subprocess.run(  # nosec: B603 - operator-controlled docker invocation
            docker_cmd,
            text=True,
            capture_output=True,
        )
        if result.returncode != 0:
            failures.append(source.sourceCode)
            print(
                f"  ERROR: preview generation failed for {source.sourceCode} (exit {result.returncode})",
                file=sys.stderr,
            )
            if result.stdout:
                print(result.stdout)
            if result.stderr:
                print(result.stderr, file=sys.stderr)
            continue

        generated += 1

    print("")
    print(f"Generated: {generated}")
    print(f"Skipped:   {skipped}")
    if failures:
        print(f"Failed:    {len(failures)} ({', '.join(failures)})", file=sys.stderr)
        sys.exit(1)


def cmd_register_job_dir(args: argparse.Namespace) -> None:
    """
    Attach an ArchiveJob row to an existing archive_tool output directory.

    This is primarily intended for development and debugging when you already
    have a crawl directory on disk (produced by archive_tool) and want to
    index its WARCs via the backend.
    """
    from datetime import datetime, timezone
    from pathlib import Path

    from .models import \
        ArchiveJob as ORMArchiveJob  # local import to avoid cycles
    from .models import Source

    output_dir = Path(args.output_dir).resolve()
    if not output_dir.is_dir():
        print(
            f"ERROR: Output directory {output_dir} does not exist or is not a directory.",
            file=sys.stderr,
        )
        sys.exit(1)

    with get_session() as session:
        src = session.query(Source).filter_by(code=args.source.lower()).one_or_none()
        if src is None:
            print(
                f"ERROR: Source with code {args.source!r} does not exist. "
                "Run 'ha-backend seed-sources' or insert it manually.",
                file=sys.stderr,
            )
            sys.exit(1)

        now = datetime.now(timezone.utc)
        job_name = args.name or output_dir.name

        job = ORMArchiveJob(
            source_id=src.id,
            name=job_name,
            output_dir=str(output_dir),
            status="completed",  # ready for indexing
            queued_at=now,
            started_at=now,
            finished_at=now,
        )
        session.add(job)
        session.flush()
        job_id = job.id

    print("Registered job from existing directory:")
    print(f"  ID:         {job_id}")
    print(f"  Source:     {args.source}")
    print(f"  Name:       {job_name}")
    print(f"  Output dir: {output_dir}")
    print("")
    print("You can now index this job with:")
    print(f"  ha-backend index-job --id {job_id}")


def cmd_start_worker(args: argparse.Namespace) -> None:
    """
    Start the background worker loop.
    """
    run_worker_loop(poll_interval=args.poll_interval, run_once=args.once)


# === Argument parser wiring ===


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ha-backend",
        description="HealthArchive backend CLI utilities.",
    )
    subparsers = parser.add_subparsers(
        dest="command",
        metavar="COMMAND",
        required=True,
    )

    # check-env
    p_env = subparsers.add_parser(
        "check-env",
        help="Check archive root directory and basic environment.",
    )
    p_env.set_defaults(func=cmd_check_env)

    # check-archive-tool
    p_tool = subparsers.add_parser(
        "check-archive-tool",
        help="Run 'archive-tool --help' to verify the integrated archive_tool crawler CLI.",
    )
    p_tool.set_defaults(func=cmd_check_archive_tool)

    # check-db
    p_db = subparsers.add_parser(
        "check-db",
        help="Check database connectivity using the configured DATABASE_URL.",
    )
    p_db.set_defaults(func=cmd_check_db)

    # run-job
    p_run = subparsers.add_parser(
        "run-job",
        help="Run a single archive_tool job immediately.",
    )
    p_run.add_argument(
        "--name",
        required=True,
        help="Logical job name (also used as ZIM base name).",
    )
    p_run.add_argument(
        "--seeds",
        nargs="+",
        required=True,
        help="One or more seed URLs to crawl.",
    )
    p_run.add_argument(
        "--initial-workers",
        type=int,
        default=1,
        help="Initial workers for zimit (passed to archive_tool).",
    )
    p_run.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
        help="Log level for archive_tool.",
    )
    p_run.add_argument(
        "--cleanup",
        action="store_true",
        default=False,
        help="Tell archive_tool to cleanup temp dirs on success.",
    )
    p_run.add_argument(
        "--overwrite",
        action="store_true",
        default=False,
        help="Allow overwriting an existing ZIM in the output dir.",
    )
    p_run.add_argument(
        "passthrough",
        nargs=argparse.REMAINDER,
        help=(
            "Optional arguments to pass through directly to archive_tool "
            "(after a literal '--')."
        ),
    )
    p_run.set_defaults(func=cmd_run_job)

    # create-job
    p_create = subparsers.add_parser(
        "create-job",
        help="Create a persistent ArchiveJob for a given source using registry defaults.",
    )
    p_create.add_argument(
        "--source",
        required=True,
        help="Source code (e.g. 'hc', 'phac').",
    )
    p_create.add_argument(
        "--page-limit",
        type=int,
        help="Optional Zimit --pageLimit for this job (dev/testing).",
    )
    p_create.add_argument(
        "--depth",
        type=int,
        help="Optional Zimit --depth for this job (dev/testing).",
    )
    p_create.set_defaults(func=cmd_create_job)

    # run-db-job
    p_run_db = subparsers.add_parser(
        "run-db-job",
        help="Run a database-backed ArchiveJob by numeric ID.",
    )
    p_run_db.add_argument(
        "--id",
        type=int,
        required=True,
        help="ArchiveJob ID to run.",
    )
    p_run_db.set_defaults(func=cmd_run_db_job)

    # index-job
    p_index = subparsers.add_parser(
        "index-job",
        help="Index a completed ArchiveJob into Snapshot rows.",
    )
    p_index.add_argument(
        "--id",
        type=int,
        required=True,
        help="ArchiveJob ID to index.",
    )
    p_index.set_defaults(func=cmd_index_job)

    # seed-sources
    p_seed = subparsers.add_parser(
        "seed-sources",
        help="Insert initial Source rows (hc, phac) into the database if missing.",
    )
    p_seed.set_defaults(func=cmd_seed_sources)

    # backfill-search-vector
    p_backfill_search = subparsers.add_parser(
        "backfill-search-vector",
        help="Backfill Snapshot.search_vector for Postgres full-text search.",
    )
    p_backfill_search.add_argument(
        "--batch-size",
        type=int,
        default=5000,
        help="Rows to update per batch.",
    )
    p_backfill_search.add_argument(
        "--start-id",
        type=int,
        default=0,
        help="Start backfill from snapshot IDs greater than this value.",
    )
    p_backfill_search.add_argument(
        "--job-id",
        type=int,
        help="Optional ArchiveJob ID filter (only backfill snapshots for this job).",
    )
    p_backfill_search.add_argument(
        "--force",
        action="store_true",
        default=False,
        help="Recompute vectors even when search_vector is already populated.",
    )
    p_backfill_search.set_defaults(func=cmd_backfill_search_vector)

    # refresh-snapshot-metadata
    p_refresh = subparsers.add_parser(
        "refresh-snapshot-metadata",
        help="Refresh title/snippet/language for a job by re-reading its WARCs.",
    )
    p_refresh.add_argument(
        "--job-id",
        type=int,
        required=True,
        help="ArchiveJob ID whose snapshots should be refreshed.",
    )
    p_refresh.add_argument(
        "--batch-size",
        type=int,
        default=200,
        help="Rows to update per DB batch.",
    )
    p_refresh.add_argument(
        "--limit",
        type=int,
        help="Optional maximum number of WARC records to process (debugging).",
    )
    p_refresh.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Compute updates but roll back at the end (no DB changes).",
    )
    p_refresh.set_defaults(func=cmd_refresh_snapshot_metadata)

    # backfill-outlinks
    p_backfill_outlinks = subparsers.add_parser(
        "backfill-outlinks",
        help="Backfill SnapshotOutlink edges for a job by re-reading its WARCs.",
    )
    p_backfill_outlinks.add_argument(
        "--job-id",
        type=int,
        required=True,
        help="ArchiveJob ID whose snapshots should have outlinks extracted.",
    )
    p_backfill_outlinks.add_argument(
        "--batch-size",
        type=int,
        default=2000,
        help="Outlink rows to insert per DB batch.",
    )
    p_backfill_outlinks.add_argument(
        "--max-links-per-snapshot",
        type=int,
        default=200,
        help="Maximum number of unique outlink targets to record per snapshot.",
    )
    p_backfill_outlinks.add_argument(
        "--update-signals",
        action="store_true",
        default=False,
        help="Recompute PageSignal rows for affected groups after backfill.",
    )
    p_backfill_outlinks.add_argument(
        "--limit",
        type=int,
        help="Optional maximum number of WARC records to process (debugging).",
    )
    p_backfill_outlinks.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Compute and insert edges but roll back at the end (no DB changes).",
    )
    p_backfill_outlinks.set_defaults(func=cmd_backfill_outlinks)

    # recompute-page-signals
    p_signals = subparsers.add_parser(
        "recompute-page-signals",
        help="Rebuild PageSignal rows from SnapshotOutlink edges.",
    )
    p_signals.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Recompute signals but roll back at the end (no DB changes).",
    )
    p_signals.set_defaults(func=cmd_recompute_page_signals)

    # list-jobs
    p_list = subparsers.add_parser(
        "list-jobs",
        help="List recent ArchiveJob rows.",
    )
    p_list.add_argument(
        "--source",
        help="Filter by source code (e.g. 'hc').",
    )
    p_list.add_argument(
        "--status",
        nargs="+",
        help="Filter by one or more statuses (e.g. queued running completed).",
    )
    p_list.add_argument(
        "--limit",
        type=int,
        default=20,
        help="Maximum number of jobs to display.",
    )
    p_list.set_defaults(func=cmd_list_jobs)

    # show-job
    p_show = subparsers.add_parser(
        "show-job",
        help="Show detailed information about a job.",
    )
    p_show.add_argument(
        "--id",
        type=int,
        required=True,
        help="ArchiveJob ID to show.",
    )
    p_show.set_defaults(func=cmd_show_job)

    # retry-job
    p_retry = subparsers.add_parser(
        "retry-job",
        help="Mark a failed job as retryable for crawl or re-indexing.",
    )
    p_retry.add_argument(
        "--id",
        type=int,
        required=True,
        help="ArchiveJob ID to retry.",
    )
    p_retry.set_defaults(func=cmd_retry_job)

    # validate-job-config
    p_validate = subparsers.add_parser(
        "validate-job-config",
        help=(
            "Validate a job's configuration by running archive_tool in "
            "dry-run mode without changing job status."
        ),
    )
    p_validate.add_argument(
        "--id",
        type=int,
        required=True,
        help="ArchiveJob ID whose config should be validated.",
    )
    p_validate.set_defaults(func=cmd_validate_job_config)

    # cleanup-job
    p_cleanup = subparsers.add_parser(
        "cleanup-job",
        help="Cleanup temporary directories and state for an indexed job.",
    )
    p_cleanup.add_argument(
        "--id",
        type=int,
        required=True,
        help="ArchiveJob ID to cleanup.",
    )
    p_cleanup.add_argument(
        "--mode",
        choices=["temp"],
        default="temp",
        help="Cleanup mode (currently only 'temp' is supported).",
    )
    p_cleanup.add_argument(
        "--force",
        action="store_true",
        default=False,
        help=(
            "Override safety checks (for example: allow temp cleanup even when "
            "replay is enabled)."
        ),
    )
    p_cleanup.set_defaults(func=cmd_cleanup_job)

    # replay-index-job
    p_replay_index = subparsers.add_parser(
        "replay-index-job",
        help="Make a job replayable by creating a pywb collection and CDX index.",
    )
    p_replay_index.add_argument(
        "--id",
        type=int,
        required=True,
        help="ArchiveJob ID to index for replay.",
    )
    p_replay_index.add_argument(
        "--collection",
        help="Override the pywb collection name (default: job-<id>).",
    )
    p_replay_index.add_argument(
        "--container",
        default="healtharchive-replay",
        help="Docker container name for the replay service.",
    )
    p_replay_index.add_argument(
        "--collections-dir",
        default="/srv/healtharchive/replay/collections",
        help="Host path to the pywb collections directory.",
    )
    p_replay_index.add_argument(
        "--warcs-host-root",
        default="/srv/healtharchive/jobs",
        help="Host path that contains WARCs and is mounted into the replay container.",
    )
    p_replay_index.add_argument(
        "--warcs-container-root",
        default="/warcs",
        help="Container path where --warcs-host-root is mounted.",
    )
    p_replay_index.add_argument(
        "--limit-warcs",
        type=int,
        help="For debugging: only link the first N discovered WARCs.",
    )
    p_replay_index.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Print actions without changing the filesystem or running docker.",
    )
    p_replay_index.set_defaults(func=cmd_replay_index_job)

    # replay-generate-previews
    p_replay_previews = subparsers.add_parser(
        "replay-generate-previews",
        help="Generate cached replay preview images for source entry pages.",
    )
    p_replay_previews.add_argument(
        "--source",
        nargs="+",
        help="Limit preview generation to one or more source codes (e.g. hc cihr).",
    )
    p_replay_previews.add_argument(
        "--overwrite",
        action="store_true",
        default=False,
        help="Regenerate previews even when a cached preview already exists.",
    )
    p_replay_previews.add_argument(
        "--format",
        choices=["jpeg", "png"],
        default="jpeg",
        help="Image format to generate (default: jpeg).",
    )
    p_replay_previews.add_argument(
        "--jpeg-quality",
        type=int,
        default=80,
        help="JPEG quality (20-95). Only used when --format=jpeg.",
    )
    p_replay_previews.add_argument(
        "--playwright-image",
        default="mcr.microsoft.com/playwright:v1.50.1-jammy",
        help="Docker image used to render pages and capture screenshots.",
    )
    p_replay_previews.add_argument(
        "--network",
        choices=["auto", "host", "bridge"],
        default="auto",
        help="Docker network mode (auto uses host networking when replay base is localhost).",
    )
    p_replay_previews.add_argument(
        "--width",
        type=int,
        default=1000,
        help="Viewport width for the preview screenshot.",
    )
    p_replay_previews.add_argument(
        "--height",
        type=int,
        default=540,
        help="Viewport height for the preview screenshot.",
    )
    p_replay_previews.add_argument(
        "--timeout-ms",
        type=int,
        default=45000,
        help="Navigation timeout in milliseconds.",
    )
    p_replay_previews.add_argument(
        "--settle-ms",
        type=int,
        default=1200,
        help="Additional delay after load (milliseconds) before taking a screenshot.",
    )
    p_replay_previews.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Print actions without creating files or running docker.",
    )
    p_replay_previews.set_defaults(func=cmd_replay_generate_previews)

    # start-worker
    p_worker = subparsers.add_parser(
        "start-worker",
        help="Start the background worker loop to process queued jobs.",
    )
    p_worker.add_argument(
        "--poll-interval",
        type=int,
        default=30,
        help="Seconds between polls when no work is found.",
    )
    p_worker.add_argument(
        "--once",
        action="store_true",
        default=False,
        help="Run a single iteration and exit.",
    )
    p_worker.set_defaults(func=cmd_start_worker)

    # register-job-dir
    p_register = subparsers.add_parser(
        "register-job-dir",
        help=(
            "Attach an ArchiveJob row to an existing archive_tool output "
            "directory (advanced/dev)."
        ),
    )
    p_register.add_argument(
        "--source",
        required=True,
        help="Source code for the job (e.g. 'hc').",
    )
    p_register.add_argument(
        "--output-dir",
        required=True,
        help="Existing archive_tool output directory to attach.",
    )
    p_register.add_argument(
        "--name",
        help="Optional logical job name; defaults to the directory name.",
    )
    p_register.set_defaults(func=cmd_register_job_dir)

    return parser


def main(argv: list[str] | None = None) -> None:
    configure_logging()
    parser = build_parser()
    args = parser.parse_args(argv)
    if not hasattr(args, "func"):
        parser.print_help()
        sys.exit(1)
    args.func(args)
