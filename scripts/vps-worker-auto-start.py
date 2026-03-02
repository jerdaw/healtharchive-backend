#!/usr/bin/env python3
from __future__ import annotations

import argparse
import fcntl
import json
import os
import stat
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

DEFAULT_DEPLOY_LOCK_FILE = "/tmp/healtharchive-backend-deploy.lock"


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _dt_to_epoch_seconds(dt: datetime) -> int:
    return int(dt.astimezone(timezone.utc).timestamp())


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


def _systemctl_is_active(unit: str) -> bool:
    r = subprocess.run(
        ["systemctl", "is-active", unit], check=False, capture_output=True, text=True
    )
    return r.stdout.strip() == "active"


def _run_capture(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, check=False, capture_output=True, text=True)  # nosec: B603


def _ps_snapshot() -> list[tuple[int, int, str]] | None:
    cp = _run_capture(["ps", "-eo", "pid=,ppid=,args="])
    if cp.returncode != 0:
        return None
    rows: list[tuple[int, int, str]] = []
    for line in cp.stdout.splitlines():
        parts = line.strip().split(maxsplit=2)
        if len(parts) < 3:
            continue
        try:
            pid = int(parts[0])
            ppid = int(parts[1])
        except ValueError:
            continue
        rows.append((pid, ppid, parts[2]))
    return rows


def _is_likely_crawl_runner_cmd(args: str) -> bool:
    s = str(args or "")
    if "archive-tool" in s:
        return True
    if "docker run" in s and "ghcr.io/openzim/zimit" in s:
        return True
    return False


def _output_dir_has_running_crawl_process(
    output_dir: str | None, ps_rows: list[tuple[int, int, str]]
) -> bool:
    if not output_dir:
        return False
    needle = str(output_dir)
    for _pid, _ppid, args in ps_rows:
        if needle in args and _is_likely_crawl_runner_cmd(args):
            return True
    return False


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

    The deploy helper uses `flock` on a persistent file. That means the file can
    exist even when no deploy is running. We therefore prefer probing whether
    the lock is *currently held* by another process.

    If the lock state can't be probed (unexpected), we fall back to an mtime age
    heuristic (backwards compatible with older behavior).
    """
    age_seconds = _file_age_seconds(deploy_lock_file, now_utc=now_utc)
    if age_seconds is None:
        return 0, None

    try:
        # Open read-only so root can probe locks on user-owned files in sticky
        # directories like /tmp (some systems restrict write opens here).
        f = deploy_lock_file.open("rb")
    except OSError:
        # Best-effort fallback to the prior "exists and is not stale" heuristic.
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


def _save_state_file(path: Path, data: dict) -> None:
    """Write state file with fsync for durability."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(json.dumps(data, indent=2, sort_keys=True) + "\n")
        f.flush()
        os.fsync(f.fileno())
    tmp.replace(path)


def _load_state_file(path: Path) -> dict:
    """Best-effort state loader (backward-compatible with older schemas)."""
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    if not isinstance(data, dict):
        return {}
    return data


def _state_int(state: dict, key: str) -> int:
    try:
        return int(state.get(key) or 0)
    except Exception:
        return 0


def _update_run_state(
    state: dict,
    *,
    now_utc: datetime,
    result: str,
    reason: str,
    worker_active: int,
    running_jobs: int,
    pending_jobs: int,
    exception: str | None = None,
    rc: int | None = None,
    stdout: str | None = None,
    stderr: str | None = None,
) -> None:
    state["last_run_utc"] = now_utc.replace(microsecond=0).isoformat()
    state["result"] = result
    state["reason"] = reason
    state["worker_active"] = int(worker_active)
    state["running_jobs"] = int(running_jobs)
    state["pending_jobs"] = int(pending_jobs)
    if exception:
        state["exception"] = str(exception)[:400]
    else:
        state.pop("exception", None)
    if rc is not None:
        state["rc"] = int(rc)
    else:
        state.pop("rc", None)
    if stdout:
        state["stdout"] = str(stdout)[:400]
    else:
        state.pop("stdout", None)
    if stderr:
        state["stderr"] = str(stderr)[:400]
    else:
        state.pop("stderr", None)


def _record_start_attempt(state: dict, *, now_utc: datetime, ok: bool) -> None:
    now_epoch = _dt_to_epoch_seconds(now_utc)
    state["start_attempts_total"] = _state_int(state, "start_attempts_total") + 1
    state["last_start_attempt_epoch"] = now_epoch
    if ok:
        state["start_success_total"] = _state_int(state, "start_success_total") + 1
        state["last_start_success_epoch"] = now_epoch
    else:
        state["start_fail_total"] = _state_int(state, "start_fail_total") + 1
        state["last_start_fail_epoch"] = now_epoch


def _write_textfile_metrics(
    *,
    out_dir: Path,
    out_file: str,
    now_utc: datetime,
    enabled: int,
    worker_active: int,
    running_jobs: int,
    pending_jobs: int,
    reconciled_running_jobs: int,
    storagebox_ok: int,
    storagebox_errno: int,
    deploy_lock_present: int,
    start_attempts_total: int,
    start_success_total: int,
    start_fail_total: int,
    last_start_attempt_epoch: int,
    last_start_success_epoch: int,
    last_start_fail_epoch: int,
    result: str,
    reason: str,
) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / out_file
    tmp = path.with_suffix(path.suffix + f".{os.getpid()}.tmp")

    def emit(line: str) -> None:
        lines.append(line.rstrip("\n"))

    lines: list[str] = []
    emit(
        "# HELP healtharchive_worker_auto_start_metrics_ok 1 if the worker auto-start watchdog ran to completion."
    )
    emit("# TYPE healtharchive_worker_auto_start_metrics_ok gauge")
    emit("healtharchive_worker_auto_start_metrics_ok 1")

    emit(
        "# HELP healtharchive_worker_auto_start_last_run_timestamp_seconds UNIX timestamp of the last watchdog run."
    )
    emit("# TYPE healtharchive_worker_auto_start_last_run_timestamp_seconds gauge")
    emit(
        f"healtharchive_worker_auto_start_last_run_timestamp_seconds {_dt_to_epoch_seconds(now_utc)}"
    )

    emit(
        "# HELP healtharchive_worker_auto_start_enabled 1 if the sentinel file exists (automation enabled)."
    )
    emit("# TYPE healtharchive_worker_auto_start_enabled gauge")
    emit(f"healtharchive_worker_auto_start_enabled {int(enabled)}")

    emit(
        "# HELP healtharchive_worker_auto_start_worker_active 1 if the worker systemd unit is active."
    )
    emit("# TYPE healtharchive_worker_auto_start_worker_active gauge")
    emit(f"healtharchive_worker_auto_start_worker_active {int(worker_active)}")

    emit(
        "# HELP healtharchive_worker_auto_start_jobs_running Number of jobs currently in status=running."
    )
    emit("# TYPE healtharchive_worker_auto_start_jobs_running gauge")
    emit(f"healtharchive_worker_auto_start_jobs_running {int(running_jobs)}")

    emit(
        "# HELP healtharchive_worker_auto_start_jobs_pending Number of jobs currently in status=queued or retryable."
    )
    emit("# TYPE healtharchive_worker_auto_start_jobs_pending gauge")
    emit(f"healtharchive_worker_auto_start_jobs_pending {int(pending_jobs)}")

    emit(
        "# HELP healtharchive_worker_auto_start_reconciled_running_jobs Number of stale status=running rows reconciled to retryable in this run."
    )
    emit("# TYPE healtharchive_worker_auto_start_reconciled_running_jobs gauge")
    emit(f"healtharchive_worker_auto_start_reconciled_running_jobs {int(reconciled_running_jobs)}")

    emit(
        "# HELP healtharchive_worker_auto_start_storagebox_mount_ok 1 if the Storage Box mount is readable (ls/stat works)."
    )
    emit("# TYPE healtharchive_worker_auto_start_storagebox_mount_ok gauge")
    emit(f"healtharchive_worker_auto_start_storagebox_mount_ok {int(storagebox_ok)}")

    emit(
        "# HELP healtharchive_worker_auto_start_storagebox_mount_errno Errno when Storage Box mount is unreadable, else -1."
    )
    emit("# TYPE healtharchive_worker_auto_start_storagebox_mount_errno gauge")
    emit(f"healtharchive_worker_auto_start_storagebox_mount_errno {int(storagebox_errno)}")

    emit(
        "# HELP healtharchive_worker_auto_start_deploy_lock_present 1 if deploy lock appears active (held by another process)."
    )
    emit("# TYPE healtharchive_worker_auto_start_deploy_lock_present gauge")
    emit(f"healtharchive_worker_auto_start_deploy_lock_present {int(deploy_lock_present)}")

    emit(
        "# HELP healtharchive_worker_auto_start_last_result 1 for the most recent watchdog outcome (labels: result, reason)."
    )
    emit("# TYPE healtharchive_worker_auto_start_last_result gauge")
    emit(f'healtharchive_worker_auto_start_last_result{{result="{result}",reason="{reason}"}} 1')

    emit(
        "# HELP healtharchive_worker_auto_start_start_attempts_total Total number of worker start attempts made by the watchdog."
    )
    emit("# TYPE healtharchive_worker_auto_start_start_attempts_total counter")
    emit(f"healtharchive_worker_auto_start_start_attempts_total {int(start_attempts_total)}")

    emit(
        "# HELP healtharchive_worker_auto_start_start_success_total Total number of successful worker start attempts made by the watchdog."
    )
    emit("# TYPE healtharchive_worker_auto_start_start_success_total counter")
    emit(f"healtharchive_worker_auto_start_start_success_total {int(start_success_total)}")

    emit(
        "# HELP healtharchive_worker_auto_start_start_fail_total Total number of failed worker start attempts made by the watchdog."
    )
    emit("# TYPE healtharchive_worker_auto_start_start_fail_total counter")
    emit(f"healtharchive_worker_auto_start_start_fail_total {int(start_fail_total)}")

    emit(
        "# HELP healtharchive_worker_auto_start_last_start_attempt_timestamp_seconds UNIX timestamp of the last worker start attempt (0 if none)."
    )
    emit("# TYPE healtharchive_worker_auto_start_last_start_attempt_timestamp_seconds gauge")
    emit(
        "healtharchive_worker_auto_start_last_start_attempt_timestamp_seconds "
        f"{int(last_start_attempt_epoch)}"
    )

    emit(
        "# HELP healtharchive_worker_auto_start_last_start_success_timestamp_seconds UNIX timestamp of the last successful worker start attempt (0 if none)."
    )
    emit("# TYPE healtharchive_worker_auto_start_last_start_success_timestamp_seconds gauge")
    emit(
        "healtharchive_worker_auto_start_last_start_success_timestamp_seconds "
        f"{int(last_start_success_epoch)}"
    )

    emit(
        "# HELP healtharchive_worker_auto_start_last_start_fail_timestamp_seconds UNIX timestamp of the last failed worker start attempt (0 if none)."
    )
    emit("# TYPE healtharchive_worker_auto_start_last_start_fail_timestamp_seconds gauge")
    emit(
        "healtharchive_worker_auto_start_last_start_fail_timestamp_seconds "
        f"{int(last_start_fail_epoch)}"
    )

    tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
    tmp.chmod(0o644)
    tmp.replace(path)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description=(
            "HealthArchive VPS helper: conservative watchdog that auto-starts the worker when it is down "
            "but there is pending work (queued/retryable jobs), with safety gates."
        )
    )
    p.add_argument(
        "--apply",
        action="store_true",
        default=False,
        help="Actually start the worker if conditions are met (default: dry-run).",
    )
    p.add_argument(
        "--sentinel-file",
        default="/etc/healtharchive/worker-auto-start-enabled",
        help="Sentinel file that indicates automation is enabled (written by operator).",
    )
    p.add_argument(
        "--state-file",
        default="/srv/healtharchive/ops/watchdog/worker-auto-start.json",
        help="Where to store watchdog state/history.",
    )
    p.add_argument(
        "--lock-file",
        default="/srv/healtharchive/ops/watchdog/worker-auto-start.lock",
        help="Lock file to prevent concurrent runs.",
    )
    p.add_argument(
        "--worker-unit",
        default="healtharchive-worker.service",
        help="Worker systemd unit name.",
    )
    p.add_argument(
        "--storagebox-mount",
        default="/srv/healtharchive/storagebox",
        help="Storage Box mountpoint on the VPS.",
    )
    p.add_argument(
        "--deploy-lock-file",
        default=DEFAULT_DEPLOY_LOCK_FILE,
        help="If this file exists (and is not stale), skip the run to avoid flapping during deploys.",
    )
    p.add_argument(
        "--deploy-lock-max-age-seconds",
        type=float,
        default=2 * 60 * 60,
        help="Treat --deploy-lock-file as stale if older than this; proceed if stale.",
    )
    p.add_argument(
        "--textfile-out-dir",
        default="/var/lib/node_exporter/textfile_collector",
        help="node_exporter textfile collector directory.",
    )
    p.add_argument(
        "--textfile-out-file",
        default="healtharchive_worker_auto_start.prom",
        help="Output filename under --textfile-out-dir.",
    )
    p.add_argument(
        "--reconcile-running-drift",
        action="store_true",
        default=True,
        help=(
            "When worker is down, reconcile stale DB running-job rows to retryable if no crawl process "
            "is active for their output_dir. Enabled by default."
        ),
    )
    p.add_argument(
        "--no-reconcile-running-drift",
        dest="reconcile_running_drift",
        action="store_false",
        help="Disable running-job drift reconciliation.",
    )
    p.add_argument(
        "--reconcile-older-than-minutes",
        type=int,
        default=10,
        help="Only reconcile running jobs older than this threshold.",
    )
    p.add_argument(
        "--reconcile-limit",
        type=int,
        default=10,
        help="Maximum running jobs to reconcile in one watchdog run.",
    )
    args = p.parse_args(argv)

    now = _utc_now()
    sentinel_file = Path(args.sentinel_file)
    enabled = 1 if sentinel_file.is_file() else 0
    state_path = Path(args.state_file)
    state_snapshot = _load_state_file(state_path)
    start_attempts_total = _state_int(state_snapshot, "start_attempts_total")
    start_success_total = _state_int(state_snapshot, "start_success_total")
    start_fail_total = _state_int(state_snapshot, "start_fail_total")
    last_start_attempt_epoch = _state_int(state_snapshot, "last_start_attempt_epoch")
    last_start_success_epoch = _state_int(state_snapshot, "last_start_success_epoch")
    last_start_fail_epoch = _state_int(state_snapshot, "last_start_fail_epoch")

    # Disabled by default unless the operator creates the sentinel.
    if enabled != 1:
        try:
            _write_textfile_metrics(
                out_dir=Path(str(args.textfile_out_dir)),
                out_file=str(args.textfile_out_file),
                now_utc=now,
                enabled=enabled,
                worker_active=int(_systemctl_is_active(str(args.worker_unit))),
                running_jobs=0,
                pending_jobs=0,
                reconciled_running_jobs=0,
                storagebox_ok=0,
                storagebox_errno=0,
                deploy_lock_present=0,
                start_attempts_total=start_attempts_total,
                start_success_total=start_success_total,
                start_fail_total=start_fail_total,
                last_start_attempt_epoch=last_start_attempt_epoch,
                last_start_success_epoch=last_start_success_epoch,
                last_start_fail_epoch=last_start_fail_epoch,
                result="skip",
                reason="disabled",
            )
        except Exception:
            pass
        return 0

    lock_path = Path(args.lock_file)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_f = lock_path.open("a", encoding="utf-8")
    try:
        fcntl.flock(lock_f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return 0

    state_path.parent.mkdir(parents=True, exist_ok=True)
    state = _load_state_file(state_path)

    result = "skip"
    reason = "no_action"
    worker_active = int(_systemctl_is_active(str(args.worker_unit)))

    # Deploy lock gate: prefer probing whether the lock is currently held.
    deploy_lock_file = Path(str(args.deploy_lock_file))
    deploy_lock_present, _deploy_lock_age_seconds = _deploy_lock_is_active(
        deploy_lock_file,
        now_utc=now,
        deploy_lock_max_age_seconds=float(args.deploy_lock_max_age_seconds),
    )
    if deploy_lock_present == 1:
        reason = "deploy_lock"
        try:
            _write_textfile_metrics(
                out_dir=Path(str(args.textfile_out_dir)),
                out_file=str(args.textfile_out_file),
                now_utc=now,
                enabled=enabled,
                worker_active=worker_active,
                running_jobs=0,
                pending_jobs=0,
                reconciled_running_jobs=0,
                storagebox_ok=0,
                storagebox_errno=0,
                deploy_lock_present=deploy_lock_present,
                start_attempts_total=_state_int(state, "start_attempts_total"),
                start_success_total=_state_int(state, "start_success_total"),
                start_fail_total=_state_int(state, "start_fail_total"),
                last_start_attempt_epoch=_state_int(state, "last_start_attempt_epoch"),
                last_start_success_epoch=_state_int(state, "last_start_success_epoch"),
                last_start_fail_epoch=_state_int(state, "last_start_fail_epoch"),
                result="skip",
                reason=reason,
            )
        except Exception:
            pass
        return 0

    # Storage Box gate.
    storagebox_mount = Path(str(args.storagebox_mount))
    storagebox_ok, storagebox_errno = _probe_readable_dir(storagebox_mount)
    if storagebox_ok != 1:
        reason = f"storagebox_unreadable_errno_{int(storagebox_errno)}"
        try:
            _write_textfile_metrics(
                out_dir=Path(str(args.textfile_out_dir)),
                out_file=str(args.textfile_out_file),
                now_utc=now,
                enabled=enabled,
                worker_active=worker_active,
                running_jobs=0,
                pending_jobs=0,
                reconciled_running_jobs=0,
                storagebox_ok=int(storagebox_ok),
                storagebox_errno=int(storagebox_errno),
                deploy_lock_present=deploy_lock_present,
                start_attempts_total=_state_int(state, "start_attempts_total"),
                start_success_total=_state_int(state, "start_success_total"),
                start_fail_total=_state_int(state, "start_fail_total"),
                last_start_attempt_epoch=_state_int(state, "last_start_attempt_epoch"),
                last_start_success_epoch=_state_int(state, "last_start_success_epoch"),
                last_start_fail_epoch=_state_int(state, "last_start_fail_epoch"),
                result="skip",
                reason=reason,
            )
        except Exception:
            pass
        return 0

    running_jobs = 0
    pending_jobs = 0
    reconciled_running_jobs = 0
    try:
        from ha_backend.db import get_session
        from ha_backend.models import ArchiveJob

        with get_session() as session:
            running_jobs = session.query(ArchiveJob).filter(ArchiveJob.status == "running").count()
            pending_jobs = (
                session.query(ArchiveJob)
                .filter(ArchiveJob.status.in_(["queued", "retryable"]))
                .count()
            )
    except Exception as exc:
        # Prefer to skip rather than flap the worker on partial DB visibility.
        reason = "db_error"
        _update_run_state(
            state,
            now_utc=now,
            result="skip",
            reason=reason,
            worker_active=worker_active,
            running_jobs=0,
            pending_jobs=0,
            exception=str(exc),
        )
        _save_state_file(state_path, state)
        try:
            _write_textfile_metrics(
                out_dir=Path(str(args.textfile_out_dir)),
                out_file=str(args.textfile_out_file),
                now_utc=now,
                enabled=enabled,
                worker_active=worker_active,
                running_jobs=0,
                pending_jobs=0,
                reconciled_running_jobs=0,
                storagebox_ok=int(storagebox_ok),
                storagebox_errno=int(storagebox_errno),
                deploy_lock_present=deploy_lock_present,
                start_attempts_total=_state_int(state, "start_attempts_total"),
                start_success_total=_state_int(state, "start_success_total"),
                start_fail_total=_state_int(state, "start_fail_total"),
                last_start_attempt_epoch=_state_int(state, "last_start_attempt_epoch"),
                last_start_success_epoch=_state_int(state, "last_start_success_epoch"),
                last_start_fail_epoch=_state_int(state, "last_start_fail_epoch"),
                result="skip",
                reason=reason,
            )
        except Exception:
            pass
        return 0

    if (
        worker_active == 0
        and running_jobs > 0
        and bool(args.reconcile_running_drift)
        and int(args.reconcile_limit) > 0
    ):
        ps_rows = _ps_snapshot()
        if ps_rows is not None:
            cutoff = now - timedelta(minutes=max(1, int(args.reconcile_older_than_minutes)))
            try:
                from ha_backend.db import get_session
                from ha_backend.models import ArchiveJob

                with get_session() as session:
                    rows = (
                        session.query(ArchiveJob)
                        .filter(ArchiveJob.status == "running")
                        .filter(ArchiveJob.started_at.is_not(None))
                        .filter(ArchiveJob.started_at < cutoff)
                        .order_by(ArchiveJob.started_at.asc().nullsfirst(), ArchiveJob.id.asc())
                        .limit(int(args.reconcile_limit))
                        .all()
                    )
                    for job in rows:
                        if _output_dir_has_running_crawl_process(
                            str(getattr(job, "output_dir", "") or ""),
                            ps_rows,
                        ):
                            continue
                        job.status = "retryable"
                        job.crawler_stage = "reconciled_worker_down_running_drift"
                        reconciled_running_jobs += 1
                    if reconciled_running_jobs > 0:
                        session.commit()
            except Exception:
                # Prefer safety over aggressive starts when reconciliation fails.
                reconciled_running_jobs = 0

    if reconciled_running_jobs > 0:
        running_jobs = max(0, int(running_jobs) - int(reconciled_running_jobs))
        pending_jobs = int(pending_jobs) + int(reconciled_running_jobs)

    if worker_active == 1:
        result = "skip"
        reason = "worker_active"
    elif running_jobs > 0:
        # Conservative: if the DB says a job is "running" but the worker is down, treat it as a
        # potentially mid-flight partial state. Require manual investigation.
        result = "skip"
        reason = "running_jobs_present_worker_inactive"
    elif pending_jobs <= 0:
        result = "skip"
        reason = "no_pending_jobs"
    else:
        # All gates passed: start the worker.
        if not args.apply:
            result = "skip"
            reason = "dry_run_would_start"
        else:
            cp = subprocess.run(
                ["systemctl", "start", str(args.worker_unit)],
                check=False,
                capture_output=True,
                text=True,
            )
            if cp.returncode == 0:
                result = "ok"
                reason = "started_worker"
                _record_start_attempt(state, now_utc=now, ok=True)
            else:
                result = "fail"
                reason = "systemctl_start_failed"
                _record_start_attempt(state, now_utc=now, ok=False)
                _update_run_state(
                    state,
                    now_utc=now,
                    result=result,
                    reason=reason,
                    worker_active=worker_active,
                    running_jobs=int(running_jobs),
                    pending_jobs=int(pending_jobs),
                    rc=int(cp.returncode),
                    stdout=cp.stdout or "",
                    stderr=cp.stderr or "",
                )
                _save_state_file(state_path, state)

    # Always write state (best-effort) for forensics.
    try:
        _update_run_state(
            state,
            now_utc=now,
            result=result,
            reason=reason,
            worker_active=worker_active,
            running_jobs=int(running_jobs),
            pending_jobs=int(pending_jobs),
        )
        _save_state_file(state_path, state)
    except Exception:
        pass

    try:
        _write_textfile_metrics(
            out_dir=Path(str(args.textfile_out_dir)),
            out_file=str(args.textfile_out_file),
            now_utc=now,
            enabled=enabled,
            worker_active=worker_active,
            running_jobs=int(running_jobs),
            pending_jobs=int(pending_jobs),
            reconciled_running_jobs=int(reconciled_running_jobs),
            storagebox_ok=int(storagebox_ok),
            storagebox_errno=int(storagebox_errno),
            deploy_lock_present=deploy_lock_present,
            start_attempts_total=_state_int(state, "start_attempts_total"),
            start_success_total=_state_int(state, "start_success_total"),
            start_fail_total=_state_int(state, "start_fail_total"),
            last_start_attempt_epoch=_state_int(state, "last_start_attempt_epoch"),
            last_start_success_epoch=_state_int(state, "last_start_success_epoch"),
            last_start_fail_epoch=_state_int(state, "last_start_fail_epoch"),
            result=result,
            reason=reason,
        )
    except Exception:
        pass

    # Keep the unit green; rely on metrics for alerting.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
