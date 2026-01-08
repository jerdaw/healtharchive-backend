#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import stat
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from ha_backend.crawl_stats import parse_crawl_log_progress
from ha_backend.db import get_session
from ha_backend.models import ArchiveJob, Source


@dataclass(frozen=True)
class RunningJob:
    job_id: int
    source_code: str
    started_at: datetime | None
    output_dir: str | None
    combined_log_path: str | None


def _dt_to_epoch_seconds(dt: datetime) -> int:
    return int(dt.astimezone(timezone.utc).timestamp())


def _age_seconds(dt: datetime, *, now_utc: datetime) -> float:
    return max(0.0, (now_utc - dt.astimezone(timezone.utc)).total_seconds())


def _find_latest_combined_log(output_dir: Path) -> Path | None:
    try:
        st = output_dir.stat()
    except OSError:
        return None
    if not stat.S_ISDIR(st.st_mode):
        return None
    try:
        candidates = list(output_dir.glob("archive_*.combined.log"))
    except OSError:
        return None
    if not candidates:
        return None

    latest: Path | None = None
    latest_mtime: float | None = None
    for p in candidates:
        try:
            st = p.stat()
        except OSError:
            continue
        if latest_mtime is None or st.st_mtime > latest_mtime:
            latest = p
            latest_mtime = st.st_mtime
    return latest


def _find_job_log(job: RunningJob) -> Path | None:
    if job.combined_log_path:
        p = Path(job.combined_log_path)
        try:
            if p.is_file():
                return p
        except OSError:
            # Treat as best-effort and fall back to output_dir globbing.
            pass
    if job.output_dir:
        return _find_latest_combined_log(Path(job.output_dir))
    return None


def _emit(lines: list[str], line: str) -> None:
    lines.append(line.rstrip("\n"))


def _probe_readable_dir(path: Path) -> tuple[int, int]:
    """
    Return (ok, errno) where:
      ok=1 means "exists, is a dir, and is readable"
      errno=-1 means "ok", otherwise best-effort OSError errno (or 0 for non-error non-ok states).
    """
    try:
        st = path.stat()
    except OSError as exc:
        return 0, int(exc.errno or -1)
    if not stat.S_ISDIR(st.st_mode):
        return 0, 0
    try:
        os.listdir(path)
    except OSError as exc:
        return 0, int(exc.errno or -1)
    return 1, -1


def _probe_readable_file(path: Path) -> tuple[int, int]:
    """
    Return (ok, errno) where:
      ok=1 means "exists and is a regular file"
      errno=-1 means "ok", otherwise best-effort OSError errno (or 0 for non-error non-ok states).
    """
    try:
        st = path.stat()
    except OSError as exc:
        return 0, int(exc.errno or -1)
    if not stat.S_ISREG(st.st_mode):
        return 0, 0
    return 1, -1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "HealthArchive VPS helper: write crawl progress/stall metrics via node_exporter textfile collector."
        )
    )
    parser.add_argument(
        "--out-dir",
        default="/var/lib/node_exporter/textfile_collector",
        help="node_exporter textfile collector directory.",
    )
    parser.add_argument(
        "--out-file",
        default="healtharchive_crawl.prom",
        help="Output filename under --out-dir.",
    )
    parser.add_argument(
        "--stall-threshold-seconds",
        type=int,
        default=3600,
        help="Mark a running job as stalled if no crawled-count increase for this long.",
    )
    parser.add_argument(
        "--max-log-bytes",
        type=int,
        default=1024 * 1024,
        help="Max bytes to read from the tail of each combined log.",
    )
    args = parser.parse_args(argv)

    out_dir = Path(args.out_dir)
    out_file = out_dir / str(args.out_file)
    now = datetime.now(timezone.utc)

    metrics_ok = 1
    jobs: list[tuple[RunningJob, str]] = []
    try:
        with get_session() as session:
            rows = (
                session.query(
                    ArchiveJob.id,
                    Source.code,
                    ArchiveJob.started_at,
                    ArchiveJob.output_dir,
                    ArchiveJob.combined_log_path,
                )
                .join(Source, ArchiveJob.source_id == Source.id)
                .filter(ArchiveJob.status == "running")
                .order_by(ArchiveJob.id.asc())
                .all()
            )
            jobs = [
                (
                    RunningJob(
                        job_id=int(job_id),
                        source_code=str(source_code),
                        started_at=started_at,
                        output_dir=str(output_dir) if output_dir is not None else None,
                        combined_log_path=str(combined_log_path) if combined_log_path else None,
                    ),
                    str(source_code),
                )
                for job_id, source_code, started_at, output_dir, combined_log_path in rows
            ]
    except Exception:
        metrics_ok = 0
        jobs = []

    lines: list[str] = []
    _emit(
        lines,
        "# HELP healtharchive_crawl_metrics_ok 1 if the crawl metrics script ran successfully.",
    )
    _emit(lines, "# TYPE healtharchive_crawl_metrics_ok gauge")
    _emit(lines, f"healtharchive_crawl_metrics_ok {metrics_ok}")
    _emit(
        lines,
        "# HELP healtharchive_crawl_metrics_timestamp_seconds UNIX timestamp when these metrics were generated.",
    )
    _emit(lines, "# TYPE healtharchive_crawl_metrics_timestamp_seconds gauge")
    _emit(lines, f"healtharchive_crawl_metrics_timestamp_seconds {_dt_to_epoch_seconds(now)}")

    _emit(
        lines, "# HELP healtharchive_crawl_running_jobs Number of jobs currently in status=running."
    )
    _emit(lines, "# TYPE healtharchive_crawl_running_jobs gauge")
    _emit(lines, f"healtharchive_crawl_running_jobs {len(jobs)}")

    _emit(
        lines,
        "# HELP healtharchive_crawl_running_job_progress_known 1 if crawlStatus stats were parsed for the job.",
    )
    _emit(lines, "# TYPE healtharchive_crawl_running_job_progress_known gauge")
    _emit(
        lines,
        "# HELP healtharchive_crawl_running_job_started_age_seconds Seconds since job started_at (DB).",
    )
    _emit(lines, "# TYPE healtharchive_crawl_running_job_started_age_seconds gauge")
    _emit(
        lines,
        "# HELP healtharchive_crawl_running_job_last_progress_age_seconds Seconds since last crawled-count increase (from logs).",
    )
    _emit(lines, "# TYPE healtharchive_crawl_running_job_last_progress_age_seconds gauge")
    _emit(
        lines,
        "# HELP healtharchive_crawl_running_job_stalled 1 if last progress age exceeds the configured stall threshold.",
    )
    _emit(lines, "# TYPE healtharchive_crawl_running_job_stalled gauge")
    _emit(
        lines,
        "# HELP healtharchive_crawl_running_job_output_dir_ok 1 if the job output_dir is readable on disk.",
    )
    _emit(lines, "# TYPE healtharchive_crawl_running_job_output_dir_ok gauge")
    _emit(
        lines,
        "# HELP healtharchive_crawl_running_job_output_dir_errno Errno observed when probing output_dir, or -1 when OK.",
    )
    _emit(lines, "# TYPE healtharchive_crawl_running_job_output_dir_errno gauge")
    _emit(
        lines,
        "# HELP healtharchive_crawl_running_job_log_probe_ok 1 if a combined log was found and is readable.",
    )
    _emit(lines, "# TYPE healtharchive_crawl_running_job_log_probe_ok gauge")
    _emit(
        lines,
        "# HELP healtharchive_crawl_running_job_log_probe_errno Errno observed when probing combined logs, or -1 when OK.",
    )
    _emit(lines, "# TYPE healtharchive_crawl_running_job_log_probe_errno gauge")

    for job, source_code in jobs:
        labels = f'job_id="{int(job.job_id)}",source="{source_code}"'

        # started_at is optional but useful for ops context.
        if job.started_at is not None:
            _emit(
                lines,
                f"healtharchive_crawl_running_job_started_age_seconds{{{labels}}} {_age_seconds(job.started_at, now_utc=now):.0f}",
            )

        output_dir_ok = 0
        output_dir_errno = 0
        if job.output_dir:
            output_dir_ok, output_dir_errno = _probe_readable_dir(Path(job.output_dir))

        log_probe_ok = 0
        log_probe_errno = 0
        log_path: Path | None = None
        if job.combined_log_path:
            log_candidate = Path(job.combined_log_path)
            log_probe_ok, log_probe_errno = _probe_readable_file(log_candidate)
            if log_probe_ok:
                log_path = log_candidate
        if log_path is None:
            # Fall back to best-effort log discovery under output_dir.
            # This is expected to be missing early in a crawl; treat "not found" as errno=0.
            try:
                log_path = _find_job_log(job)
            except OSError as exc:
                log_path = None
                log_probe_ok = 0
                log_probe_errno = int(exc.errno or -1)
            if log_path is not None:
                log_probe_ok = 1
                log_probe_errno = -1

        progress_known = 0
        age_seconds = -1.0
        stalled = 0
        if log_path is not None:
            try:
                progress = parse_crawl_log_progress(log_path, max_bytes=int(args.max_log_bytes))
            except OSError as exc:
                progress = None
                log_probe_ok = 0
                log_probe_errno = int(exc.errno or -1)
            except Exception:
                progress = None
                log_probe_ok = 0
                log_probe_errno = -1
            else:
                if progress is not None:
                    progress_known = 1
                    age_seconds = progress.last_progress_age_seconds(now_utc=now)
                    stalled = 1 if age_seconds >= float(args.stall_threshold_seconds) else 0

        _emit(lines, f"healtharchive_crawl_running_job_progress_known{{{labels}}} {progress_known}")
        _emit(
            lines,
            f"healtharchive_crawl_running_job_last_progress_age_seconds{{{labels}}} {age_seconds:.0f}",
        )
        _emit(lines, f"healtharchive_crawl_running_job_stalled{{{labels}}} {stalled}")
        _emit(lines, f"healtharchive_crawl_running_job_output_dir_ok{{{labels}}} {output_dir_ok}")
        _emit(
            lines,
            f"healtharchive_crawl_running_job_output_dir_errno{{{labels}}} {output_dir_errno}",
        )
        _emit(lines, f"healtharchive_crawl_running_job_log_probe_ok{{{labels}}} {log_probe_ok}")
        _emit(
            lines, f"healtharchive_crawl_running_job_log_probe_errno{{{labels}}} {log_probe_errno}"
        )

    out_dir.mkdir(parents=True, exist_ok=True)
    tmp = out_file.with_suffix(out_file.suffix + f".{os.getpid()}.tmp")
    tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
    tmp.chmod(0o644)
    tmp.replace(out_file)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
