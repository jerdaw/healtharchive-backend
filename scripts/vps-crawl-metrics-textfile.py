#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import stat
import subprocess
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from archive_tool.constants import STATE_FILE_NAME
from ha_backend.crawl_stats import (
    count_new_crawl_phase_events_from_log_tail,
    parse_crawl_log_progress,
)
from ha_backend.db import get_session
from ha_backend.models import ArchiveJob, Source


@dataclass(frozen=True)
class RunningJob:
    job_id: int
    source_code: str
    started_at: datetime | None
    output_dir: str | None
    combined_log_path: str | None


@dataclass(frozen=True)
class PendingIndexJob:
    job_id: int
    source_code: str
    finished_at: datetime | None


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
    """
    Find the most relevant combined log file for a running job.

    Important: running jobs often have a stale `combined_log_path` (last finished run),
    so we prefer the newest log on disk under `output_dir` when available.
    """
    by_path: Path | None = None
    by_output: Path | None = None

    if job.combined_log_path:
        p = Path(job.combined_log_path)
        try:
            if p.is_file():
                by_path = p
        except OSError:
            by_path = None

    if job.output_dir:
        by_output = _find_latest_combined_log(Path(job.output_dir))

    if by_path is None:
        return by_output
    if by_output is None:
        return by_path

    try:
        return by_output if by_output.stat().st_mtime >= by_path.stat().st_mtime else by_path
    except OSError:
        # Fall back to the output-dir candidate; it's usually the freshest signal for running jobs.
        return by_output or by_path


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


def _systemctl_is_active(unit: str) -> int:
    try:
        r = subprocess.run(  # nosec: B603
            ["systemctl", "is-active", unit],
            check=False,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError:
        return 0
    return 1 if r.stdout.strip() == "active" else 0


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
    parser.add_argument(
        "--worker-unit",
        default="healtharchive-worker.service",
        help="Worker systemd unit name (used only for metrics).",
    )
    parser.add_argument(
        "--storagebox-mount",
        default="/srv/healtharchive/storagebox",
        help="Storage Box mountpoint on the VPS (used only for metrics).",
    )
    parser.add_argument(
        "--infra-error-window-minutes",
        type=int,
        default=10,
        help="Window for 'recent infra_error jobs' metrics (default: 10 minutes).",
    )
    args = parser.parse_args(argv)

    out_dir = Path(args.out_dir)
    out_file = out_dir / str(args.out_file)
    now = datetime.now(timezone.utc)

    metrics_ok = 1
    jobs: list[tuple[RunningJob, str]] = []
    pending_index_jobs: list[PendingIndexJob] = []
    pending_crawl_jobs = 0
    recent_infra_error_jobs = 0
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

            pending_rows = (
                session.query(ArchiveJob.id, Source.code, ArchiveJob.finished_at)
                .join(Source, ArchiveJob.source_id == Source.id)
                .filter(ArchiveJob.status == "completed")
                .order_by(ArchiveJob.finished_at.asc().nullsfirst(), ArchiveJob.id.asc())
                .all()
            )
            pending_index_jobs = [
                PendingIndexJob(
                    job_id=int(job_id),
                    source_code=str(source_code),
                    finished_at=finished_at,
                )
                for job_id, source_code, finished_at in pending_rows
            ]

            pending_crawl_jobs = (
                session.query(ArchiveJob)
                .filter(ArchiveJob.status.in_(["queued", "retryable"]))
                .count()
            )

            window_minutes = max(1, int(args.infra_error_window_minutes))
            infra_cutoff = now - timedelta(minutes=window_minutes)
            recent_infra_error_jobs = (
                session.query(ArchiveJob)
                .filter(ArchiveJob.crawler_status == "infra_error")
                .filter(ArchiveJob.updated_at >= infra_cutoff)
                .count()
            )
    except Exception:
        metrics_ok = 0
        jobs = []
        pending_index_jobs = []
        pending_crawl_jobs = 0
        recent_infra_error_jobs = 0

    worker_active = _systemctl_is_active(str(args.worker_unit))
    storagebox_ok, _storagebox_errno = _probe_readable_dir(Path(str(args.storagebox_mount)))
    worker_should_be_running = 1 if (pending_crawl_jobs > 0 and storagebox_ok == 1) else 0

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

    _emit(lines, "# HELP healtharchive_worker_active 1 if the worker systemd unit is active.")
    _emit(lines, "# TYPE healtharchive_worker_active gauge")
    _emit(lines, f"healtharchive_worker_active {worker_active}")

    _emit(
        lines,
        "# HELP healtharchive_jobs_pending_crawl Number of jobs currently in status=queued or retryable.",
    )
    _emit(lines, "# TYPE healtharchive_jobs_pending_crawl gauge")
    _emit(lines, f"healtharchive_jobs_pending_crawl {pending_crawl_jobs}")

    window_minutes = max(1, int(args.infra_error_window_minutes))
    _emit(
        lines,
        "# HELP healtharchive_jobs_infra_error_recent_total Number of jobs with crawler_status=infra_error updated within the recent window.",
    )
    _emit(lines, "# TYPE healtharchive_jobs_infra_error_recent_total gauge")
    _emit(
        lines,
        f'healtharchive_jobs_infra_error_recent_total{{minutes="{window_minutes}"}} {recent_infra_error_jobs}',
    )

    _emit(
        lines,
        "# HELP healtharchive_worker_should_be_running 1 if there are pending crawl jobs and the Storage Box mount is readable.",
    )
    _emit(lines, "# TYPE healtharchive_worker_should_be_running gauge")
    _emit(lines, f"healtharchive_worker_should_be_running {worker_should_be_running}")

    _emit(
        lines,
        "# HELP healtharchive_indexing_pending_jobs Number of jobs currently in status=completed (crawl done, indexing not done).",
    )
    _emit(lines, "# TYPE healtharchive_indexing_pending_jobs gauge")
    _emit(lines, f"healtharchive_indexing_pending_jobs {len(pending_index_jobs)}")

    _emit(
        lines,
        "# HELP healtharchive_indexing_pending_job_max_age_seconds Max age (seconds) since finished_at among status=completed jobs, or 0 when none.",
    )
    _emit(lines, "# TYPE healtharchive_indexing_pending_job_max_age_seconds gauge")

    max_pending_age_seconds = 0.0
    pending_by_source: dict[str, list[PendingIndexJob]] = {}
    for j in pending_index_jobs:
        pending_by_source.setdefault(j.source_code, []).append(j)
        if j.finished_at is not None:
            max_pending_age_seconds = max(
                max_pending_age_seconds, _age_seconds(j.finished_at, now_utc=now)
            )
    _emit(
        lines, f"healtharchive_indexing_pending_job_max_age_seconds {max_pending_age_seconds:.0f}"
    )

    _emit(
        lines,
        "# HELP healtharchive_indexing_pending_jobs_by_source Number of status=completed jobs grouped by source.",
    )
    _emit(lines, "# TYPE healtharchive_indexing_pending_jobs_by_source gauge")
    _emit(
        lines,
        "# HELP healtharchive_indexing_pending_job_max_age_seconds_by_source Max age (seconds) since finished_at for status=completed jobs, by source.",
    )
    _emit(lines, "# TYPE healtharchive_indexing_pending_job_max_age_seconds_by_source gauge")

    for source_code, source_jobs in sorted(pending_by_source.items(), key=lambda kv: kv[0]):
        max_age_source = 0.0
        for j in source_jobs:
            if j.finished_at is not None:
                max_age_source = max(max_age_source, _age_seconds(j.finished_at, now_utc=now))
        labels = f'source="{source_code}"'
        _emit(
            lines, f"healtharchive_indexing_pending_jobs_by_source{{{labels}}} {len(source_jobs)}"
        )
        _emit(
            lines,
            f"healtharchive_indexing_pending_job_max_age_seconds_by_source{{{labels}}} {max_age_source:.0f}",
        )

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

    _emit(
        lines,
        "# HELP healtharchive_crawl_running_job_state_file_ok 1 if .archive_state.json exists and is a regular file.",
    )
    _emit(lines, "# TYPE healtharchive_crawl_running_job_state_file_ok gauge")
    _emit(
        lines,
        "# HELP healtharchive_crawl_running_job_state_file_errno Errno observed when probing .archive_state.json, or -1 when OK.",
    )
    _emit(lines, "# TYPE healtharchive_crawl_running_job_state_file_errno gauge")
    _emit(
        lines,
        "# HELP healtharchive_crawl_running_job_state_parse_ok 1 if .archive_state.json was parsed successfully.",
    )
    _emit(lines, "# TYPE healtharchive_crawl_running_job_state_parse_ok gauge")
    _emit(
        lines,
        "# HELP healtharchive_crawl_running_job_state_mtime_age_seconds Seconds since .archive_state.json mtime, or -1 when unknown.",
    )
    _emit(lines, "# TYPE healtharchive_crawl_running_job_state_mtime_age_seconds gauge")
    _emit(
        lines,
        "# HELP healtharchive_crawl_running_job_current_workers Current worker count from .archive_state.json, or -1 when unknown.",
    )
    _emit(lines, "# TYPE healtharchive_crawl_running_job_current_workers gauge")
    _emit(
        lines,
        "# HELP healtharchive_crawl_running_job_worker_reductions_done Worker reductions done from .archive_state.json, or -1 when unknown.",
    )
    _emit(lines, "# TYPE healtharchive_crawl_running_job_worker_reductions_done gauge")
    _emit(
        lines,
        "# HELP healtharchive_crawl_running_job_container_restarts_done Container restarts done from .archive_state.json, or -1 when unknown.",
    )
    _emit(lines, "# TYPE healtharchive_crawl_running_job_container_restarts_done gauge")
    _emit(
        lines,
        "# HELP healtharchive_crawl_running_job_vpn_rotations_done VPN rotations done from .archive_state.json, or -1 when unknown.",
    )
    _emit(lines, "# TYPE healtharchive_crawl_running_job_vpn_rotations_done gauge")
    _emit(
        lines,
        "# HELP healtharchive_crawl_running_job_temp_dirs_count Temp dir count tracked in .archive_state.json, or -1 when unknown.",
    )
    _emit(lines, "# TYPE healtharchive_crawl_running_job_temp_dirs_count gauge")
    _emit(
        lines,
        "# HELP healtharchive_crawl_running_job_crawl_rate_ppm Crawl rate in pages per minute (from log window), or -1 when unknown.",
    )
    _emit(lines, "# TYPE healtharchive_crawl_running_job_crawl_rate_ppm gauge")
    _emit(
        lines,
        "# HELP healtharchive_crawl_running_job_new_crawl_phase_count Number of 'New Crawl Phase' stage starts seen in the combined log tail window, or -1 when unknown.",
    )
    _emit(lines, "# TYPE healtharchive_crawl_running_job_new_crawl_phase_count gauge")
    _emit(
        lines,
        "# HELP healtharchive_crawl_running_job_errors_timeout Timeout error count from .archive_state.json, or -1 when unknown.",
    )
    _emit(lines, "# TYPE healtharchive_crawl_running_job_errors_timeout gauge")
    _emit(
        lines,
        "# HELP healtharchive_crawl_running_job_errors_http HTTP error count from .archive_state.json, or -1 when unknown.",
    )
    _emit(lines, "# TYPE healtharchive_crawl_running_job_errors_http gauge")
    _emit(
        lines,
        "# HELP healtharchive_crawl_running_job_errors_other Other error count from .archive_state.json, or -1 when unknown.",
    )
    _emit(lines, "# TYPE healtharchive_crawl_running_job_errors_other gauge")

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
        output_dir_path: Path | None = Path(job.output_dir) if job.output_dir else None
        if output_dir_path:
            output_dir_ok, output_dir_errno = _probe_readable_dir(output_dir_path)

        log_probe_ok = 0
        log_probe_errno = 0
        log_path: Path | None = None
        log_candidate: Path | None = None
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

        if (
            output_dir_path
            and output_dir_ok == 0
            and output_dir_errno not in (0, -1)
            and (log_path or log_candidate)
        ):
            target = log_path or log_candidate
            if target and target.is_relative_to(output_dir_path):
                log_probe_ok = 0
                log_probe_errno = output_dir_errno

        progress_known = 0
        age_seconds = -1.0
        stalled = 0
        crawl_rate_ppm = -1.0
        new_crawl_phase_count = -1
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
                    crawl_rate_ppm = progress.crawl_rate_ppm
            new_phase_count = count_new_crawl_phase_events_from_log_tail(
                log_path, max_bytes=int(args.max_log_bytes)
            )
            if new_phase_count is not None:
                new_crawl_phase_count = int(new_phase_count)

        state_file_ok = 0
        state_file_errno = 0
        state_parse_ok = 0
        state_mtime_age_seconds = -1.0
        current_workers = -1
        worker_reductions_done = -1
        container_restarts_done = -1
        vpn_rotations_done = -1
        temp_dirs_count = -1
        errors_timeout = -1
        errors_http = -1
        errors_other = -1

        if output_dir_path and output_dir_ok:
            state_path = output_dir_path / STATE_FILE_NAME
            state_file_ok, state_file_errno = _probe_readable_file(state_path)
            if state_file_ok:
                try:
                    st = state_path.stat()
                except OSError as exc:
                    state_file_ok = 0
                    state_file_errno = int(exc.errno or -1)
                else:
                    state_mtime_age_seconds = max(0.0, now.timestamp() - float(st.st_mtime))
                    try:
                        raw = state_path.read_text(encoding="utf-8")
                        data = json.loads(raw)
                    except Exception:
                        state_parse_ok = 0
                    else:
                        state_parse_ok = 1
                        try:
                            current_workers = int(data.get("current_workers"))
                        except Exception:
                            current_workers = -1
                        try:
                            worker_reductions_done = int(data.get("worker_reductions_done"))
                        except Exception:
                            worker_reductions_done = -1
                        try:
                            container_restarts_done = int(data.get("container_restarts_done"))
                        except Exception:
                            container_restarts_done = -1
                        try:
                            vpn_rotations_done = int(data.get("vpn_rotations_done"))
                        except Exception:
                            vpn_rotations_done = -1
                        temp_dirs = data.get("temp_dirs_host_paths")
                        if isinstance(temp_dirs, list):
                            temp_dirs_count = len(temp_dirs)
                        # Extract error counts for per-error-type visibility
                        error_counts = data.get("error_counts")
                        if isinstance(error_counts, dict):
                            try:
                                errors_timeout = int(error_counts.get("timeout", -1))
                            except (TypeError, ValueError):
                                errors_timeout = -1
                            try:
                                errors_http = int(error_counts.get("http", -1))
                            except (TypeError, ValueError):
                                errors_http = -1
                            try:
                                errors_other = int(error_counts.get("other", -1))
                            except (TypeError, ValueError):
                                errors_other = -1

        _emit(lines, f"healtharchive_crawl_running_job_progress_known{{{labels}}} {progress_known}")
        _emit(
            lines,
            f"healtharchive_crawl_running_job_last_progress_age_seconds{{{labels}}} {age_seconds:.0f}",
        )
        _emit(lines, f"healtharchive_crawl_running_job_stalled{{{labels}}} {stalled}")
        _emit(
            lines,
            f"healtharchive_crawl_running_job_crawl_rate_ppm{{{labels}}} {crawl_rate_ppm:.1f}",
        )
        _emit(
            lines,
            f"healtharchive_crawl_running_job_new_crawl_phase_count{{{labels}}} {new_crawl_phase_count}",
        )
        _emit(lines, f"healtharchive_crawl_running_job_output_dir_ok{{{labels}}} {output_dir_ok}")
        _emit(
            lines,
            f"healtharchive_crawl_running_job_output_dir_errno{{{labels}}} {output_dir_errno}",
        )
        _emit(lines, f"healtharchive_crawl_running_job_log_probe_ok{{{labels}}} {log_probe_ok}")
        _emit(
            lines, f"healtharchive_crawl_running_job_log_probe_errno{{{labels}}} {log_probe_errno}"
        )
        _emit(lines, f"healtharchive_crawl_running_job_state_file_ok{{{labels}}} {state_file_ok}")
        _emit(
            lines,
            f"healtharchive_crawl_running_job_state_file_errno{{{labels}}} {state_file_errno}",
        )
        _emit(lines, f"healtharchive_crawl_running_job_state_parse_ok{{{labels}}} {state_parse_ok}")
        _emit(
            lines,
            f"healtharchive_crawl_running_job_state_mtime_age_seconds{{{labels}}} {state_mtime_age_seconds:.0f}",
        )
        _emit(
            lines, f"healtharchive_crawl_running_job_current_workers{{{labels}}} {current_workers}"
        )
        _emit(
            lines,
            f"healtharchive_crawl_running_job_worker_reductions_done{{{labels}}} {worker_reductions_done}",
        )
        _emit(
            lines,
            f"healtharchive_crawl_running_job_container_restarts_done{{{labels}}} {container_restarts_done}",
        )
        _emit(
            lines,
            f"healtharchive_crawl_running_job_vpn_rotations_done{{{labels}}} {vpn_rotations_done}",
        )
        _emit(
            lines,
            f"healtharchive_crawl_running_job_temp_dirs_count{{{labels}}} {temp_dirs_count}",
        )
        _emit(
            lines,
            f"healtharchive_crawl_running_job_errors_timeout{{{labels}}} {errors_timeout}",
        )
        _emit(
            lines,
            f"healtharchive_crawl_running_job_errors_http{{{labels}}} {errors_http}",
        )
        _emit(
            lines,
            f"healtharchive_crawl_running_job_errors_other{{{labels}}} {errors_other}",
        )

    out_dir.mkdir(parents=True, exist_ok=True)
    tmp = out_file.with_suffix(out_file.suffix + f".{os.getpid()}.tmp")
    tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
    tmp.chmod(0o644)
    tmp.replace(out_file)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
