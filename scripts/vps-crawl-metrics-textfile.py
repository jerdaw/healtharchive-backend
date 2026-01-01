#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
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
    if not output_dir.is_dir():
        return None
    candidates = list(output_dir.glob("archive_*.combined.log"))
    if not candidates:
        return None
    return max(candidates, key=lambda p: p.stat().st_mtime)


def _find_job_log(job: RunningJob) -> Path | None:
    if job.combined_log_path:
        p = Path(job.combined_log_path)
        if p.is_file():
            return p
    if job.output_dir:
        return _find_latest_combined_log(Path(job.output_dir))
    return None


def _emit(lines: list[str], line: str) -> None:
    lines.append(line.rstrip("\n"))


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

    for job, source_code in jobs:
        labels = f'job_id="{int(job.job_id)}",source="{source_code}"'

        # started_at is optional but useful for ops context.
        if job.started_at is not None:
            _emit(
                lines,
                f"healtharchive_crawl_running_job_started_age_seconds{{{labels}}} {_age_seconds(job.started_at, now_utc=now):.0f}",
            )

        log_path = _find_job_log(job)
        progress_known = 0
        age_seconds = -1.0
        stalled = 0
        if log_path is not None:
            progress = parse_crawl_log_progress(log_path, max_bytes=int(args.max_log_bytes))
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

    out_dir.mkdir(parents=True, exist_ok=True)
    tmp = out_file.with_suffix(out_file.suffix + f".{os.getpid()}.tmp")
    tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
    tmp.chmod(0o644)
    tmp.replace(out_file)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
