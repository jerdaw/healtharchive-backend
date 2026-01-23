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


def _ensure_recovery_tool_options(job: ArchiveJob) -> bool:
    """
    Ensure self-healing crawl options are present on a job before a recovery retry.

    This is intentionally conservative:
    - enables monitoring (required for adaptive restart)
    - enables adaptive restart (container restart on stall)
    - ensures annual jobs have non-trivial restart + monitoring thresholds
    - preserves any existing explicit operator overrides

    Returns True if the job config was modified.
    """
    cfg = dict(job.config or {})
    tool = dict(cfg.get("tool_options") or {})

    changed = False

    campaign_kind = str(cfg.get("campaign_kind") or "").strip().lower()
    is_annual = campaign_kind == "annual"

    # Required for any monitor-driven recovery strategies.
    if not bool(tool.get("enable_monitoring", False)):
        tool["enable_monitoring"] = True
        changed = True

    if not bool(tool.get("enable_adaptive_restart", False)):
        tool["enable_adaptive_restart"] = True
        changed = True

    try:
        max_restarts = int(tool.get("max_container_restarts") or 0)
    except (TypeError, ValueError):
        max_restarts = 0
    min_restarts = 20 if is_annual else 6
    if max_restarts < min_restarts:
        # Long annual crawls can exhaust a tiny restart budget early, leading to
        # repeated manual intervention. When recovering, bump low/missing values
        # to a non-trivial minimum.
        tool["max_container_restarts"] = min_restarts
        changed = True

    if is_annual:
        # For annual jobs, prefer tolerating "noisy but progressing" sites to
        # avoid thrashing (restart loops + long idle backoffs).
        if tool.get("skip_final_build") is None:
            tool["skip_final_build"] = True
            changed = True
        if tool.get("docker_shm_size") is None:
            tool["docker_shm_size"] = "1g"
            changed = True
        if tool.get("stall_timeout_minutes") is None:
            tool["stall_timeout_minutes"] = 60
            changed = True
        if tool.get("error_threshold_timeout") is None:
            tool["error_threshold_timeout"] = 50
            changed = True
        if tool.get("error_threshold_http") is None:
            tool["error_threshold_http"] = 50
            changed = True
        if tool.get("backoff_delay_minutes") is None:
            tool["backoff_delay_minutes"] = 2
            changed = True

    if changed:
        cfg["tool_options"] = tool
        job.config = cfg
    return changed


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
        "--skip-if-any-job-progress-within-seconds",
        type=int,
        default=600,
        help=(
            "Safety: if any other running job has made progress within this window, skip recovery "
            "to avoid interrupting a healthy crawl when the worker is stopped/restarted."
        ),
    )
    parser.add_argument(
        "--soft-recover-when-guarded",
        dest="soft_recover_when_guarded",
        action="store_true",
        default=True,
        help=(
            "If another running job has made progress within the guard window, avoid stopping/restarting "
            "the worker but still mark the stalled job retryable. This cleans up zombie 'running' jobs "
            "without interrupting a healthy crawl."
        ),
    )
    parser.add_argument(
        "--no-soft-recover-when-guarded",
        dest="soft_recover_when_guarded",
        action="store_false",
        help="Disable soft recovery when another job is progressing.",
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
        default=3,
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
    except Exception as exc:
        msg = str(exc)
        if "no such table" in msg and "archive_jobs" in msg:
            print("ERROR: database schema is missing required tables (archive_jobs).")
            print("Hint: load the backend env so HEALTHARCHIVE_DATABASE_URL points at the real DB:")
            print("  sudo bash -lc 'set -a; source /etc/healtharchive/backend.env; set +a; ...'")
            return 2
        raise

    stalled: list[tuple[RunningJob, float]] = []
    progress_age_by_job_id: dict[int, float] = {}
    for job in running_jobs:
        log_path = _find_job_log(job)
        if log_path is None:
            continue
        progress = parse_crawl_log_progress(log_path)
        if progress is None:
            continue
        age = progress.last_progress_age_seconds(now_utc=now)
        progress_age_by_job_id[int(job.job_id)] = float(age)
        if age >= float(args.stall_threshold_seconds):
            stalled.append((job, age))

    if not stalled:
        return 0

    # Only recover one job per run (worker processes one job at a time).
    job, age = stalled[0]
    guard_seconds = int(args.skip_if_any_job_progress_within_seconds or 0)
    if guard_seconds > 0:
        other_recent = [
            (jid, a)
            for jid, a in progress_age_by_job_id.items()
            if jid != int(job.job_id) and a < float(guard_seconds)
        ]
        if other_recent:
            jid, a = sorted(other_recent, key=lambda item: item[1])[0]
            if not bool(args.soft_recover_when_guarded):
                print(
                    f"SKIP job_id={job.job_id} source={job.source_code}: stalled for {age:.0f}s, "
                    f"but another running job_id={jid} made progress {a:.0f}s ago (<{guard_seconds}s guard)."
                )
                return 0
            print(
                f"{'APPLY' if args.apply else 'DRY-RUN'}: soft-recover stalled job_id={job.job_id} "
                f"source={job.source_code} stalled_age_seconds={age:.0f} "
                f"(another job_id={jid} made progress {a:.0f}s ago; not restarting worker)"
            )
            if not args.apply:
                return 0

            recent_n = _count_recent_recoveries(state, job.job_id, since_utc=recent_cutoff)
            if recent_n >= int(args.max_recoveries_per_job_per_day):
                print(
                    f"SKIP job_id={job.job_id} source={job.source_code}: stalled for {age:.0f}s, "
                    f"but max recoveries reached ({recent_n}/{args.max_recoveries_per_job_per_day} in last 24h)."
                )
                return 0

            # Soft-recover: mark stale running jobs retryable without stopping the worker.
            # This avoids interrupting a healthy crawl (single-worker host), while cleaning up
            # zombie 'running' jobs so they can be retried later.
            try:
                with get_session() as session:
                    orm_job = session.get(ArchiveJob, job.job_id)
                    if orm_job is not None:
                        if _ensure_recovery_tool_options(orm_job):
                            tool_opts = (orm_job.config or {}).get("tool_options") or {}
                            try:
                                max_restarts = int(tool_opts.get("max_container_restarts") or 0)
                            except (TypeError, ValueError):
                                max_restarts = 0
                            print(
                                "Updated job config before soft recovery: "
                                f"enable_adaptive_restart={bool(tool_opts.get('enable_adaptive_restart'))} "
                                f"max_container_restarts={max_restarts}"
                            )
            except Exception as exc:
                print(f"WARNING: failed to update job config before soft recovery: {exc}")

            _run(
                [
                    "/opt/healtharchive-backend/.venv/bin/ha-backend",
                    "recover-stale-jobs",
                    "--older-than-minutes",
                    str(int(args.recover_older_than_minutes)),
                    "--require-no-progress-seconds",
                    str(int(args.stall_threshold_seconds)),
                    "--apply",
                    "--source",
                    job.source_code,
                    "--limit",
                    "5",
                ]
            )

            _record_recovery(state, job.job_id, when_utc=now)
            _save_state(state_path, state)
            return 0

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

    # Before restarting the worker, ensure the retried job has the key self-healing options.
    try:
        with get_session() as session:
            orm_job = session.get(ArchiveJob, job.job_id)
            if orm_job is not None:
                if _ensure_recovery_tool_options(orm_job):
                    session.commit()
                    tool_opts = (orm_job.config or {}).get("tool_options") or {}
                    try:
                        max_restarts = int(tool_opts.get("max_container_restarts") or 0)
                    except (TypeError, ValueError):
                        max_restarts = 0
                    print(
                        "Updated job config before recovery: "
                        f"enable_adaptive_restart={bool(tool_opts.get('enable_adaptive_restart'))} "
                        f"max_container_restarts={max_restarts}"
                    )
    except Exception as exc:
        print(f"WARNING: failed to update job config before recovery: {exc}")

    _run(["systemctl", "stop", "healtharchive-worker.service"])
    _run(
        [
            "/opt/healtharchive-backend/.venv/bin/ha-backend",
            "recover-stale-jobs",
            "--older-than-minutes",
            str(int(args.recover_older_than_minutes)),
            "--require-no-progress-seconds",
            str(int(args.stall_threshold_seconds)),
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
