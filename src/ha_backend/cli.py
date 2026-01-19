from __future__ import annotations

import argparse
import json
import re
import subprocess  # nosec: B404 - controlled CLI invocation of external tool
import sys
from pathlib import Path
from typing import Any, ContextManager, Sequence, cast

from sqlalchemy import text
from sqlalchemy.engine.url import make_url

from .changes import compute_changes_backfill, compute_changes_since
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
    try:
        safe_url = make_url(db_cfg.database_url).render_as_string(hide_password=True)
    except Exception:
        safe_url = db_cfg.database_url
    print(f"Database URL: {safe_url}")

    try:
        get_engine()
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
          --name hc-2025-12-09 \\
          --seeds https://www.canada.ca/en/health-canada.html \\
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
    Insert initial Source rows (hc, phac, cihr) if they are missing.
    """
    created_count = 0
    with get_session() as session:
        created_count = seed_sources(session)

    print(f"Seeded {created_count} source(s).")


def cmd_compute_changes(args: argparse.Namespace) -> None:
    """
    Compute change events (diffs) between adjacent snapshot captures.
    """
    max_events = args.max_events
    source_code = args.source
    dry_run = args.dry_run

    with get_session() as session:
        if args.backfill:
            result = compute_changes_backfill(
                session,
                source_code=source_code,
                max_events=max_events,
                dry_run=dry_run,
            )
        else:
            result = compute_changes_since(
                session,
                since_days=args.since_days,
                source_code=source_code,
                max_events=max_events,
                dry_run=dry_run,
            )

    mode = "backfill" if args.backfill else f"last {args.since_days} days"
    print("Computed change events:")
    print(f"  Mode:    {mode}")
    print(f"  Created: {result.created}")
    print(f"  Skipped: {result.skipped}")
    print(f"  Dry-run: {dry_run}")


def cmd_schedule_annual(args: argparse.Namespace) -> None:
    """
    Enqueue the Jan 01 (UTC) annual campaign jobs for a specific year.

    This command is intentionally conservative:
    - dry-run is the default (no DB changes unless --apply is passed)
    - sources are allowlisted and ordered (hc, phac, cihr)
    - job creation is idempotent using config metadata + name collision checks
    - refuses to create a new annual job if the source already has an "active"
      job (queued/running/completed/indexing/index_failed/retryable) that is not
      indexed yet
    """
    from datetime import datetime, timedelta, timezone

    from .job_registry import (
        build_job_config,
        build_output_dir_for_job,
        generate_job_name,
        get_config_for_source,
    )
    from .models import ArchiveJob as ORMArchiveJob
    from .models import Source

    apply_mode = bool(getattr(args, "apply", False))

    now = datetime.now(timezone.utc)
    year = getattr(args, "year", None)
    if year is None:
        if now.month == 1 and now.day == 1:
            year = now.year
        else:
            print(
                "ERROR: --year is required unless this command is run on Jan 01 (UTC).",
                file=sys.stderr,
            )
            sys.exit(1)

    if not isinstance(year, int) or year < 1970 or year > 2100:
        print(
            "ERROR: --year must be a four-digit year between 1970 and 2100.",
            file=sys.stderr,
        )
        sys.exit(1)

    campaign_dt = datetime(year, 1, 1, tzinfo=timezone.utc)
    campaign_date = campaign_dt.date().isoformat()

    annual_sources_ordered = ("hc", "phac", "cihr")
    allowed_sources = set(annual_sources_ordered)

    requested_sources = getattr(args, "sources", None) or list(annual_sources_ordered)
    normalized_requested = [s.strip().lower() for s in requested_sources if s.strip()]
    invalid_sources = sorted({s for s in normalized_requested if s not in allowed_sources})
    if invalid_sources:
        print(
            "ERROR: --sources contains unsupported codes: "
            + ", ".join(invalid_sources)
            + ". Allowed: hc, phac, cihr.",
            file=sys.stderr,
        )
        sys.exit(1)

    requested_set = set(normalized_requested)
    sources_in_order = [s for s in annual_sources_ordered if s in requested_set]

    max_create_per_run = getattr(args, "max_create_per_run", None)
    if max_create_per_run is None:
        max_create_per_run = len(sources_in_order)
    if max_create_per_run < 0:
        print("ERROR: --max-create-per-run must be >= 0.", file=sys.stderr)
        sys.exit(1)

    tool_cfg = get_archive_tool_config()
    # We intentionally stagger queued_at by a few seconds across sources so the
    # single-worker queue pick order is deterministic (hc → phac → cihr) even if
    # all jobs are scheduled in the same command invocation.
    scheduled_at = now.replace(microsecond=0)
    queued_at_by_source = {
        source_code: scheduled_at + timedelta(seconds=i)
        for i, source_code in enumerate(sources_in_order)
    }

    blocking_statuses = {
        "queued",
        "retryable",
        "running",
        "completed",
        "indexing",
        "index_failed",
    }

    print("HealthArchive Backend – Schedule Annual Campaign")
    print("------------------------------------------------")
    print(f"Mode:            {'APPLY' if apply_mode else 'DRY-RUN'}")
    print(f"Campaign date:   {campaign_date} (Jan 01 UTC)")
    print(f"Sources:         {', '.join(sources_in_order) if sources_in_order else '(none)'}")
    print(f"Max creates:     {max_create_per_run}")
    print(f"Archive root:    {tool_cfg.archive_root}")
    print("")

    plan: list[dict[str, object]] = []

    with get_session() as session:
        for source_code in sources_in_order:
            cfg = get_config_for_source(source_code)
            if cfg is None:
                plan.append(
                    {
                        "source": source_code,
                        "action": "error",
                        "reason": "Source is not registered in job_registry.py",
                    }
                )
                continue

            source = session.query(Source).filter_by(code=cfg.source_code).one_or_none()
            if source is None:
                plan.append(
                    {
                        "source": source_code,
                        "action": "error",
                        "reason": "Missing Source row in DB; run 'ha-backend seed-sources'",
                    }
                )
                continue

            job_name = generate_job_name(cfg, now=campaign_dt)

            existing_jobs = (
                session.query(ORMArchiveJob)
                .filter(ORMArchiveJob.source_id == source.id)
                .order_by(ORMArchiveJob.id.desc())
                .all()
            )

            annual_matches = [
                j
                for j in existing_jobs
                if (j.config or {}).get("campaign_kind") == "annual"
                and (j.config or {}).get("campaign_year") == year
            ]
            if len(annual_matches) > 1:
                plan.append(
                    {
                        "source": source_code,
                        "action": "error",
                        "reason": f"Multiple annual jobs already exist for {year} (ids: {', '.join(str(j.id) for j in annual_matches)})",
                    }
                )
                continue
            if len(annual_matches) == 1:
                j = annual_matches[0]
                plan.append(
                    {
                        "source": source_code,
                        "action": "skip",
                        "reason": f"Already scheduled for {year} (job id={j.id}, status={j.status}, name={j.name})",
                    }
                )
                continue

            name_matches = [j for j in existing_jobs if j.name == job_name]
            if name_matches:
                j = name_matches[0]
                plan.append(
                    {
                        "source": source_code,
                        "action": "skip",
                        "reason": f"Job name already exists (job id={j.id}, status={j.status}, name={j.name}); refusing to create a duplicate",
                    }
                )
                continue

            blocked = next((j for j in existing_jobs if j.status in blocking_statuses), None)
            if blocked is not None:
                plan.append(
                    {
                        "source": source_code,
                        "action": "skip",
                        "reason": f"Source has an active job (job id={blocked.id}, status={blocked.status}, name={blocked.name}); finish/index it before scheduling annual",
                    }
                )
                continue

            job_config = build_job_config(cfg)
            job_config.update(
                {
                    "campaign_kind": "annual",
                    "campaign_year": year,
                    "campaign_date": campaign_date,
                    "campaign_date_utc": f"{campaign_date}T00:00:00Z",
                    "scheduler_version": "v1",
                }
            )

            output_dir = build_output_dir_for_job(
                cfg.source_code,
                job_name,
                archive_root=tool_cfg.archive_root,
                now=scheduled_at,
            )

            plan.append(
                {
                    "source": source_code,
                    "action": "create",
                    "job_name": job_name,
                    "output_dir": str(output_dir),
                    "queued_at": queued_at_by_source[source_code],
                    "job_config": job_config,
                    "source_id": source.id,
                }
            )

        errors = [p for p in plan if p.get("action") == "error"]
        creates = [p for p in plan if p.get("action") == "create"]

        for item in plan:
            src = str(item.get("source"))
            action = str(item.get("action"))
            reason = item.get("reason")

            if action == "create":
                job_name = str(item.get("job_name"))
                output_dir_str = str(item.get("output_dir"))
                job_config_data = item.get("job_config")
                seeds = job_config_data.get("seeds") if isinstance(job_config_data, dict) else None
                seed_count = len(seeds) if isinstance(seeds, list) else 0
                print(f"{src}: WOULD CREATE {job_name} (seeds={seed_count})")
                print(f"     output_dir={output_dir_str}")
            elif action == "skip":
                print(f"{src}: SKIP - {reason}")
            else:
                print(f"{src}: ERROR - {reason}")

        print("")
        print(
            f"Summary: would_create={len(creates)}, skip={len([p for p in plan if p.get('action') == 'skip'])}, errors={len(errors)}"
        )

        if not apply_mode:
            print("")
            print("Dry-run only; re-run with --apply to enqueue jobs.")
            return

        if errors:
            print("")
            print("Aborting (no changes applied) due to errors above.", file=sys.stderr)
            sys.exit(1)

        created = 0
        for item in creates:
            if created >= max_create_per_run:
                break

            source_id_value = item.get("source_id")
            if not isinstance(source_id_value, int):
                raise RuntimeError(
                    f"Internal error: expected source_id int, got {type(source_id_value).__name__}"
                )
            source_id = source_id_value
            source = session.get(Source, source_id)
            if source is None:
                raise RuntimeError(
                    f"Internal error: planned Source id={source_id} disappeared during apply."
                )

            job_config_value = item.get("job_config")
            if not isinstance(job_config_value, dict):
                raise RuntimeError(
                    f"Internal error: expected job_config dict, got {type(job_config_value).__name__}"
                )
            job_config = job_config_value

            job = ORMArchiveJob(
                source=source,
                name=str(item["job_name"]),
                output_dir=str(item["output_dir"]),
                status="queued",
                queued_at=cast(datetime, item["queued_at"]),
                config=job_config,
            )
            session.add(job)
            session.flush()
            created += 1
            print(f"CREATED {source.code}: job id={job.id} name={job.name}")

        if created < len(creates):
            for item in creates[created:]:
                print(
                    f"NOT CREATED (cap): {item['source']} {item['job_name']} "
                    f"(--max-create-per-run={max_create_per_run})"
                )


def cmd_annual_status(args: argparse.Namespace) -> None:
    """
    Report the status of the Jan 01 (UTC) annual campaign for a given year.

    This is a read-only convenience command designed for operations.
    """
    from datetime import datetime, timezone

    from .models import ArchiveJob as ORMArchiveJob
    from .models import Source

    year = int(args.year)
    campaign_dt = datetime(year, 1, 1, tzinfo=timezone.utc)
    campaign_date = campaign_dt.date().isoformat()

    annual_sources_ordered = ("hc", "phac", "cihr")
    allowed_sources = set(annual_sources_ordered)

    # Optional filter to match schedule-annual’s allowlist discipline.
    requested_sources = getattr(args, "sources", None) or list(annual_sources_ordered)
    normalized_requested = [s.strip().lower() for s in requested_sources if s.strip()]
    invalid_sources = sorted({s for s in normalized_requested if s not in allowed_sources})
    if invalid_sources:
        print(
            "ERROR: --sources contains unsupported codes: "
            + ", ".join(invalid_sources)
            + ". Allowed: hc, phac, cihr.",
            file=sys.stderr,
        )
        sys.exit(1)

    requested_set = set(normalized_requested)
    sources_in_order = [s for s in annual_sources_ordered if s in requested_set]

    def _dt_str(dt: datetime | None) -> str | None:
        if dt is None:
            return None
        return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat()

    results: list[dict[str, object]] = []

    with get_session() as session:
        for source_code in sources_in_order:
            expected_job_name = f"{source_code}-{year}0101"

            source = session.query(Source).filter_by(code=source_code).one_or_none()
            if source is None:
                results.append(
                    {
                        "sourceCode": source_code,
                        "expectedJobName": expected_job_name,
                        "status": "error",
                        "error": "Missing Source row; run 'ha-backend seed-sources'",
                        "job": None,
                        "candidates": [],
                        "isSearchReady": False,
                    }
                )
                continue

            jobs = (
                session.query(ORMArchiveJob)
                .filter(ORMArchiveJob.source_id == source.id)
                .order_by(ORMArchiveJob.id.desc())
                .all()
            )

            blocking_statuses = {
                "queued",
                "retryable",
                "running",
                "completed",
                "indexing",
                "index_failed",
            }
            blocking_job = next(
                (j for j in jobs if j.status in blocking_statuses),
                None,
            )
            blocking_payload = (
                {
                    "jobId": blocking_job.id,
                    "jobName": blocking_job.name,
                    "status": blocking_job.status,
                    "createdAt": _dt_str(blocking_job.created_at),
                }
                if blocking_job is not None
                else None
            )

            meta_candidates = [
                j
                for j in jobs
                if (j.config or {}).get("campaign_kind") == "annual"
                and (j.config or {}).get("campaign_year") == year
            ]
            name_candidates = [j for j in jobs if j.name == expected_job_name]

            candidates = meta_candidates or name_candidates
            candidates_payload = [
                {
                    "jobId": j.id,
                    "jobName": j.name,
                    "status": j.status,
                    "createdAt": _dt_str(j.created_at),
                }
                for j in candidates
            ]

            if not candidates:
                results.append(
                    {
                        "sourceCode": source_code,
                        "expectedJobName": expected_job_name,
                        "status": "missing",
                        "job": None,
                        "blockingJob": blocking_payload,
                        "candidates": [],
                        "isSearchReady": False,
                    }
                )
                continue

            if len(candidates) > 1:
                results.append(
                    {
                        "sourceCode": source_code,
                        "expectedJobName": expected_job_name,
                        "status": "error",
                        "error": "Multiple annual job candidates found; resolve duplicates before relying on status.",
                        "job": None,
                        "candidates": candidates_payload,
                        "isSearchReady": False,
                    }
                )
                continue

            job = candidates[0]
            cfg = job.config or {}

            is_search_ready = job.status == "indexed"

            results.append(
                {
                    "sourceCode": source_code,
                    "expectedJobName": expected_job_name,
                    "status": job.status,
                    "job": {
                        "jobId": job.id,
                        "jobName": job.name,
                        "outputDir": job.output_dir,
                        "queuedAt": _dt_str(job.queued_at),
                        "startedAt": _dt_str(job.started_at),
                        "finishedAt": _dt_str(job.finished_at),
                        "retryCount": job.retry_count,
                        "indexedPageCount": job.indexed_page_count,
                        "crawlerExitCode": job.crawler_exit_code,
                        "crawlerStatus": job.crawler_status,
                        "crawlerStage": job.crawler_stage,
                        "campaignKind": cfg.get("campaign_kind"),
                        "campaignYear": cfg.get("campaign_year"),
                        "campaignDate": cfg.get("campaign_date"),
                        "schedulerVersion": cfg.get("scheduler_version"),
                    },
                    "blockingJob": blocking_payload,
                    "candidates": candidates_payload,
                    "isSearchReady": is_search_ready,
                }
            )

    total_sources = len(results)
    indexed = sum(1 for r in results if r.get("status") == "indexed")
    failed = sum(1 for r in results if r.get("status") in {"failed", "index_failed"})
    missing = sum(1 for r in results if r.get("status") == "missing")
    errors = sum(1 for r in results if r.get("status") == "error")
    in_progress = total_sources - indexed - failed - missing - errors

    ready_for_search = indexed == total_sources and errors == 0

    payload = {
        "campaignYear": year,
        "campaignDate": campaign_date,
        "sources": results,
        "summary": {
            "totalSources": total_sources,
            "indexed": indexed,
            "inProgress": in_progress,
            "failed": failed,
            "missing": missing,
            "errors": errors,
            "readyForSearch": ready_for_search,
        },
    }

    if bool(getattr(args, "json", False)):
        print(json.dumps(payload, indent=2, sort_keys=True))
        return

    print(f"Annual campaign status — {campaign_date} (Jan 01 UTC)")
    print(f"Ready for search: {'YES' if ready_for_search else 'NO'}")
    print(
        "Summary: "
        f"total={total_sources} indexed={indexed} in_progress={in_progress} "
        f"failed={failed} missing={missing} errors={errors}"
    )
    print("")

    for r in results:
        source_code = str(r.get("sourceCode"))
        status = str(r.get("status"))

        if status == "missing":
            blocking_job_data = r.get("blockingJob")
            if isinstance(blocking_job_data, dict) and blocking_job_data:
                print(
                    f"{source_code}: MISSING annual job for {year} (expected {r.get('expectedJobName')}); "
                    f"active_job={blocking_job_data.get('jobId')}({blocking_job_data.get('status')}) {blocking_job_data.get('jobName')}"
                )
            else:
                print(
                    f"{source_code}: MISSING annual job for {year} (expected {r.get('expectedJobName')})"
                )
            continue

        if status == "error":
            err = r.get("error") or "Unknown error"
            print(f"{source_code}: ERROR - {err}")
            candidate_payloads = r.get("candidates") or []
            if isinstance(candidate_payloads, list) and candidate_payloads:
                ids = ", ".join(
                    f"{c.get('jobId')}({c.get('status')})"
                    for c in candidate_payloads
                    if isinstance(c, dict)
                )
                if ids:
                    print(f"     candidates: {ids}")
            continue

        job_data = r.get("job") or {}
        if not isinstance(job_data, dict):
            job_data = {}

        print(
            f"{source_code}: job_id={job_data.get('jobId')} status={status} "
            f"indexed_pages={job_data.get('indexedPageCount')} retries={job_data.get('retryCount')} "
            f"crawl_rc={job_data.get('crawlerExitCode')} crawl_status={job_data.get('crawlerStatus')} "
            f"name={job_data.get('jobName')}"
        )


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
            print(f"Database dialect is {dialect_name!r}; Postgres FTS backfill is skipped.")
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
            exec_result = cast(Any, session.execute(stmt))
            session.commit()

            batch_updated = int(exec_result.rowcount or 0)
            total_updated += batch_updated
            print(
                f"Backfilled search_vector for ids ({last_id}, {max_id}] "
                f"({batch_updated} rows; total {total_updated})."
            )
            last_id = max_id

    print(f"Done. Total rows updated: {total_updated}")


def cmd_backfill_normalized_url_groups(args: argparse.Namespace) -> None:
    """
    Backfill Snapshot.normalized_url_group for existing rows.

    This improves /api/search de-duplication in view=pages and supports URL-style
    lookup features that rely on normalized page grouping.
    """
    from sqlalchemy import or_

    from .models import Snapshot, Source
    from .url_normalization import normalize_url_for_grouping

    batch_size: int = args.batch_size
    job_id: int | None = args.job_id
    source: str | None = args.source
    dry_run: bool = args.dry_run
    limit: int | None = args.limit

    normalized_source = source.strip().lower() if source else None

    updated = 0
    scanned = 0

    with get_session() as session:
        query = session.query(Snapshot)
        if normalized_source:
            query = query.join(Source).filter(Source.code == normalized_source)
        if job_id is not None:
            query = query.filter(Snapshot.job_id == job_id)

        query = query.filter(
            or_(
                Snapshot.normalized_url_group.is_(None),
                Snapshot.normalized_url_group == "",
            )
        ).order_by(Snapshot.id)

        if limit is not None:
            query = query.limit(limit)

        for snap in query.yield_per(batch_size):
            scanned += 1
            normalized = normalize_url_for_grouping(snap.url)
            if not normalized:
                continue
            snap.normalized_url_group = normalized
            updated += 1

            if scanned % batch_size == 0:
                session.flush()
                if not dry_run:
                    session.commit()

        session.flush()
        if dry_run:
            session.rollback()
        else:
            session.commit()

    mode = "DRY RUN" if dry_run else "UPDATED"
    print(f"{mode}: normalized_url_group for {updated} row(s) (scanned {scanned}).")


def cmd_rebuild_pages(args: argparse.Namespace) -> None:
    """
    Rebuild Page rows (page-level grouping) from Snapshot rows.

    This is metadata-only: it never reads or mutates WARC content.
    """
    from .models import ArchiveJob, Page, Source
    from .pages import discover_job_page_groups, rebuild_pages

    source: str | None = args.source
    job_id: int | None = args.job_id
    dry_run: bool = args.dry_run
    truncate: bool = args.truncate

    normalized_source = source.strip().lower() if source else None

    with get_session() as session:
        if truncate:
            deleted = session.query(Page).delete(synchronize_session=False)
            if dry_run:
                print(f"DRY RUN: would truncate pages table ({deleted} row(s) would be deleted).")
            else:
                print(f"Truncated pages table ({deleted} row(s) deleted).")

        if job_id is not None:
            job = session.get(ArchiveJob, job_id)
            if job is None:
                raise SystemExit(f"ArchiveJob {job_id} not found.")
            if job.source_id is None:
                raise SystemExit(f"ArchiveJob {job_id} has no source_id; cannot rebuild pages.")

            groups = discover_job_page_groups(session, job_id=job_id)
            print(f"Discovered {len(groups)} page group(s) from job {job_id}.")

            result = rebuild_pages(
                session,
                source_id=job.source_id,
                groups=tuple(groups),
                delete_missing=True,
            )
        else:
            source_id = None
            if normalized_source:
                row = (
                    session.query(Source.id).filter(Source.code == normalized_source).one_or_none()
                )
                if row is None:
                    raise SystemExit(f"Source {normalized_source!r} not found.")
                source_id = int(row[0])

            result = rebuild_pages(
                session,
                source_id=source_id,
                delete_missing=False,
            )

        def format_upserted_groups(n: int) -> str:
            # Postgres often reports rowcount=-1 for INSERT..SELECT statements,
            # especially with ON CONFLICT; treat that as "unknown" rather than
            # printing a confusing negative number.
            if n < 0:
                return "unknown"
            return str(n)

        if dry_run:
            session.rollback()
            print(
                "DRY RUN: would upsert "
                f"{format_upserted_groups(result.upserted_groups)} page group(s) and delete {result.deleted_groups}."
            )
        else:
            session.commit()
            if result.upserted_groups < 0:
                from sqlalchemy import func

                total_pages = session.query(func.count(Page.id)).scalar() or 0
                if job_id is not None and job is not None and job.source_id is not None:
                    total_pages = (
                        session.query(func.count(Page.id))
                        .filter(Page.source_id == job.source_id)
                        .scalar()
                        or 0
                    )
                elif normalized_source and source_id is not None:
                    total_pages = (
                        session.query(func.count(Page.id))
                        .filter(Page.source_id == source_id)
                        .scalar()
                        or 0
                    )
                print(f"Pages table now contains {int(total_pages)} row(s).")

            print(
                "UPDATED: upserted "
                f"{format_upserted_groups(result.upserted_groups)} page group(s) and deleted {result.deleted_groups}."
            )


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
                    session.bulk_update_mappings(Snapshot, updates)  # type: ignore[arg-type]
                    if not dry_run:
                        session.commit()
                    updated_count += len(updates)
                    updates.clear()

            if limit is not None and processed_records > limit:
                break

        if updates:
            session.bulk_update_mappings(Snapshot, updates)  # type: ignore[arg-type]
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
                if from_group is not None:
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
                    session.bulk_insert_mappings(SnapshotOutlink, pending)  # type: ignore[arg-type]
                    if not dry_run:
                        session.flush()
                    inserted_rows += len(pending)
                    pending.clear()

            if limit is not None and processed_records > limit:
                break

        if pending:
            session.bulk_insert_mappings(SnapshotOutlink, pending)  # type: ignore[arg-type]
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
        if not inspector.has_table("snapshot_outlinks") or not inspector.has_table("page_signals"):
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

    discovered_warc_count: int | None = None
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

        # Best-effort, on-demand WARC discovery to avoid misleading "0" counts
        # for long-running crawls (job.warc_file_count is primarily updated by
        # the indexing pipeline).
        try:
            from .indexing.warc_discovery import discover_warcs_for_job

            discovered_warc_count = len(discover_warcs_for_job(job))
        except Exception:
            discovered_warc_count = None

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
    if discovered_warc_count is None:
        print("WARC files (discovered): (unknown)")
    else:
        print(f"WARC files (discovered): {discovered_warc_count}")
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


def cmd_recover_stale_jobs(args: argparse.Namespace) -> None:
    """
    Recover jobs that appear stuck in status='running'.

    This is safe-by-default: it prints a recovery plan unless --apply is passed.
    """
    from datetime import datetime, timedelta, timezone
    from pathlib import Path

    from sqlalchemy import or_

    from .crawl_stats import parse_crawl_log_progress
    from .models import ArchiveJob as ORMArchiveJob
    from .models import Source

    apply_mode = bool(getattr(args, "apply", False))
    older_than_minutes = int(getattr(args, "older_than_minutes", 0) or 0)
    if older_than_minutes <= 0:
        print("ERROR: --older-than-minutes must be > 0.", file=sys.stderr)
        sys.exit(2)

    require_no_progress_seconds = getattr(args, "require_no_progress_seconds", None)
    if require_no_progress_seconds is not None:
        require_no_progress_seconds = int(require_no_progress_seconds)
        if require_no_progress_seconds <= 0:
            print("ERROR: --require-no-progress-seconds must be > 0.", file=sys.stderr)
            sys.exit(2)

    include_missing_started_at = bool(getattr(args, "include_missing_started_at", False))
    source_filter = (getattr(args, "source", None) or "").strip().lower() or None
    limit = getattr(args, "limit", None)
    if limit is not None:
        limit = int(limit)
        if limit <= 0:
            print("ERROR: --limit must be > 0.", file=sys.stderr)
            sys.exit(2)

    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(minutes=older_than_minutes)

    print("HealthArchive Backend – Recover Stale Jobs")
    print("------------------------------------------")
    print(f"Mode:            {'APPLY' if apply_mode else 'DRY-RUN'}")
    print(f"Older than:      {older_than_minutes} minute(s)")
    print(f"Cutoff (UTC):    {cutoff.replace(microsecond=0).isoformat()}")
    print(f"Source filter:   {source_filter or '(none)'}")
    print(f"Limit:           {limit or '(none)'}")
    print(
        f"Require progress: {'(none)' if require_no_progress_seconds is None else f'no progress for ≥{require_no_progress_seconds}s'}"
    )
    print("")

    def _find_latest_combined_log(output_dir: Path) -> Path | None:
        try:
            candidates = sorted(
                output_dir.glob("archive_*.combined.log"),
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )
        except OSError:
            return None
        return candidates[0] if candidates else None

    def _find_log_for_job(job: ORMArchiveJob) -> Path | None:
        if job.combined_log_path:
            p = Path(job.combined_log_path)
            try:
                if p.is_file():
                    return p
            except OSError:
                return None
        if not job.output_dir:
            return None
        return _find_latest_combined_log(Path(job.output_dir))

    with get_session() as session:
        query = session.query(ORMArchiveJob).filter(ORMArchiveJob.status == "running")
        if include_missing_started_at:
            query = query.filter(
                or_(
                    ORMArchiveJob.started_at.is_(None),
                    ORMArchiveJob.started_at < cutoff,
                )
            )
        else:
            query = query.filter(ORMArchiveJob.started_at.is_not(None)).filter(
                ORMArchiveJob.started_at < cutoff
            )

        if source_filter:
            query = query.join(Source).filter(Source.code == source_filter)

        query = query.order_by(ORMArchiveJob.started_at.asc().nullsfirst(), ORMArchiveJob.id.asc())
        if limit is not None:
            query = query.limit(limit)

        try:
            jobs = query.all()
        except Exception as exc:
            msg = str(exc)
            if "no such table" in msg and "archive_jobs" in msg:
                print(
                    "ERROR: database schema is missing required tables (archive_jobs).",
                    file=sys.stderr,
                )
                print(
                    "Hint: on production, load the backend env first so HEALTHARCHIVE_DATABASE_URL points at Postgres:",
                    file=sys.stderr,
                )
                print(
                    "  set -a; source /etc/healtharchive/backend.env; set +a",
                    file=sys.stderr,
                )
                print("Then re-run the command.", file=sys.stderr)
                sys.exit(1)
            raise
        if not jobs:
            print("No stale running jobs found.")
            return

        # Optional: tighten the selection to jobs with no progress in logs.
        if require_no_progress_seconds is not None:
            filtered: list[ORMArchiveJob] = []
            skipped: list[str] = []
            for job in jobs:
                log_path = _find_log_for_job(job)
                progress_age = None
                if log_path is not None:
                    progress = parse_crawl_log_progress(log_path)
                    if progress is not None:
                        progress_age = int(progress.last_progress_age_seconds(now_utc=now))
                if progress_age is None:
                    # If we can't determine progress, err on the side of allowing recovery
                    # (often indicates infra/mount issues or missing logs).
                    filtered.append(job)
                    continue
                if progress_age >= require_no_progress_seconds:
                    filtered.append(job)
                else:
                    skipped.append(
                        f"job_id={job.id} (progress_age_seconds={progress_age} < {require_no_progress_seconds})"
                    )
            if skipped:
                print("Skipping jobs with recent progress:")
                for line in skipped:
                    print(f"- {line}")
                print("")
            jobs = filtered
            if not jobs:
                print("No stale running jobs matched the progress requirement.")
                return

        for job in jobs:
            started_at = job.started_at
            if started_at is not None and started_at.tzinfo is None:
                started_at = started_at.replace(tzinfo=timezone.utc)
            started_str = (
                started_at.astimezone(timezone.utc).replace(microsecond=0).isoformat()
                if started_at
                else None
            )
            age_min = None
            if started_at is not None:
                age_min = int((now - started_at).total_seconds() // 60)
            source_code = job.source.code if job.source else "?"
            progress_age_str = "-"
            log_path = _find_log_for_job(job)
            if log_path is not None:
                progress = parse_crawl_log_progress(log_path)
                if progress is not None:
                    progress_age_str = str(int(progress.last_progress_age_seconds(now_utc=now)))
            print(
                f"job_id={job.id} source={source_code} status={job.status} "
                f"started_at={started_str} age_min={age_min} "
                f"last_progress_age_seconds={progress_age_str} name={job.name}"
            )

        if not apply_mode:
            print("")
            print("Dry-run only; re-run with --apply to mark these jobs as retryable.")
            return

        for job in jobs:
            job.status = "retryable"
            job.crawler_stage = "recovered_stale_running"

        print("")
        print(f"Recovered {len(jobs)} job(s) (set status=retryable).")


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

    Modes:

    - `temp`: legacy cleanup that deletes `.tmp*` directories and the state
      file. This can delete WARCs required for replay.
    - `temp-nonwarc`: safe cleanup that preserves WARCs by consolidating them
      into `<output_dir>/warcs/` before deleting `.tmp*`. This mode is designed
      to keep replay and snapshot viewing working.

    The underlying helpers (CrawlState, cleanup_temp_dirs) live in the in-repo
    ``archive_tool`` package and should be kept in sync with this command.

    Safety: When HEALTHARCHIVE_REPLAY_BASE_URL is set (replay is enabled),
    this command refuses to run in `temp` mode unless --force is provided,
    because deleting temp dirs also deletes WARCs required for replay. The
    `temp-nonwarc` mode is allowed because it preserves WARCs.
    """
    import shutil
    from datetime import datetime, timezone
    from pathlib import Path

    from archive_tool.state import CrawlState
    from archive_tool.utils import find_all_warc_files

    from .archive_storage import (
        build_warc_path_mapping,
        consolidate_warcs,
        get_job_provenance_dir,
        get_job_warcs_dir,
        snapshot_crawl_configs,
        snapshot_state_file,
    )
    from .models import ArchiveJob as ORMArchiveJob
    from .models import Snapshot

    job_id = args.id
    mode = args.mode
    dry_run = bool(getattr(args, "dry_run", False))

    if mode not in ("temp", "temp-nonwarc"):
        print(
            f"Unsupported cleanup mode {mode!r}; expected 'temp' or 'temp-nonwarc'.",
            file=sys.stderr,
        )
        sys.exit(1)

    if mode == "temp" and get_replay_base_url() and not args.force:
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

        output_dir = Path(job.output_dir).resolve()
        if not output_dir.is_dir():
            print(
                f"ERROR: Output directory {output_dir} does not exist or is not a directory.",
                file=sys.stderr,
            )
            sys.exit(1)

        # Discover temp dirs via CrawlState and a glob fallback.
        state = CrawlState(output_dir, initial_workers=1)
        temp_dirs = state.get_temp_dir_paths()
        temp_dir_candidates = sorted([p for p in output_dir.glob(".tmp*") if p.is_dir()])
        # Merge candidates while preserving order.
        seen_dirs: set[Path] = set()
        merged_temp_dirs: list[Path] = []
        for p in list(temp_dirs) + list(temp_dir_candidates):
            p = p.resolve()
            if p in seen_dirs:
                continue
            seen_dirs.add(p)
            merged_temp_dirs.append(p)
        temp_dirs = merged_temp_dirs

        had_state_file = state.state_file_path.exists()

        if mode == "temp":
            if not temp_dirs and not had_state_file:
                print(
                    f"No temp dirs or state file discovered for job {job.id}; nothing to cleanup.",
                    file=sys.stderr,
                )
                return

            if dry_run:
                print("Dry run: would delete temp dirs and state file:")
                for d in temp_dirs:
                    print(f"  - {d}")
                if had_state_file:
                    print(f"  - {state.state_file_path}")
                return

            from archive_tool.utils import cleanup_temp_dirs

            cleanup_temp_dirs(temp_dirs, state.state_file_path)
            job.cleanup_status = "temp_cleaned"
            job.cleaned_at = datetime.now(timezone.utc)
            job.state_file_path = None
            return

        # mode == "temp-nonwarc": consolidate WARCs to a stable dir, preserve provenance,
        # then delete `.tmp*` directories.
        if not temp_dirs and not had_state_file:
            print(
                f"No temp dirs or state file discovered for job {job.id}; nothing to cleanup.",
                file=sys.stderr,
            )
            return

        stable_warcs_dir = get_job_warcs_dir(output_dir)
        stable_present = stable_warcs_dir.is_dir() and (
            any(stable_warcs_dir.rglob("*.warc.gz")) or any(stable_warcs_dir.rglob("*.warc"))
        )

        # Determine whether this job's snapshots still reference `.tmp*` WARC paths.
        tmp_warc_prefix = f"{output_dir.as_posix()}/.tmp"
        tmp_ref_count = (
            session.query(Snapshot.id)
            .filter(Snapshot.job_id == job.id)
            .filter(Snapshot.warc_path.like(f"{tmp_warc_prefix}%"))
            .count()
        )

        # If stable WARCs are missing, consolidate them from `.tmp*` first.
        source_warcs: list[Path] = []
        if not stable_present:
            source_warcs = find_all_warc_files(temp_dirs) if temp_dirs else []
            if not source_warcs:
                print(
                    "ERROR: No WARCs discovered under .tmp* directories, and no stable warcs/ directory exists. "
                    "Refusing temp-nonwarc cleanup because it would likely break replay.",
                    file=sys.stderr,
                )
                sys.exit(1)

        if dry_run:
            print("Dry run: temp-nonwarc cleanup plan")
            print(f"Job ID:            {job.id}")
            print(f"Output dir:        {output_dir}")
            print(f"Stable WARCs dir:  {stable_warcs_dir} (present={int(stable_present)})")
            if not stable_present:
                print(f"Would consolidate: {len(source_warcs)} WARC(s)")
            print(f"Snapshots w/ .tmp WARCs: {tmp_ref_count}")
            print(f"Would rewrite Snapshot.warc_path: {int(tmp_ref_count > 0)}")
            print(f"Would preserve provenance under: {get_job_provenance_dir(output_dir)}")
            print("Would delete temp dirs:")
            for d in temp_dirs:
                print(f"  - {d}")
            if had_state_file:
                print(f"  - {state.state_file_path} (would copy to provenance then delete)")
            return

        if not stable_present:
            consolidate_warcs(
                output_dir=output_dir,
                source_warc_paths=source_warcs,
                allow_copy_fallback=True,
                dry_run=False,
            )

        # Rewrite Snapshot.warc_path values to point at stable WARCs before deleting temp dirs.
        if tmp_ref_count > 0:
            mapping = build_warc_path_mapping(output_dir)
            if not mapping:
                print(
                    f"ERROR: Missing or empty WARC manifest at {stable_warcs_dir / 'manifest.json'}; "
                    "cannot rewrite snapshot paths; refusing cleanup.",
                    file=sys.stderr,
                )
                sys.exit(1)

            distinct_paths = (
                session.query(Snapshot.warc_path).filter(Snapshot.job_id == job.id).distinct().all()
            )
            warc_paths_in_db = sorted({p for (p,) in distinct_paths if p})
            updated = 0
            for old_path in warc_paths_in_db:
                new_path = mapping.get(str(Path(old_path).resolve()))
                if not new_path or old_path == new_path:
                    continue
                updated += (
                    session.query(Snapshot)
                    .filter(Snapshot.job_id == job.id, Snapshot.warc_path == old_path)
                    .update({Snapshot.warc_path: new_path}, synchronize_session=False)
                )

            if updated:
                session.flush()

            # Refuse to delete temp dirs if any snapshot still references a `.tmp*` WARC path.
            remaining_tmp_refs = (
                session.query(Snapshot.id)
                .filter(Snapshot.job_id == job.id)
                .filter(Snapshot.warc_path.like(f"{tmp_warc_prefix}%"))
                .limit(1)
                .all()
            )
            if remaining_tmp_refs:
                print(
                    "ERROR: Some snapshots still reference WARCs under `.tmp*` after consolidation. "
                    "Refusing to delete temp dirs. Re-run indexing for this job or investigate the WARC manifest.",
                    file=sys.stderr,
                )
                sys.exit(1)

        provenance_dir = get_job_provenance_dir(output_dir)
        snapshot_state_file(output_dir, dest_dir=provenance_dir, dry_run=False)
        snapshot_crawl_configs(
            temp_dirs,
            output_dir=output_dir,
            dest_dir=provenance_dir,
            dry_run=False,
        )

        # Delete the original state file to avoid stale references to removed temp dirs;
        # we preserved a copy under provenance/.
        if state.state_file_path.exists():
            try:
                state.state_file_path.unlink()
            except OSError:
                pass

        for d in temp_dirs:
            if d.is_dir() and d.name.startswith(".tmp"):
                shutil.rmtree(d)

        job.cleanup_status = "temp_nonwarc_cleaned"
        job.cleaned_at = datetime.now(timezone.utc)

        prov_state_path = provenance_dir / "archive_state.json"
        job.state_file_path = str(prov_state_path) if prov_state_path.is_file() else None


def cmd_consolidate_warcs(args: argparse.Namespace) -> None:
    """
    Consolidate a job's WARCs into a stable `<output_dir>/warcs/` directory.

    This creates hardlinks by default (no extra disk usage) and writes a
    `warcs/manifest.json` mapping of original `.tmp*` WARC paths to stable
    filenames.
    """
    from pathlib import Path

    from archive_tool.utils import find_all_warc_files

    from .archive_storage import build_warc_path_mapping, consolidate_warcs
    from .indexing.warc_discovery import discover_temp_warcs_for_job
    from .models import ArchiveJob as ORMArchiveJob
    from .models import Snapshot

    job_id: int = args.id
    dry_run: bool = bool(args.dry_run)
    allow_copy: bool = bool(args.allow_copy)
    rewrite_paths: bool = bool(args.rewrite_snapshot_paths)

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

        temp_dirs = sorted([p for p in output_dir.glob(".tmp*") if p.is_dir()])
        source_warcs = find_all_warc_files(temp_dirs) if temp_dirs else []
        if not source_warcs:
            # Fallback to state-based discovery (may find temp dirs not present in glob for edge cases).
            source_warcs = discover_temp_warcs_for_job(job)

        if not source_warcs:
            print("No `.tmp*` WARCs discovered; nothing to consolidate.")
            return

        result = consolidate_warcs(
            output_dir=output_dir,
            source_warc_paths=source_warcs,
            allow_copy_fallback=allow_copy,
            dry_run=dry_run,
        )

        print("WARC consolidation")
        print("------------------")
        print(f"Job ID:           {job.id}")
        print(f"Output dir:       {output_dir}")
        print(f"Stable WARCs dir: {result.warcs_dir}")
        print(f"Manifest:         {result.manifest_path}")
        print(f"Source WARCs:     {len(source_warcs)}")
        print(f"Stable WARCs:     {len(result.stable_warcs)}")
        print(f"Created:          {result.created}")
        print(f"Reused:           {result.reused}")
        if dry_run:
            print("")
            print("Dry run: no files were created and no DB rows were updated.")
            return

        if rewrite_paths:
            mapping = build_warc_path_mapping(output_dir)
            if not mapping:
                print(
                    "ERROR: WARC manifest missing or empty; cannot rewrite snapshot paths.",
                    file=sys.stderr,
                )
                sys.exit(1)

            distinct_paths = (
                session.query(Snapshot.warc_path).filter(Snapshot.job_id == job.id).distinct().all()
            )
            warc_paths_in_db = sorted({p for (p,) in distinct_paths if p})
            updated = 0
            for old_path in warc_paths_in_db:
                new_path = mapping.get(str(Path(old_path).resolve()))
                if not new_path or new_path == old_path:
                    continue
                updated += (
                    session.query(Snapshot)
                    .filter(Snapshot.job_id == job.id, Snapshot.warc_path == old_path)
                    .update({Snapshot.warc_path: new_path}, synchronize_session=False)
                )
            if updated:
                session.commit()
            print(f"Rewrote Snapshot.warc_path for {updated} row(s).")


def cmd_job_storage_report(args: argparse.Namespace) -> None:
    """
    Print (and optionally refresh) a job's storage accounting fields.
    """
    from pathlib import Path

    from .archive_storage import compute_job_storage_stats
    from .indexing.warc_discovery import discover_warcs_for_job
    from .models import ArchiveJob as ORMArchiveJob

    job_id: int = args.id
    refresh: bool = bool(args.refresh)
    as_json: bool = bool(args.json)

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

        if refresh:
            warc_paths = discover_warcs_for_job(job)
            temp_dirs = sorted([p for p in output_dir.glob(".tmp*") if p.is_dir()])
            stats = compute_job_storage_stats(
                output_dir=output_dir,
                temp_dirs=temp_dirs,
                stable_warc_paths=warc_paths,
            )
            job.warc_file_count = int(stats.warc_file_count)
            job.warc_bytes_total = int(stats.warc_bytes_total)
            job.output_bytes_total = int(stats.output_bytes_total)
            job.tmp_bytes_total = int(stats.tmp_bytes_total)
            job.tmp_non_warc_bytes_total = int(stats.tmp_non_warc_bytes_total)
            job.storage_scanned_at = stats.scanned_at
            session.commit()

        payload = {
            "jobId": job.id,
            "name": job.name,
            "status": job.status,
            "outputDir": job.output_dir,
            "warcFileCount": int(job.warc_file_count),
            "warcBytesTotal": int(job.warc_bytes_total),
            "outputBytesTotal": int(job.output_bytes_total),
            "tmpBytesTotal": int(job.tmp_bytes_total),
            "tmpNonWarcBytesTotal": int(job.tmp_non_warc_bytes_total),
            "storageScannedAt": job.storage_scanned_at.isoformat()
            if job.storage_scanned_at
            else None,
        }

        if as_json:
            print(json.dumps(payload, indent=2, sort_keys=True))
            return

        print("Job storage report")
        print("------------------")
        for k in (
            "jobId",
            "name",
            "status",
            "outputDir",
            "warcFileCount",
            "warcBytesTotal",
            "outputBytesTotal",
            "tmpBytesTotal",
            "tmpNonWarcBytesTotal",
            "storageScannedAt",
        ):
            print(f"{k}: {payload[k]}")


def cmd_verify_warcs(args: argparse.Namespace) -> None:
    """
    Verify integrity of WARC files for a given job (optionally quarantining corrupt ones).

    This is intended for post-incident recovery and for sanity-checking outputs before indexing.
    """
    import os
    from datetime import datetime, timedelta, timezone
    from pathlib import Path

    from ha_backend.indexing.warc_discovery import discover_warcs_for_job
    from ha_backend.indexing.warc_verify import (
        WarcVerificationOptions,
        filter_warcs_by_mtime,
        quarantine_warcs,
        sort_warcs_by_mtime_desc,
        verify_warcs,
    )
    from ha_backend.models import ArchiveJob as ORMArchiveJob
    from ha_backend.models import Snapshot

    job_id = int(getattr(args, "job_id", None) or 0)
    if job_id <= 0:
        print("ERROR: --job-id is required.", file=sys.stderr)
        sys.exit(2)

    # NOTE: `--level` supports 0; do not use `or 1` which would coerce 0 -> 1.
    level_raw = getattr(args, "level", 1)
    level = int(level_raw) if level_raw is not None else 1
    if level not in (0, 1, 2):
        print("ERROR: --level must be one of: 0, 1, 2", file=sys.stderr)
        sys.exit(2)

    limit_warcs = getattr(args, "limit_warcs", None)
    if limit_warcs is not None:
        limit_warcs = int(limit_warcs)
        if limit_warcs <= 0:
            print("ERROR: --limit-warcs must be >= 1.", file=sys.stderr)
            sys.exit(2)

    since_minutes = getattr(args, "since_minutes", None)
    if since_minutes is not None:
        since_minutes = int(since_minutes)
        if since_minutes <= 0:
            print("ERROR: --since-minutes must be > 0.", file=sys.stderr)
            sys.exit(2)

    max_decompressed_bytes = getattr(args, "max_decompressed_bytes", None)
    if max_decompressed_bytes is not None:
        max_decompressed_bytes = int(max_decompressed_bytes)
        if max_decompressed_bytes <= 0:
            print("ERROR: --max-decompressed-bytes must be > 0.", file=sys.stderr)
            sys.exit(2)

    max_records = getattr(args, "max_records", None)
    if max_records is not None:
        max_records = int(max_records)
        if max_records <= 0:
            print("ERROR: --max-records must be > 0.", file=sys.stderr)
            sys.exit(2)

    apply_quarantine = bool(getattr(args, "apply_quarantine", False))
    json_out_raw = getattr(args, "json_out", None)
    json_out = Path(json_out_raw).expanduser() if json_out_raw else None
    json_out_user_provided = json_out is not None
    metrics_file_raw = getattr(args, "metrics_file", None)
    metrics_file = Path(metrics_file_raw).expanduser() if metrics_file_raw else None

    job_status: str
    output_dir: Path
    source_code: str
    warc_paths: list[Path]
    snapshots_present = False

    with get_session() as session:
        job = session.get(ORMArchiveJob, job_id)
        if job is None:
            print(f"ERROR: Job {job_id} not found.", file=sys.stderr)
            sys.exit(1)

        source_code = job.source.code if job.source else "?"
        output_dir = Path(job.output_dir).resolve()
        job_status = str(job.status)

        if apply_quarantine:
            snapshots_present = bool(
                session.query(Snapshot.id).filter(Snapshot.job_id == job_id).limit(1).all()
            )

        try:
            warc_paths = discover_warcs_for_job(job)
        except Exception as exc:
            print(f"ERROR: Failed to discover WARCs for job {job_id}: {exc}", file=sys.stderr)
            sys.exit(1)

    warc_paths = sort_warcs_by_mtime_desc(warc_paths)
    if since_minutes is not None:
        cutoff = datetime.now(timezone.utc) - timedelta(minutes=since_minutes)
        warc_paths = filter_warcs_by_mtime(warc_paths, since_epoch_seconds=int(cutoff.timestamp()))

    if limit_warcs is not None:
        warc_paths = warc_paths[:limit_warcs]

    if not warc_paths:
        print(f"ERROR: No WARCs discovered for job {job_id}.", file=sys.stderr)
        sys.exit(1)

    options = WarcVerificationOptions(
        level=level,
        max_decompressed_bytes=max_decompressed_bytes,
        max_records=max_records,
    )
    report = verify_warcs(warc_paths, options=options)

    now_utc = datetime.now(timezone.utc)
    ts = now_utc.strftime("%Y%m%dT%H%M%SZ")

    # Always emit a JSON report by default for audit/debuggability.
    # If the user did not pass --json-out, use <output_dir>/warc_verify/... and treat
    # failures to write as best-effort (verification results remain the source of truth).
    if json_out is None:
        json_out = output_dir / "warc_verify" / f"verify-warcs-{job_id}-{ts}.json"
        json_out_user_provided = False

    quarantined: list[dict[str, str]] = []
    if apply_quarantine and report.failures:
        if job_status == "running":
            print("ERROR: Refusing to quarantine WARCs while job is running.", file=sys.stderr)
            sys.exit(2)

        if snapshots_present:
            print(
                "ERROR: Refusing to quarantine WARCs for a job that already has Snapshot rows "
                "(this would break replay integrity).",
                file=sys.stderr,
            )
            sys.exit(2)

        quarantine_root = output_dir / "warcs_quarantine" / ts
        bad_paths = [Path(f.path) for f in report.failures if f.error_kind != "infra_error"]
        if bad_paths:
            output_root = output_dir.resolve()
            for p in bad_paths:
                if output_root not in p.resolve().parents:
                    print(
                        f"ERROR: Refusing to quarantine WARC outside job output_dir: {p}",
                        file=sys.stderr,
                    )
                    sys.exit(2)

            quarantined = quarantine_warcs(
                bad_paths,
                quarantine_root=quarantine_root,
                relative_to=output_dir,
            )

            marker_path = output_dir / "WARCS_QUARANTINED.txt"
            lines: list[str] = []
            lines.append("HealthArchive WARCs quarantined")
            lines.append("--------------------------------")
            lines.append(f"timestamp_utc={now_utc.replace(microsecond=0).isoformat()}")
            lines.append(f"job_id={job_id}")
            lines.append(f"source={source_code}")
            lines.append(f"output_dir={output_dir}")
            lines.append(f"quarantine_root={quarantine_root}")
            lines.append("")
            for entry in quarantined:
                lines.append(
                    f"- from={entry['from']} to={entry['to']} sha256={entry['sha256Before']} "
                    f"size_bytes={entry.get('sizeBytes', '?')} mtime={entry.get('mtimeEpochSeconds', '?')}"
                )
            lines.append("")
            marker_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

            with get_session() as session:
                job = session.get(ORMArchiveJob, job_id)
                if job is not None:
                    job.status = "retryable"
                    job.retry_count = 0

    if json_out is not None:
        try:
            json_out.parent.mkdir(parents=True, exist_ok=True)
            json_out.write_text(report.to_json(), encoding="utf-8")
        except Exception as exc:  # pragma: no cover - best-effort unless explicit
            if json_out_user_provided:
                print(f"ERROR: failed to write JSON report {json_out}: {exc}", file=sys.stderr)
                sys.exit(1)
            print(f"WARN: failed to write JSON report {json_out}: {exc}", file=sys.stderr)
            json_out = None

    if metrics_file is not None:
        # Best-effort: metrics writing should never be the reason a verification fails.
        def _prom_escape(value: str) -> str:
            return (
                value.replace("\\", "\\\\")
                .replace("\n", "\\n")
                .replace('"', '\\"')
                .replace("\r", "\\r")
            )

        def _write_textfile_metrics(path: Path, *, content: str) -> None:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(f"{path.suffix}.tmp.{os.getpid()}")
            tmp.write_text(content, encoding="utf-8")
            os.chmod(tmp, 0o644)
            tmp.replace(path)

        try:
            job_label = str(job_id)
            source_label = _prom_escape(source_code)
            now_seconds = int(now_utc.timestamp())
            ok = 1 if report.warcs_failed == 0 else 0
            lines = [
                "# HELP healtharchive_warc_verify_metrics_ok 1 if verify-warcs ran successfully.",
                "# TYPE healtharchive_warc_verify_metrics_ok gauge",
                "healtharchive_warc_verify_metrics_ok 1",
                "# HELP healtharchive_warc_verify_last_run_timestamp_seconds Unix timestamp of the last verify-warcs run.",
                "# TYPE healtharchive_warc_verify_last_run_timestamp_seconds gauge",
                f"healtharchive_warc_verify_last_run_timestamp_seconds {now_seconds}",
                "# HELP healtharchive_warc_verify_job_ok 1 if no failures were detected for the job.",
                "# TYPE healtharchive_warc_verify_job_ok gauge",
                f'healtharchive_warc_verify_job_ok{{job_id="{job_label}",source="{source_label}"}} {ok}',
                "# HELP healtharchive_warc_verify_job_level Verification level used for this run.",
                "# TYPE healtharchive_warc_verify_job_level gauge",
                f'healtharchive_warc_verify_job_level{{job_id="{job_label}",source="{source_label}"}} {level}',
                "# HELP healtharchive_warc_verify_job_warcs_total Total WARCs considered for verification.",
                "# TYPE healtharchive_warc_verify_job_warcs_total gauge",
                f'healtharchive_warc_verify_job_warcs_total{{job_id="{job_label}",source="{source_label}"}} {report.warcs_total}',
                "# HELP healtharchive_warc_verify_job_warcs_checked WARCs checked during this run.",
                "# TYPE healtharchive_warc_verify_job_warcs_checked gauge",
                f'healtharchive_warc_verify_job_warcs_checked{{job_id="{job_label}",source="{source_label}"}} {report.warcs_checked}',
                "# HELP healtharchive_warc_verify_job_warcs_failed WARCs that failed verification.",
                "# TYPE healtharchive_warc_verify_job_warcs_failed gauge",
                f'healtharchive_warc_verify_job_warcs_failed{{job_id="{job_label}",source="{source_label}"}} {report.warcs_failed}',
                "# HELP healtharchive_warc_verify_job_quarantined WARCs moved to quarantine during this run.",
                "# TYPE healtharchive_warc_verify_job_quarantined gauge",
                f'healtharchive_warc_verify_job_quarantined{{job_id="{job_label}",source="{source_label}"}} {len(quarantined)}',
            ]
            _write_textfile_metrics(metrics_file, content="\n".join(lines) + "\n")
        except Exception as exc:  # pragma: no cover - best-effort
            print(f"WARN: failed to write metrics file {metrics_file}: {exc}", file=sys.stderr)

    print("WARC verification report")
    print("-----------------------")
    print(f"job_id:        {job_id}")
    print(f"source:        {source_code}")
    print(f"output_dir:    {output_dir}")
    print(f"job_status:    {job_status}")
    print(f"level:         {level}")
    if since_minutes is not None:
        print(f"since_minutes: {since_minutes}")
    if limit_warcs is not None:
        print(f"limit_warcs:   {limit_warcs}")
    if max_decompressed_bytes is not None:
        print(f"max_decompressed_bytes: {max_decompressed_bytes}")
    if max_records is not None:
        print(f"max_records:   {max_records}")
    print("")
    print(
        f"Summary: total={report.warcs_total} checked={report.warcs_checked} ok={report.warcs_ok} failed={report.warcs_failed}"
    )

    if report.failures:
        print("")
        print("Failures:")
        for f in report.failures[:50]:
            print(f"- path={f.path} kind={f.error_kind} error={f.error}")
        if len(report.failures) > 50:
            print(f"... ({len(report.failures) - 50} more)")

    if quarantined:
        print("")
        print(f"Quarantined {len(quarantined)} WARC file(s).")
        print(f"Marker: {output_dir / 'WARCS_QUARANTINED.txt'}")

    if (json_out_user_provided or quarantined or report.warcs_failed > 0) and json_out is not None:
        print("")
        print(f"JSON report: {json_out}")

    if report.warcs_failed == 0:
        return
    sys.exit(1)


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
    import getpass
    import hashlib
    from datetime import datetime, timezone
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
            run_docker(["docker", "exec", container_name, "wb-manager", "init", collection_name])

        archive_dir.mkdir(parents=True, exist_ok=True)
        indexes_dir.mkdir(parents=True, exist_ok=True)

    # Remove existing stable WARC links for idempotency.
    existing_links = sorted(archive_dir.glob("warc-*"))
    if existing_links:
        print(f"Removing {len(existing_links)} existing WARC link(s) from {archive_dir}")
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
        try:
            path.unlink()
        except PermissionError as exc:
            user = getpass.getuser()
            print(
                f"ERROR: Permission denied while removing existing WARC link {path}: {exc}\n"
                f"Hint: run this command as a user that can write to {archive_dir} "
                f"(e.g. `sudo -u hareplay ...` or `sudo ...`). Current user: {user}\n"
                f"Debug: `ls -ld {archive_dir}`",
                file=sys.stderr,
            )
            sys.exit(1)

    host_root_resolved = warcs_host_root.resolve()
    if not host_root_resolved.is_dir():
        print(
            f"ERROR: WARCs host root {host_root_resolved} does not exist or is not a directory.",
            file=sys.stderr,
        )
        sys.exit(1)

    print(f"Linking {len(warc_paths)} WARC(s) into {archive_dir}")
    rel_paths_for_hash: list[str] = []
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

        rel_paths_for_hash.append(rel.as_posix())

        target_in_container = str(Path(warcs_container_root) / rel)
        link_path = archive_dir / link_name

        if dry_run:
            print(f"  would link {link_path} -> {target_in_container}")
            continue

        try:
            link_path.symlink_to(target_in_container)
        except PermissionError as exc:
            user = getpass.getuser()
            print(
                f"ERROR: Permission denied while creating WARC link {link_path}: {exc}\n"
                f"Hint: run this command as a user that can write to {archive_dir} "
                f"(e.g. `sudo -u hareplay ...` or `sudo ...`). Current user: {user}\n"
                f"Debug: `ls -ld {archive_dir}`",
                file=sys.stderr,
            )
            sys.exit(1)

    if dry_run:
        print("")
        print(f"Would run: docker exec {container_name} wb-manager reindex {collection_name}")
        return

    print("Rebuilding pywb CDX index (wb-manager reindex)...")
    run_docker(["docker", "exec", container_name, "wb-manager", "reindex", collection_name])
    warc_list_hash = hashlib.sha256(
        "\n".join(sorted(rel_paths_for_hash)).encode("utf-8")
    ).hexdigest()
    marker_path = collection_root / "replay-index.meta.json"
    marker_payload = {
        "version": 1,
        "jobId": job_id,
        "collectionName": collection_name,
        "indexedAtUtc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "warcCount": len(rel_paths_for_hash),
        "warcListHash": warc_list_hash,
        "warcsHostRoot": str(host_root_resolved),
        "warcsContainerRoot": warcs_container_root,
    }
    try:
        tmp_path = marker_path.with_suffix(".tmp")
        tmp_path.write_text(
            json.dumps(marker_payload, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        tmp_path.replace(marker_path)
    except Exception as exc:
        print(
            f"WARNING: Failed to write replay index marker {marker_path}: {exc}",
            file=sys.stderr,
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
        sources = list_sources(lang=None, db=session)

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
            print(f"- {source.sourceCode}: exists ({existing.name}); use --overwrite to regenerate")
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
            "npm install --silent --no-progress --no-audit --no-fund "
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


def cmd_replay_reconcile(args: argparse.Namespace) -> None:
    """
    Reconcile replay indexing (pywb) and optional source previews for indexed jobs.

    This is intentionally "ops-y" and safe-by-default:
    - dry-run by default (use --apply to perform actions)
    - capped work per run
    - global lock to prevent concurrent runs

    It is designed to align with `docs/operations/replay-and-preview-automation-plan.md`.
    """
    import hashlib
    import os
    from dataclasses import dataclass
    from datetime import datetime, timezone
    from pathlib import Path

    from .api.routes_public import _find_replay_preview_file, list_sources
    from .indexing.warc_discovery import discover_warcs_for_job
    from .models import ArchiveJob as ORMArchiveJob
    from .models import Source

    apply_mode: bool = bool(args.apply)
    dry_run: bool = not apply_mode

    max_jobs: int = int(args.max_jobs)
    if max_jobs < 0:
        print("ERROR: --max-jobs must be >= 0.", file=sys.stderr)
        sys.exit(2)

    previews_enabled: bool = bool(args.previews)
    max_previews: int = int(args.max_previews)
    if max_previews < 0:
        print("ERROR: --max-previews must be >= 0.", file=sys.stderr)
        sys.exit(2)

    verify_warc_hash: bool = bool(args.verify_warc_hash)

    requested_sources = getattr(args, "sources", None) or []
    source_allowlist = {s.strip().lower() for s in requested_sources if s.strip()}

    requested_job_ids = getattr(args, "job_id", None) or []
    job_id_allowlist = {int(v) for v in requested_job_ids} if requested_job_ids else set()

    campaign_year: int | None = getattr(args, "campaign_year", None)
    if campaign_year is not None and campaign_year < 1970:
        print("ERROR: --campaign-year looks invalid.", file=sys.stderr)
        sys.exit(2)

    container_name: str = str(args.container)
    collections_dir = Path(args.collections_dir).expanduser()
    warcs_host_root = Path(args.warcs_host_root).expanduser()
    warcs_container_root = str(args.warcs_container_root)

    lock_file = Path(args.lock_file).expanduser()

    @dataclass(frozen=True)
    class ReplayCheck:
        job_id: int
        source_code: str | None
        collection_name: str
        needs_reindex: bool
        is_blocked: bool
        reason: str
        has_index: bool
        has_warc_links: bool

    def _lock_or_exit() -> ContextManager[object]:
        lock_file.parent.mkdir(parents=True, exist_ok=True)

        fcntl_module: Any | None
        try:
            import fcntl as fcntl_module
        except Exception:  # pragma: no cover - non-POSIX fallback
            fcntl_module = None

        if fcntl_module is None:
            # Minimal cross-platform fallback: atomic create. Not crash-proof, but
            # better than no lock in dev environments.
            try:
                fd = os.open(str(lock_file), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            except FileExistsError:
                print(
                    f"ERROR: Lock file already exists; is another reconciler running? {lock_file}",
                    file=sys.stderr,
                )
                sys.exit(2)
            os.write(
                fd,
                f"pid={os.getpid()}\nstarted_at_utc={datetime.now(timezone.utc).isoformat()}\n".encode(
                    "utf-8"
                ),
            )
            os.close(fd)

            class _FallbackLock:
                def __enter__(self) -> "_FallbackLock":
                    return self

                def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
                    try:
                        lock_file.unlink()
                    except FileNotFoundError:
                        return

            return _FallbackLock()

        fh = lock_file.open("a+")
        try:
            fcntl_module.flock(fh.fileno(), fcntl_module.LOCK_EX | fcntl_module.LOCK_NB)
        except BlockingIOError:
            print(
                f"ERROR: Another replay reconciler is already running (lock held): {lock_file}",
                file=sys.stderr,
            )
            sys.exit(2)

        fh.seek(0)
        fh.truncate()
        fh.write(f"pid={os.getpid()}\n")
        fh.write(f"started_at_utc={datetime.now(timezone.utc).isoformat()}\n")
        fh.flush()

        class _FlockLock:
            def __enter__(self) -> "_FlockLock":
                return self

            def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
                try:
                    fh.close()
                except Exception:
                    return

        return _FlockLock()

    def _job_replay_state(job: ORMArchiveJob, source_code: str | None) -> ReplayCheck:
        collection_name = f"job-{job.id}"
        collection_root = collections_dir / collection_name
        archive_dir = collection_root / "archive"
        index_file = collection_root / "indexes" / "index.cdxj"

        has_index = index_file.is_file()
        has_warc_links = bool(list(archive_dir.glob("warc-*"))) if archive_dir.is_dir() else False

        if has_index and has_warc_links and not verify_warc_hash:
            return ReplayCheck(
                job_id=job.id,
                source_code=source_code,
                collection_name=collection_name,
                needs_reindex=False,
                is_blocked=False,
                reason="ready",
                has_index=has_index,
                has_warc_links=has_warc_links,
            )

        marker_path = collection_root / "replay-index.meta.json"
        if has_index and has_warc_links and marker_path.is_file() and verify_warc_hash:
            try:
                marker = json.loads(marker_path.read_text(encoding="utf-8"))
            except Exception as exc:
                return ReplayCheck(
                    job_id=job.id,
                    source_code=source_code,
                    collection_name=collection_name,
                    needs_reindex=True,
                    is_blocked=False,
                    reason=f"marker_parse_error: {exc}",
                    has_index=has_index,
                    has_warc_links=has_warc_links,
                )

            expected_hash = marker.get("warcListHash")
            expected_count = marker.get("warcCount")

            try:
                warcs = discover_warcs_for_job(job)
            except Exception as exc:
                return ReplayCheck(
                    job_id=job.id,
                    source_code=source_code,
                    collection_name=collection_name,
                    needs_reindex=True,
                    is_blocked=True,
                    reason=f"blocked: warc_discovery_failed: {exc}",
                    has_index=has_index,
                    has_warc_links=has_warc_links,
                )

            if not warcs:
                return ReplayCheck(
                    job_id=job.id,
                    source_code=source_code,
                    collection_name=collection_name,
                    needs_reindex=True,
                    is_blocked=True,
                    reason="blocked: no_warcs_discovered",
                    has_index=has_index,
                    has_warc_links=has_warc_links,
                )

            try:
                rels = sorted(
                    str(p.resolve().relative_to(warcs_host_root.resolve())).replace(os.sep, "/")
                    for p in warcs
                )
                current_hash = hashlib.sha256("\n".join(rels).encode("utf-8")).hexdigest()
            except Exception as exc:
                return ReplayCheck(
                    job_id=job.id,
                    source_code=source_code,
                    collection_name=collection_name,
                    needs_reindex=True,
                    is_blocked=False,
                    reason=f"hash_error: {exc}",
                    has_index=has_index,
                    has_warc_links=has_warc_links,
                )

            if expected_hash and expected_hash != current_hash:
                return ReplayCheck(
                    job_id=job.id,
                    source_code=source_code,
                    collection_name=collection_name,
                    needs_reindex=True,
                    is_blocked=False,
                    reason="warc_hash_changed",
                    has_index=has_index,
                    has_warc_links=has_warc_links,
                )

            if isinstance(expected_count, int) and expected_count != len(warcs):
                return ReplayCheck(
                    job_id=job.id,
                    source_code=source_code,
                    collection_name=collection_name,
                    needs_reindex=True,
                    is_blocked=False,
                    reason="warc_count_changed",
                    has_index=has_index,
                    has_warc_links=has_warc_links,
                )

            return ReplayCheck(
                job_id=job.id,
                source_code=source_code,
                collection_name=collection_name,
                needs_reindex=False,
                is_blocked=False,
                reason="ready (marker verified)",
                has_index=has_index,
                has_warc_links=has_warc_links,
            )

        # If we're missing the basic replay ingredients, we need to (re)index.
        needs_reindex = not has_index or not has_warc_links
        reason_parts: list[str] = []
        if not has_index:
            reason_parts.append("missing_index")
        if not has_warc_links:
            reason_parts.append("missing_warc_links")

        is_blocked = False
        reason = ",".join(reason_parts) if reason_parts else "needs_reindex"

        if needs_reindex:
            try:
                warcs = discover_warcs_for_job(job)
            except Exception as exc:
                is_blocked = True
                reason = f"blocked: warc_discovery_failed: {exc}"
            else:
                if not warcs:
                    is_blocked = True
                    reason = "blocked: no_warcs_discovered"

        return ReplayCheck(
            job_id=job.id,
            source_code=source_code,
            collection_name=collection_name,
            needs_reindex=needs_reindex and not is_blocked,
            is_blocked=is_blocked,
            reason=reason,
            has_index=has_index,
            has_warc_links=has_warc_links,
        )

    def _is_job_replay_ready(job_id: int) -> bool:
        collection_name = f"job-{job_id}"
        collection_root = collections_dir / collection_name
        archive_dir = collection_root / "archive"
        index_file = collection_root / "indexes" / "index.cdxj"
        return (
            index_file.is_file() and archive_dir.is_dir() and bool(list(archive_dir.glob("warc-*")))
        )

    with _lock_or_exit():
        print("Replay reconcile")
        print("----------------")
        print(f"Mode:            {'APPLY' if apply_mode else 'DRY-RUN'}")
        print(f"Collections dir: {collections_dir}")
        print(f"Lock file:       {lock_file}")
        print(f"Container:       {container_name}")
        print(f"Verify WARC hash: {'YES' if verify_warc_hash else 'NO'}")
        print(f"Max jobs:        {max_jobs}")
        print(f"Previews:        {'YES' if previews_enabled else 'NO'}")
        if previews_enabled:
            print(f"Max previews:    {max_previews}")
        if source_allowlist:
            print(f"Sources:         {', '.join(sorted(source_allowlist))}")
        if job_id_allowlist:
            print(f"Job IDs:         {', '.join(str(v) for v in sorted(job_id_allowlist))}")
        if campaign_year is not None:
            print(f"Campaign year:   {campaign_year}")
        print("")

        with get_session() as session:
            rows = (
                session.query(ORMArchiveJob, Source.code)
                .outerjoin(Source, Source.id == ORMArchiveJob.source_id)
                .filter(ORMArchiveJob.status == "indexed")
                .order_by(ORMArchiveJob.id.desc())
                .all()
            )

            filtered: list[tuple[ORMArchiveJob, str | None]] = []
            for job, source_code in rows:
                if source_allowlist:
                    if source_code is None or source_code not in source_allowlist:
                        continue
                if job_id_allowlist and job.id not in job_id_allowlist:
                    continue
                if campaign_year is not None:
                    cfg = job.config or {}
                    if (
                        cfg.get("campaign_kind") != "annual"
                        or cfg.get("campaign_year") != campaign_year
                    ):
                        continue
                filtered.append((job, source_code))

            checks = [_job_replay_state(job, source_code) for job, source_code in filtered]
            needs = [c for c in checks if c.needs_reindex]
            blocked = [c for c in checks if c.is_blocked]

            planned = needs[:max_jobs]
            capped = needs[max_jobs:]

            ready = len(checks) - len(needs) - len(blocked)

            print("Replay indexing status")
            print("----------------------")
            print(
                f"Jobs scanned: {len(checks)}  ready={ready}  needs_index={len(needs)}  blocked={len(blocked)}"
            )
            if capped:
                print(f"Capped: {len(capped)} (use --max-jobs to increase)")
            print("")

            if planned:
                for c in planned:
                    src = c.source_code or "?"
                    print(
                        f"WOULD INDEX: job_id={c.job_id} source={src} collection={c.collection_name} reason={c.reason}"
                        if dry_run
                        else f"INDEXING: job_id={c.job_id} source={src} collection={c.collection_name} reason={c.reason}"
                    )
                print("")

            if blocked:
                for c in blocked:
                    src = c.source_code or "?"
                    print(
                        f"BLOCKED: job_id={c.job_id} source={src} collection={c.collection_name} reason={c.reason}",
                        file=sys.stderr,
                    )
                print("")

            replay_indexed_ok = 0
            replay_indexed_failed = 0

            if apply_mode and planned:
                for c in planned:
                    ns = argparse.Namespace(
                        id=c.job_id,
                        collection=None,
                        container=container_name,
                        collections_dir=str(collections_dir),
                        warcs_host_root=str(warcs_host_root),
                        warcs_container_root=warcs_container_root,
                        limit_warcs=None,
                        dry_run=False,
                    )
                    try:
                        cmd_replay_index_job(ns)
                    except SystemExit as exc:
                        replay_indexed_failed += 1
                        print(
                            f"ERROR: replay-index-job failed for job {c.job_id} (exit {exc.code})",
                            file=sys.stderr,
                        )
                    else:
                        replay_indexed_ok += 1

            if apply_mode and planned:
                print("")
                print(
                    f"Replay indexing applied: ok={replay_indexed_ok} failed={replay_indexed_failed}"
                )

            # === Optional preview reconciliation ===
            if not previews_enabled or max_previews == 0:
                if dry_run:
                    print("Previews: disabled (pass --previews to enable).")
                return

            preview_dir = get_replay_preview_dir()
            if preview_dir is None:
                print(
                    "ERROR: HEALTHARCHIVE_REPLAY_PREVIEW_DIR is not set; cannot reconcile previews.",
                    file=sys.stderr,
                )
                sys.exit(1)

            preview_dir = preview_dir.expanduser().resolve()

            source_summaries = list_sources(lang=None, db=session)
            wanted_sources = (
                [s for s in source_summaries if s.sourceCode in source_allowlist]
                if source_allowlist
                else list(source_summaries)
            )

            missing_preview_sources: list[str] = []
            blocked_preview_sources: list[str] = []
            planned_job_ids = {p.job_id for p in planned}

            for source_summary in wanted_sources:
                browse_url = source_summary.entryBrowseUrl or ""
                match = re.search(r"/job-(\d+)(?:/|$)", browse_url)
                if not match:
                    continue
                job_id = int(match.group(1))

                if not _is_job_replay_ready(job_id):
                    if dry_run and job_id in planned_job_ids:
                        # In dry-run mode, allow preview planning for jobs that would be
                        # made replay-ready by the planned replay indexing actions above.
                        pass
                    else:
                        blocked_preview_sources.append(source_summary.sourceCode)
                        continue

                found = _find_replay_preview_file(preview_dir, source_summary.sourceCode, job_id)
                if found is None:
                    missing_preview_sources.append(source_summary.sourceCode)

            if not missing_preview_sources:
                print("Previews: all selected sources already have previews.")
                return

            planned_preview_sources = missing_preview_sources[:max_previews]
            capped_previews = missing_preview_sources[max_previews:]

            print("")
            print("Preview status")
            print("--------------")
            print(
                f"Missing previews: {len(missing_preview_sources)}  planned={len(planned_preview_sources)}"
            )
            if capped_previews:
                print(f"Capped: {len(capped_previews)} (use --max-previews to increase)")
            if blocked_preview_sources:
                print(
                    "Blocked (replay not ready yet): "
                    + ", ".join(sorted(set(blocked_preview_sources))),
                    file=sys.stderr,
                )
            print("")

            if dry_run:
                print("WOULD GENERATE previews for: " + ", ".join(planned_preview_sources))
                print("Dry-run only; re-run with --apply to generate previews.")
                return

            ns = argparse.Namespace(
                source=planned_preview_sources,
                overwrite=False,
                format="jpeg",
                jpeg_quality=80,
                playwright_image="mcr.microsoft.com/playwright:v1.50.1-jammy",
                network="auto",
                width=1000,
                height=540,
                timeout_ms=45000,
                settle_ms=1200,
                dry_run=False,
            )
            try:
                cmd_replay_generate_previews(ns)
            except SystemExit as exc:
                print(
                    f"ERROR: replay-generate-previews failed (exit {exc.code}).",
                    file=sys.stderr,
                )
                sys.exit(int(exc.code or 1))


def cmd_register_job_dir(args: argparse.Namespace) -> None:
    """
    Attach an ArchiveJob row to an existing archive_tool output directory.

    This is primarily intended for development and debugging when you already
    have a crawl directory on disk (produced by archive_tool) and want to
    index its WARCs via the backend.
    """
    from datetime import datetime, timezone
    from pathlib import Path

    from .models import ArchiveJob as ORMArchiveJob  # local import to avoid cycles
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
            "Optional arguments to pass through directly to archive_tool (after a literal '--')."
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
        help="Insert initial Source rows (hc, phac, cihr) into the database if missing.",
    )
    p_seed.set_defaults(func=cmd_seed_sources)

    # compute-changes
    p_changes = subparsers.add_parser(
        "compute-changes",
        help="Compute change events (diffs) between adjacent snapshot captures.",
    )
    p_changes.add_argument(
        "--source",
        help="Optional source code filter (e.g., hc, phac).",
    )
    p_changes.add_argument(
        "--max-events",
        type=int,
        default=200,
        help="Maximum change events to compute in one run.",
    )
    p_changes.add_argument(
        "--since-days",
        type=int,
        default=30,
        help="Only consider snapshots captured in the last N days (default: 30).",
    )
    p_changes.add_argument(
        "--backfill",
        action="store_true",
        default=False,
        help="Backfill all missing change events (ignores --since-days).",
    )
    p_changes.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Compute counts without writing change events to the database.",
    )
    p_changes.set_defaults(func=cmd_compute_changes)

    # schedule-annual
    p_schedule_annual = subparsers.add_parser(
        "schedule-annual",
        help=(
            "Enqueue the Jan 01 (UTC) annual campaign jobs (hc/phac/cihr). "
            "Dry-run by default; pass --apply to create jobs."
        ),
    )
    p_schedule_annual.add_argument(
        "--year",
        type=int,
        help=(
            "Campaign year (jobs are labeled as Jan 01 UTC for that year). "
            "If omitted, only allowed when running on Jan 01 (UTC)."
        ),
    )
    p_schedule_annual.add_argument(
        "--sources",
        nargs="+",
        help="Subset of sources to schedule (allowlisted): hc phac cihr.",
    )
    p_schedule_annual.add_argument(
        "--max-create-per-run",
        type=int,
        help="Hard cap on jobs created in one run (defaults to number of selected sources).",
    )
    p_schedule_annual.add_argument(
        "--apply",
        action="store_true",
        default=False,
        help="Create queued ArchiveJob rows (otherwise dry-run only).",
    )
    p_schedule_annual.set_defaults(func=cmd_schedule_annual)

    # annual-status
    p_annual_status = subparsers.add_parser(
        "annual-status",
        help="Report annual campaign status for a year (jobs + indexing readiness).",
    )
    p_annual_status.add_argument(
        "--year",
        type=int,
        required=True,
        help="Campaign year (reports the Jan 01 UTC annual edition for that year).",
    )
    p_annual_status.add_argument(
        "--json",
        action="store_true",
        default=False,
        help="Emit machine-readable JSON instead of text output.",
    )
    p_annual_status.add_argument(
        "--sources",
        nargs="+",
        help="Subset of sources to report (allowlisted): hc phac cihr.",
    )
    p_annual_status.set_defaults(func=cmd_annual_status)

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

    # backfill-normalized-url-groups
    p_backfill_groups = subparsers.add_parser(
        "backfill-normalized-url-groups",
        help="Backfill Snapshot.normalized_url_group for consistent view=pages grouping.",
    )
    p_backfill_groups.add_argument(
        "--batch-size",
        type=int,
        default=5000,
        help="Rows to process per batch commit.",
    )
    p_backfill_groups.add_argument(
        "--job-id",
        type=int,
        help="Optional ArchiveJob ID filter.",
    )
    p_backfill_groups.add_argument(
        "--source",
        help="Optional Source code filter (e.g. 'hc', 'phac').",
    )
    p_backfill_groups.add_argument(
        "--limit",
        type=int,
        help="Optional maximum number of rows to scan (debugging).",
    )
    p_backfill_groups.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Compute updates but roll back at the end (no DB changes).",
    )
    p_backfill_groups.set_defaults(func=cmd_backfill_normalized_url_groups)

    # rebuild-pages
    p_rebuild_pages = subparsers.add_parser(
        "rebuild-pages",
        help="Rebuild page-level grouping rows (pages table) from snapshots.",
    )
    p_rebuild_pages.add_argument(
        "--source",
        help="Optional Source code filter (e.g. 'hc', 'phac').",
    )
    p_rebuild_pages.add_argument(
        "--job-id",
        type=int,
        help="Optional ArchiveJob ID; rebuild pages only for groups referenced by this job.",
    )
    p_rebuild_pages.add_argument(
        "--truncate",
        action="store_true",
        default=False,
        help="Delete all existing Page rows before rebuilding (useful for a full refresh).",
    )
    p_rebuild_pages.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Compute changes but roll back at the end (no DB changes).",
    )
    p_rebuild_pages.set_defaults(func=cmd_rebuild_pages)

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

    # recover-stale-jobs
    p_recover = subparsers.add_parser(
        "recover-stale-jobs",
        help="Recover jobs stuck in status=running by marking them retryable (safe-by-default).",
    )
    p_recover.add_argument(
        "--older-than-minutes",
        type=int,
        required=True,
        help="Mark jobs as stale if started more than this many minutes ago.",
    )
    p_recover.add_argument(
        "--require-no-progress-seconds",
        type=int,
        default=None,
        help=(
            "Optional safety: only recover jobs whose combined logs show no increase in crawled-count for at least this long. "
            "If progress cannot be determined, the job is still considered recoverable."
        ),
    )
    p_recover.add_argument(
        "--include-missing-started-at",
        action="store_true",
        default=False,
        help="Also recover running jobs missing started_at (unusual).",
    )
    p_recover.add_argument(
        "--source",
        help="Optional source code filter (e.g. hc).",
    )
    p_recover.add_argument(
        "--limit",
        type=int,
        help="Optional maximum number of jobs to recover.",
    )
    p_recover.add_argument(
        "--apply",
        action="store_true",
        default=False,
        help="Apply changes (default is dry-run).",
    )
    p_recover.set_defaults(func=cmd_recover_stale_jobs)

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
        choices=["temp", "temp-nonwarc"],
        default="temp",
        help=(
            "Cleanup mode. 'temp' deletes .tmp* and may delete WARCs; "
            "'temp-nonwarc' consolidates WARCs to warcs/ first and then "
            "deletes .tmp*."
        ),
    )
    p_cleanup.add_argument(
        "--force",
        action="store_true",
        default=False,
        help=(
            "Override safety checks (for example: allow temp cleanup even when replay is enabled)."
        ),
    )
    p_cleanup.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Print the planned cleanup actions without changing files or the DB.",
    )
    p_cleanup.set_defaults(func=cmd_cleanup_job)

    # consolidate-warcs
    p_consolidate = subparsers.add_parser(
        "consolidate-warcs",
        help="Consolidate a job's WARCs into a stable warcs/ directory.",
    )
    p_consolidate.add_argument(
        "--id",
        type=int,
        required=True,
        help="ArchiveJob ID whose WARCs should be consolidated.",
    )
    p_consolidate.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Print the planned consolidation actions without creating files.",
    )
    p_consolidate.add_argument(
        "--allow-copy",
        action="store_true",
        default=False,
        help=(
            "Allow falling back to file copies when hardlinking is not possible "
            "(e.g. cross-device)."
        ),
    )
    p_consolidate.add_argument(
        "--rewrite-snapshot-paths",
        action="store_true",
        default=False,
        help="Rewrite Snapshot.warc_path values for this job to point at warcs/ stable files.",
    )
    p_consolidate.set_defaults(func=cmd_consolidate_warcs)

    # job-storage-report
    p_storage = subparsers.add_parser(
        "job-storage-report",
        help="Report (and optionally refresh) a job's storage accounting fields.",
    )
    p_storage.add_argument(
        "--id",
        type=int,
        required=True,
        help="ArchiveJob ID to report on.",
    )
    p_storage.add_argument(
        "--refresh",
        action="store_true",
        default=False,
        help="Recompute storage stats from disk and persist them to the DB.",
    )
    p_storage.add_argument(
        "--json",
        action="store_true",
        default=False,
        help="Emit JSON instead of human-readable text.",
    )
    p_storage.set_defaults(func=cmd_job_storage_report)

    # verify-warcs
    p_verify_warcs = subparsers.add_parser(
        "verify-warcs",
        help="Verify integrity of WARC files for a job (optionally quarantine corrupt files).",
    )
    p_verify_warcs.add_argument(
        "--job-id",
        "--id",
        dest="job_id",
        type=int,
        required=True,
        help="ArchiveJob ID whose WARCs should be verified.",
    )
    p_verify_warcs.add_argument(
        "--level",
        type=int,
        default=1,
        choices=[0, 1, 2],
        help="Verification level (0=exist/read, 1=gzip, 2=warc parse) (default: 1).",
    )
    p_verify_warcs.add_argument(
        "--since-minutes",
        type=int,
        help="Only verify WARCs modified within the last N minutes (UTC).",
    )
    p_verify_warcs.add_argument(
        "--limit-warcs",
        type=int,
        help="Only verify the newest N WARCs (by mtime).",
    )
    p_verify_warcs.add_argument(
        "--max-decompressed-bytes",
        "--max-bytes",
        dest="max_decompressed_bytes",
        type=int,
        help="For level 1 gzip checks: stop after N decompressed bytes per WARC (bounded CPU).",
    )
    p_verify_warcs.add_argument(
        "--max-records",
        type=int,
        help="For level 2 WARC checks: stop after N WARC records per file.",
    )
    p_verify_warcs.add_argument(
        "--json-out",
        help="Write JSON report to this path (default: <output_dir>/warc_verify/...).",
    )
    p_verify_warcs.add_argument(
        "--metrics-file",
        help="Optional Prometheus textfile metrics output path (node_exporter textfile_collector).",
    )
    p_verify_warcs.add_argument(
        "--apply-quarantine",
        action="store_true",
        default=False,
        help=(
            "Move corrupt WARCs into <output_dir>/warcs_quarantine/<timestamp>/ and mark the job retryable. "
            "Refuses if the job is running or already has Snapshot rows."
        ),
    )
    p_verify_warcs.set_defaults(func=cmd_verify_warcs)

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

    # replay-reconcile
    p_replay_reconcile = subparsers.add_parser(
        "replay-reconcile",
        help=(
            "Reconcile pywb replay indexing (and optionally previews) for indexed jobs. "
            "Dry-run by default; pass --apply to perform actions."
        ),
    )
    p_replay_reconcile.add_argument(
        "--apply",
        action="store_true",
        default=False,
        help="Apply changes (default is dry-run).",
    )
    p_replay_reconcile.add_argument(
        "--max-jobs",
        type=int,
        default=1,
        help="Max replay indexing jobs to run per invocation (default: 1).",
    )
    p_replay_reconcile.add_argument(
        "--job-id",
        nargs="+",
        type=int,
        help="Optional allowlist of ArchiveJob IDs to consider.",
    )
    p_replay_reconcile.add_argument(
        "--sources",
        nargs="+",
        help="Optional allowlist of source codes to consider (e.g. hc phac).",
    )
    p_replay_reconcile.add_argument(
        "--campaign-year",
        type=int,
        help="Optional filter: only jobs with config campaign_kind=annual and campaign_year=YYYY.",
    )
    p_replay_reconcile.add_argument(
        "--verify-warc-hash",
        action="store_true",
        default=False,
        help=(
            "Verify replay-index.meta.json WARC hash/count for existing collections "
            "and reindex when mismatched (more expensive)."
        ),
    )
    p_replay_reconcile.add_argument(
        "--container",
        default="healtharchive-replay",
        help="Docker container name for the replay service.",
    )
    p_replay_reconcile.add_argument(
        "--collections-dir",
        default="/srv/healtharchive/replay/collections",
        help="Host path to the pywb collections directory.",
    )
    p_replay_reconcile.add_argument(
        "--warcs-host-root",
        default="/srv/healtharchive/jobs",
        help="Host path that contains WARCs and is mounted into the replay container.",
    )
    p_replay_reconcile.add_argument(
        "--warcs-container-root",
        default="/warcs",
        help="Container path where --warcs-host-root is mounted.",
    )
    p_replay_reconcile.add_argument(
        "--lock-file",
        default="/srv/healtharchive/replay/.locks/replay-reconcile.lock",
        help="Lock file path used to prevent concurrent reconciliation runs.",
    )
    p_replay_reconcile.add_argument(
        "--previews",
        action="store_true",
        default=False,
        help="Also reconcile cached /archive source preview images.",
    )
    p_replay_reconcile.add_argument(
        "--max-previews",
        type=int,
        default=1,
        help="Max previews to generate per invocation when --previews is enabled (default: 1).",
    )
    p_replay_reconcile.set_defaults(func=cmd_replay_reconcile)

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
            "Attach an ArchiveJob row to an existing archive_tool output directory (advanced/dev)."
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
