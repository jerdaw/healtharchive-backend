from __future__ import annotations

import argparse
import subprocess
import sys
from typing import Sequence

from sqlalchemy import text

from .config import get_archive_tool_config, get_database_config
from .db import get_engine, get_session
from .indexing import index_job
from .job_registry import create_job_for_source
from .jobs import create_job, run_persistent_job
from .seeds import seed_sources
from .worker import run_worker_loop
from .logging_config import configure_logging


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

    # We intentionally call the configured command; default is 'archive-tool'
    result = subprocess.run(
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

    from .models import ArchiveJob as ORMArchiveJob  # local import to avoid cycles

    with get_session() as session:
        job_row: ORMArchiveJob = create_job_for_source(
            source_code,
            session=session,
        )

    print("Created job:")
    print(f"  ID:         {job_row.id}")
    print(f"  Source:     {source_code}")
    print(f"  Name:       {job_row.name}")
    print(f"  Output dir: {job_row.output_dir}")


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


def cmd_list_jobs(args: argparse.Namespace) -> None:
    """
    List recent ArchiveJob rows with optional filters.
    """
    from .models import ArchiveJob as ORMArchiveJob, Source

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


def cmd_cleanup_job(args: argparse.Namespace) -> None:
    """
    Cleanup temporary directories and state for a completed/indexed job.

    Currently only 'temp' mode is supported, which removes archive_tool's
    temp dirs (including WARCs) and the state file, but leaves the job
    output directory and any final ZIM in place.
    """
    from pathlib import Path
    from datetime import datetime, timezone

    from archive_tool.state import CrawlState
    from archive_tool.utils import cleanup_temp_dirs, find_latest_temp_dir_fallback

    from .models import ArchiveJob as ORMArchiveJob

    job_id = args.id
    mode = args.mode

    if mode != "temp":
        print(
            f"Unsupported cleanup mode {mode!r}; only 'temp' is currently supported.",
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
        help="Run 'archive-tool --help' to verify the vendored archive_tool.",
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
    p_cleanup.set_defaults(func=cmd_cleanup_job)

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

    return parser


def main(argv: list[str] | None = None) -> None:
    configure_logging()
    parser = build_parser()
    args = parser.parse_args(argv)
    if not hasattr(args, "func"):
        parser.print_help()
        sys.exit(1)
    args.func(args)
