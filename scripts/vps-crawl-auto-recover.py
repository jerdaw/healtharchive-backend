#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
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


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _load_state(path: Path) -> dict:
    if not path.is_file():
        return {"recoveries": {}}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {"recoveries": {}}


def _save_state(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(path)


def _record_recovery(state: dict, job_id: int, *, when_utc: datetime) -> None:
    recoveries = state.setdefault("recoveries", {})
    items = list(recoveries.get(str(job_id)) or [])
    items.append(when_utc.replace(microsecond=0).isoformat())
    recoveries[str(job_id)] = items


def _count_recent_recoveries(state: dict, job_id: int, *, since_utc: datetime) -> int:
    recoveries = state.get("recoveries", {})
    items = list(recoveries.get(str(job_id)) or [])
    n = 0
    for raw in items:
        try:
            ts = datetime.fromisoformat(str(raw))
        except ValueError:
            continue
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        if ts >= since_utc:
            n += 1
    return n


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


def _run(cmd: list[str]) -> None:
    subprocess.run(cmd, check=True)  # nosec: B603


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "HealthArchive VPS helper: auto-recover stalled running crawl jobs "
            "by restarting the worker and marking stale jobs retryable."
        )
    )
    parser.add_argument(
        "--stall-threshold-seconds",
        type=int,
        default=5400,
        help="Consider a running job stalled if no crawled-count increase for this long.",
    )
    parser.add_argument(
        "--recover-older-than-minutes",
        type=int,
        default=5,
        help="Pass-through to ha-backend recover-stale-jobs --older-than-minutes.",
    )
    parser.add_argument(
        "--max-recoveries-per-job-per-day",
        type=int,
        default=2,
        help="Safety cap to avoid restart loops.",
    )
    parser.add_argument(
        "--state-file",
        default="/srv/healtharchive/ops/watchdog/crawl-auto-recover.json",
        help="Where to store watchdog recovery history.",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        default=False,
        help="Apply recovery actions (default is dry-run).",
    )
    args = parser.parse_args(argv)

    now = _utc_now()
    state_path = Path(args.state_file)
    state = _load_state(state_path)
    recent_cutoff = now - timedelta(days=1)

    running_jobs: list[RunningJob] = []
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
        running_jobs = [
            RunningJob(
                job_id=int(job_id),
                source_code=str(source_code),
                started_at=started_at,
                output_dir=str(output_dir) if output_dir is not None else None,
                combined_log_path=str(combined_log_path) if combined_log_path else None,
            )
            for job_id, source_code, started_at, output_dir, combined_log_path in rows
        ]

    stalled: list[tuple[RunningJob, float]] = []
    for job in running_jobs:
        log_path = _find_job_log(job)
        if log_path is None:
            continue
        progress = parse_crawl_log_progress(log_path)
        if progress is None:
            continue
        age = progress.last_progress_age_seconds(now_utc=now)
        if age >= float(args.stall_threshold_seconds):
            stalled.append((job, age))

    if not stalled:
        return 0

    # Only recover one job per run (worker processes one job at a time).
    job, age = stalled[0]
    recent_n = _count_recent_recoveries(state, job.job_id, since_utc=recent_cutoff)
    if recent_n >= int(args.max_recoveries_per_job_per_day):
        print(
            f"SKIP job_id={job.job_id} source={job.source_code}: stalled for {age:.0f}s, "
            f"but max recoveries reached ({recent_n}/{args.max_recoveries_per_job_per_day} in last 24h)."
        )
        return 0

    print(
        f"{'APPLY' if args.apply else 'DRY-RUN'}: would recover stalled job_id={job.job_id} "
        f"source={job.source_code} stalled_age_seconds={age:.0f}"
    )
    if not args.apply:
        return 0

    _run(["systemctl", "stop", "healtharchive-worker.service"])
    _run(
        [
            "/opt/healtharchive-backend/.venv/bin/ha-backend",
            "recover-stale-jobs",
            "--older-than-minutes",
            str(int(args.recover_older_than_minutes)),
            "--apply",
            "--source",
            job.source_code,
            "--limit",
            "5",
        ]
    )
    _run(["systemctl", "start", "healtharchive-worker.service"])

    _record_recovery(state, job.job_id, when_utc=now)
    _save_state(state_path, state)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
