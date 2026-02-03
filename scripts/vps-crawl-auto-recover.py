#!/usr/bin/env python3
from __future__ import annotations

import argparse
import fcntl
import json
import os
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ha_backend.crawl_stats import parse_crawl_log_progress
from ha_backend.db import get_session
from ha_backend.models import ArchiveJob, Source

DEFAULT_DEPLOY_LOCK_FILE = "/tmp/healtharchive-backend-deploy.lock"
DEFAULT_STATE_FILE = "/srv/healtharchive/ops/watchdog/crawl-auto-recover.json"
DEFAULT_LOCK_FILE = "/srv/healtharchive/ops/watchdog/crawl-auto-recover.lock"
DEFAULT_TEXTFILE_OUT_DIR = "/var/lib/node_exporter/textfile_collector"
DEFAULT_TEXTFILE_OUT_FILE = "healtharchive_crawl_auto_recover.prom"


def _dt_to_epoch_seconds(dt: datetime) -> int:
    return int(dt.astimezone(timezone.utc).timestamp())


def _file_age_seconds(path: Path, *, now_utc: datetime) -> float | None:
    try:
        st = path.stat()
    except FileNotFoundError:
        return None
    except OSError:
        return 0.0
    try:
        mtime = datetime.fromtimestamp(st.st_mtime, tz=timezone.utc)
    except (OverflowError, OSError, ValueError):
        return 0.0
    return max(0.0, (now_utc - mtime).total_seconds())


def _deploy_lock_is_active(
    deploy_lock_file: Path,
    *,
    now_utc: datetime,
    deploy_lock_max_age_seconds: float,
) -> tuple[int, float | None]:
    """
    Return (active, age_seconds) for the deploy lock.

    The deploy helper uses `flock` on a persistent file, so the file may exist
    even when no deploy is running. Prefer probing whether the lock is *held*.
    """
    age_seconds = _file_age_seconds(deploy_lock_file, now_utc=now_utc)
    if age_seconds is None:
        return 0, None

    try:
        f = deploy_lock_file.open("rb")
    except OSError:
        return (
            1 if age_seconds <= float(deploy_lock_max_age_seconds) else 0,
            age_seconds,
        )
    try:
        try:
            fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return 1, age_seconds
        except OSError:
            return (
                1 if age_seconds <= float(deploy_lock_max_age_seconds) else 0,
                age_seconds,
            )
        else:
            try:
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)
            except OSError:
                pass
            return 0, age_seconds
    finally:
        try:
            f.close()
        except Exception:
            pass


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
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(json.dumps(state, indent=2, sort_keys=True) + "\n")
        f.flush()
        os.fsync(f.fileno())
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
    try:
        if not output_dir.is_dir():
            return None
    except OSError:
        return None
    try:
        candidates = list(output_dir.glob("archive_*.combined.log"))
    except OSError:
        return None
    if not candidates:
        return None

    def _safe_mtime(p: Path) -> float:
        try:
            return p.stat().st_mtime
        except OSError:
            return 0.0

    return max(candidates, key=_safe_mtime)


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


def _write_textfile_metrics(
    *,
    out_dir: Path,
    out_file: str,
    now_utc: datetime,
    state: dict,
    enabled: int,
    running_jobs: int,
    stalled_jobs: int,
    deploy_lock_present: int,
    result: str,
    reason: str,
) -> None:
    """Write prometheus textfile metrics for observability."""
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / out_file
    tmp = path.with_suffix(path.suffix + f".{os.getpid()}.tmp")

    lines: list[str] = []

    lines.append(
        "# HELP healtharchive_crawl_auto_recover_metrics_ok 1 if the crawl auto-recover watchdog ran to completion."
    )
    lines.append("# TYPE healtharchive_crawl_auto_recover_metrics_ok gauge")
    lines.append("healtharchive_crawl_auto_recover_metrics_ok 1")

    lines.append(
        "# HELP healtharchive_crawl_auto_recover_last_run_timestamp_seconds UNIX timestamp of the last watchdog run."
    )
    lines.append("# TYPE healtharchive_crawl_auto_recover_last_run_timestamp_seconds gauge")
    lines.append(
        f"healtharchive_crawl_auto_recover_last_run_timestamp_seconds {_dt_to_epoch_seconds(now_utc)}"
    )

    lines.append(
        "# HELP healtharchive_crawl_auto_recover_enabled 1 if the sentinel file exists (automation enabled)."
    )
    lines.append("# TYPE healtharchive_crawl_auto_recover_enabled gauge")
    lines.append(f"healtharchive_crawl_auto_recover_enabled {int(enabled)}")

    lines.append(
        "# HELP healtharchive_crawl_auto_recover_running_jobs Number of jobs currently in status=running."
    )
    lines.append("# TYPE healtharchive_crawl_auto_recover_running_jobs gauge")
    lines.append(f"healtharchive_crawl_auto_recover_running_jobs {int(running_jobs)}")

    lines.append(
        "# HELP healtharchive_crawl_auto_recover_stalled_jobs Number of running jobs detected as stalled."
    )
    lines.append("# TYPE healtharchive_crawl_auto_recover_stalled_jobs gauge")
    lines.append(f"healtharchive_crawl_auto_recover_stalled_jobs {int(stalled_jobs)}")

    lines.append(
        "# HELP healtharchive_crawl_auto_recover_deploy_lock_present 1 if deploy lock appears active."
    )
    lines.append("# TYPE healtharchive_crawl_auto_recover_deploy_lock_present gauge")
    lines.append(f"healtharchive_crawl_auto_recover_deploy_lock_present {int(deploy_lock_present)}")

    # Count recent recoveries from state
    recoveries = state.get("recoveries", {})
    total_recoveries = sum(len(v) for v in recoveries.values() if isinstance(v, list))
    lines.append(
        "# HELP healtharchive_crawl_auto_recover_recoveries_total Total number of recoveries recorded."
    )
    lines.append("# TYPE healtharchive_crawl_auto_recover_recoveries_total counter")
    lines.append(f"healtharchive_crawl_auto_recover_recoveries_total {int(total_recoveries)}")

    lines.append(
        "# HELP healtharchive_crawl_auto_recover_last_result 1 for the most recent watchdog outcome."
    )
    lines.append("# TYPE healtharchive_crawl_auto_recover_last_result gauge")
    lines.append(
        f'healtharchive_crawl_auto_recover_last_result{{result="{result}",reason="{reason}"}} 1'
    )

    with open(tmp, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
        f.flush()
        os.fsync(f.fileno())
    tmp.chmod(0o644)
    tmp.replace(path)


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
        default=3600,
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
        default=DEFAULT_STATE_FILE,
        help="Where to store watchdog recovery history.",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        default=False,
        help="Apply recovery actions (default is dry-run).",
    )
    parser.add_argument(
        "--simulate-stalled-job-id",
        action="append",
        default=[],
        help=(
            "DRILL ONLY (dry-run): treat the given job ID as stalled regardless of log progress. "
            "Requires overriding --state-file/--lock-file/--textfile-out-dir/--textfile-out-file "
            "to avoid touching production watchdog state or Prometheus metrics."
        ),
    )
    parser.add_argument(
        "--simulate-stalled-age-seconds",
        type=int,
        default=None,
        help=(
            "DRILL ONLY: when using --simulate-stalled-job-id, treat the simulated job(s) as stalled for "
            "this long. Default: stall_threshold_seconds + 1."
        ),
    )
    parser.add_argument(
        "--sentinel-file",
        default="/etc/healtharchive/crawl-auto-recover-enabled",
        help="Sentinel file that indicates automation is enabled (written by operator).",
    )
    parser.add_argument(
        "--lock-file",
        default=DEFAULT_LOCK_FILE,
        help="Lock file to prevent concurrent runs.",
    )
    parser.add_argument(
        "--deploy-lock-file",
        default=DEFAULT_DEPLOY_LOCK_FILE,
        help="If this file exists (and lock is held), skip the run to avoid flapping during deploys.",
    )
    parser.add_argument(
        "--deploy-lock-max-age-seconds",
        type=float,
        default=2 * 60 * 60,
        help="Treat --deploy-lock-file as stale if older than this; proceed if stale.",
    )
    parser.add_argument(
        "--textfile-out-dir",
        default=DEFAULT_TEXTFILE_OUT_DIR,
        help="node_exporter textfile collector directory.",
    )
    parser.add_argument(
        "--textfile-out-file",
        default=DEFAULT_TEXTFILE_OUT_FILE,
        help="Output filename under --textfile-out-dir.",
    )
    args = parser.parse_args(argv)

    now = _utc_now()
    simulate_job_ids_raw = list(getattr(args, "simulate_stalled_job_id", []) or [])
    simulate_job_ids = [int(x) for x in simulate_job_ids_raw if str(x).strip()]
    simulate_mode = bool(simulate_job_ids)
    if simulate_mode and bool(args.apply):
        print(
            "ERROR: --simulate-stalled-job-id is only allowed in dry-run mode (omit --apply).",
            file=sys.stderr,
        )
        return 2
    if simulate_mode:
        # Hard safety rail: drills should not write production state/metrics or contend for the production lock.
        required_overrides: list[str] = []
        if str(args.state_file) == DEFAULT_STATE_FILE:
            required_overrides.append("--state-file")
        if str(args.lock_file) == DEFAULT_LOCK_FILE:
            required_overrides.append("--lock-file")
        if str(args.textfile_out_dir) == DEFAULT_TEXTFILE_OUT_DIR:
            required_overrides.append("--textfile-out-dir")
        if str(args.textfile_out_file) == DEFAULT_TEXTFILE_OUT_FILE:
            required_overrides.append("--textfile-out-file")
        if required_overrides:
            print(
                "ERROR: drill mode requires overriding these flags to avoid touching production watchdog state/metrics:",
                file=sys.stderr,
            )
            print("  " + " ".join(required_overrides), file=sys.stderr)
            print(
                "Hint: use /tmp paths, for example:",
                file=sys.stderr,
            )
            print(
                "  --state-file /tmp/healtharchive-crawl-auto-recover.drill.state.json "
                "--lock-file /tmp/healtharchive-crawl-auto-recover.drill.lock "
                "--textfile-out-dir /tmp --textfile-out-file healtharchive_crawl_auto_recover.drill.prom",
                file=sys.stderr,
            )
            return 2

    state_path = Path(args.state_file)
    sentinel_file = Path(args.sentinel_file)
    enabled = 1 if sentinel_file.is_file() else 0

    # Metrics helper for early exits
    def write_metrics(
        *,
        running_jobs: int = 0,
        stalled_jobs: int = 0,
        deploy_lock_present: int = 0,
        result: str = "skip",
        reason: str = "unknown",
        state: dict | None = None,
    ) -> None:
        try:
            _write_textfile_metrics(
                out_dir=Path(str(args.textfile_out_dir)),
                out_file=str(args.textfile_out_file),
                now_utc=now,
                state=state or {},
                enabled=enabled,
                running_jobs=running_jobs,
                stalled_jobs=stalled_jobs,
                deploy_lock_present=deploy_lock_present,
                result=result,
                reason=reason,
            )
        except Exception:
            pass

    # Disabled by default unless operator creates the sentinel (drills bypass this).
    if enabled != 1 and not simulate_mode:
        write_metrics(result="skip", reason="disabled")
        return 0

    # Lock file to prevent concurrent runs
    lock_path = Path(args.lock_file)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_f = lock_path.open("a", encoding="utf-8")
    try:
        fcntl.flock(lock_f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        # Another instance is running
        return 0

    # Deploy lock check (drills bypass this).
    deploy_lock_file = Path(str(args.deploy_lock_file))
    deploy_lock_present, _deploy_lock_age_seconds = _deploy_lock_is_active(
        deploy_lock_file,
        now_utc=now,
        deploy_lock_max_age_seconds=float(args.deploy_lock_max_age_seconds),
    )
    if deploy_lock_present == 1 and not simulate_mode:
        write_metrics(deploy_lock_present=1, result="skip", reason="deploy_lock")
        return 0

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

            if simulate_mode:
                sim_rows = (
                    session.query(
                        ArchiveJob.id,
                        Source.code,
                        ArchiveJob.started_at,
                        ArchiveJob.output_dir,
                        ArchiveJob.combined_log_path,
                    )
                    .join(Source, ArchiveJob.source_id == Source.id)
                    .filter(ArchiveJob.id.in_(simulate_job_ids))
                    .order_by(ArchiveJob.id.asc())
                    .all()
                )
                existing_ids = {int(j.job_id) for j in running_jobs}
                found_ids: set[int] = set()
                for job_id, source_code, started_at, output_dir, combined_log_path in sim_rows:
                    jid = int(job_id)
                    found_ids.add(jid)
                    if jid in existing_ids:
                        continue
                    running_jobs.append(
                        RunningJob(
                            job_id=jid,
                            source_code=str(source_code),
                            started_at=started_at,
                            output_dir=str(output_dir) if output_dir is not None else None,
                            combined_log_path=str(combined_log_path) if combined_log_path else None,
                        )
                    )
                missing = [jid for jid in simulate_job_ids if jid not in found_ids]
                if missing:
                    print(
                        "WARNING: drill simulate job(s) not found in DB and will be ignored: "
                        + ", ".join(str(x) for x in missing)
                    )
    except Exception as exc:
        msg = str(exc)
        if "no such table" in msg and "archive_jobs" in msg:
            print("ERROR: database schema is missing required tables (archive_jobs).")
            print("Hint: load the backend env so HEALTHARCHIVE_DATABASE_URL points at the real DB:")
            print("  sudo bash -lc 'set -a; source /etc/healtharchive/backend.env; set +a; ...'")
            return 2
        raise

    if simulate_mode:
        print(
            "DRILL: simulate-stalled-job-id active; dry-run only. "
            "No systemd actions or DB writes will be performed."
        )

    stalled: list[tuple[RunningJob, float]] = []
    progress_age_by_job_id: dict[int, float] = {}
    for job in running_jobs:
        if int(job.job_id) in simulate_job_ids:
            simulated_age = (
                int(args.simulate_stalled_age_seconds)
                if args.simulate_stalled_age_seconds is not None
                else int(args.stall_threshold_seconds) + 1
            )
            age = float(simulated_age)
            progress_age_by_job_id[int(job.job_id)] = float(age)
            if age >= float(args.stall_threshold_seconds):
                stalled.append((job, age))
            continue
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
        write_metrics(
            running_jobs=len(running_jobs),
            stalled_jobs=0,
            result="skip",
            reason="no_stalled_jobs",
            state=state,
        )
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
                write_metrics(
                    running_jobs=len(running_jobs),
                    stalled_jobs=len(stalled),
                    result="skip",
                    reason="guard_window_healthy_job",
                    state=state,
                )
                return 0
            print(
                f"{'APPLY' if args.apply else 'DRY-RUN'}: soft-recover stalled job_id={job.job_id} "
                f"source={job.source_code} stalled_age_seconds={age:.0f} "
                f"(another job_id={jid} made progress {a:.0f}s ago; not restarting worker)"
            )
            if not args.apply:
                print("")
                print("Planned actions (dry-run):")
                print("  1) (skip) do not restart the worker (guard window active)")
                print(
                    "  2) /opt/healtharchive-backend/.venv/bin/ha-backend recover-stale-jobs "
                    f"--older-than-minutes {int(args.recover_older_than_minutes)} "
                    f"--require-no-progress-seconds {int(args.stall_threshold_seconds)} "
                    f"--apply --source {job.source_code} --limit 5"
                )
                write_metrics(
                    running_jobs=len(running_jobs),
                    stalled_jobs=len(stalled),
                    result="skip",
                    reason="dry_run_soft_recover",
                    state=state,
                )
                return 0

            recent_n = _count_recent_recoveries(state, job.job_id, since_utc=recent_cutoff)
            if recent_n >= int(args.max_recoveries_per_job_per_day):
                print(
                    f"SKIP job_id={job.job_id} source={job.source_code}: stalled for {age:.0f}s, "
                    f"but max recoveries reached ({recent_n}/{args.max_recoveries_per_job_per_day} in last 24h)."
                )
                write_metrics(
                    running_jobs=len(running_jobs),
                    stalled_jobs=len(stalled),
                    result="skip",
                    reason="max_recoveries_soft",
                    state=state,
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
            write_metrics(
                running_jobs=len(running_jobs),
                stalled_jobs=len(stalled),
                result="ok",
                reason="soft_recovered",
                state=state,
            )
            return 0

    recent_n = _count_recent_recoveries(state, job.job_id, since_utc=recent_cutoff)
    if recent_n >= int(args.max_recoveries_per_job_per_day):
        print(
            f"SKIP job_id={job.job_id} source={job.source_code}: stalled for {age:.0f}s, "
            f"but max recoveries reached ({recent_n}/{args.max_recoveries_per_job_per_day} in last 24h)."
        )
        write_metrics(
            running_jobs=len(running_jobs),
            stalled_jobs=len(stalled),
            result="skip",
            reason="max_recoveries_full",
            state=state,
        )
        return 0

    print(
        f"{'APPLY' if args.apply else 'DRY-RUN'}: would recover stalled job_id={job.job_id} "
        f"source={job.source_code} stalled_age_seconds={age:.0f}"
    )
    if not args.apply:
        print("")
        print("Planned actions (dry-run):")
        print("  1) systemctl stop healtharchive-worker.service")
        print(
            "  2) /opt/healtharchive-backend/.venv/bin/ha-backend recover-stale-jobs "
            f"--older-than-minutes {int(args.recover_older_than_minutes)} "
            f"--require-no-progress-seconds {int(args.stall_threshold_seconds)} "
            f"--apply --source {job.source_code} --limit 5"
        )
        print("  3) systemctl start healtharchive-worker.service")
        write_metrics(
            running_jobs=len(running_jobs),
            stalled_jobs=len(stalled),
            result="skip",
            reason="dry_run_full_recover",
            state=state,
        )
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
    write_metrics(
        running_jobs=len(running_jobs),
        stalled_jobs=len(stalled),
        result="ok",
        reason="full_recovered",
        state=state,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
