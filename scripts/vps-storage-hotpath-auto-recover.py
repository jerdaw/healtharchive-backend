#!/usr/bin/env python3
from __future__ import annotations

import argparse
import fcntl
import json
import os
import stat
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

DEFAULT_DEPLOY_LOCK_FILE = "/tmp/healtharchive-backend-deploy.lock"


@dataclass(frozen=True)
class RunningJob:
    job_id: int
    source_code: str
    started_at: datetime | None
    output_dir: str | None


@dataclass(frozen=True)
class NextJob:
    job_id: int
    source_code: str
    status: str
    queued_at: datetime | None
    output_dir: str | None


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _dt_to_epoch_seconds(dt: datetime) -> int:
    return int(dt.astimezone(timezone.utc).timestamp())


def _parse_utc(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(str(raw))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _load_state(path: Path) -> dict:
    if not path.is_file():
        return {
            "observations": {},
            "recoveries": {"global": [], "jobs": {}},
        }
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {
            "observations": {},
            "recoveries": {"global": [], "jobs": {}},
        }
    if not isinstance(data, dict):
        return {
            "observations": {},
            "recoveries": {"global": [], "jobs": {}},
        }
    data.setdefault("observations", {})
    data.setdefault("recoveries", {"global": [], "jobs": {}})
    rec = data["recoveries"]
    if not isinstance(rec, dict):
        data["recoveries"] = {"global": [], "jobs": {}}
    else:
        rec.setdefault("global", [])
        rec.setdefault("jobs", {})
    return data


def _save_state(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(json.dumps(state, indent=2, sort_keys=True) + "\n")
        f.flush()
        os.fsync(f.fileno())
    tmp.replace(path)


def _count_recent_timestamps(items: list, *, since_utc: datetime) -> int:
    n = 0
    for raw in items:
        ts = _parse_utc(str(raw))
        if ts is None:
            continue
        if ts >= since_utc:
            n += 1
    return n


def _record_global_recovery(state: dict, *, when_utc: datetime) -> None:
    rec = state.setdefault("recoveries", {}).setdefault("global", [])
    if not isinstance(rec, list):
        rec = []
        state["recoveries"]["global"] = rec
    rec.append(when_utc.replace(microsecond=0).isoformat())


def _record_job_recovery(state: dict, job_id: int, *, when_utc: datetime) -> None:
    jobs = state.setdefault("recoveries", {}).setdefault("jobs", {})
    if not isinstance(jobs, dict):
        jobs = {}
        state["recoveries"]["jobs"] = jobs
    items = jobs.get(str(job_id))
    if not isinstance(items, list):
        items = []
    items.append(when_utc.replace(microsecond=0).isoformat())
    jobs[str(job_id)] = items


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


def _read_manifest_hot_paths(manifest_path: Path) -> list[Path]:
    try:
        if not manifest_path.is_file():
            return []
    except OSError:
        return []
    try:
        lines = manifest_path.read_text(encoding="utf-8").splitlines()
    except Exception:
        return []

    hot_paths: list[Path] = []
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) < 2:
            continue
        hot_paths.append(Path(parts[1]))
    return hot_paths


def _run_read(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, check=False, capture_output=True, text=True)  # nosec: B603


def _run_apply(
    cmd: list[str], *, timeout_seconds: float | None = None
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # nosec: B603
        cmd,
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout_seconds,
    )


def _get_mount_info(path: str) -> dict[str, str] | None:
    """
    Return mount info for the mount containing `path` (best-effort), or None.

    Uses findmnt when available; falls back to parsing `mount` output.
    """
    r = _run_read(["findmnt", "-T", path, "-o", "SOURCE,TARGET,FSTYPE", "-n"])
    if r.returncode == 0 and r.stdout.strip():
        parts = r.stdout.strip().split(None, 2)
        if len(parts) >= 3:
            return {"source": parts[0], "target": parts[1], "fstype": parts[2]}

    r = _run_read(["mount"])
    if r.returncode != 0:
        return None
    needle = f" on {path} "
    for line in r.stdout.splitlines():
        if needle not in line:
            continue
        # Example: SRC on TARGET type FSTYPE (opts)
        try:
            left, rest = line.split(" on ", 1)
            target_part, rest2 = rest.split(" type ", 1)
            fstype_part = rest2.split(" ", 1)[0].strip()
        except ValueError:
            continue
        return {
            "source": left.strip(),
            "target": target_part.strip(),
            "fstype": fstype_part.strip(),
        }
    return None


def _is_unit_present(unit: str) -> bool:
    r = _run_read(["systemctl", "cat", unit])
    return r.returncode == 0


def _systemctl_is_active(unit: str) -> bool:
    r = _run_read(["systemctl", "is-active", unit])
    return r.stdout.strip() == "active"


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


def _log_stale_mount_diagnostics(
    path: str,
    job_id: int | None,
    mount_info: dict,
    *,
    storagebox_mount: Path,
) -> None:
    """
    Log diagnostic information when a stale mount is detected.

    Helps investigate root cause by capturing:
    - Base Storage Box mount health
    - Mount type (bind mount vs direct sshfs)
    - Network connectivity hints
    - Filesystem state
    """
    print(f"DIAGNOSTIC: Stale mount detected at {path}", file=sys.stderr)
    if job_id is not None:
        print(f"  Job ID: {job_id}", file=sys.stderr)

    # Check base Storage Box mount
    storagebox_ok, storagebox_errno = _probe_readable_dir(storagebox_mount)
    if storagebox_ok == 1:
        print("  Base sshfs: OK (readable)", file=sys.stderr)
    else:
        print(f"  Base sshfs: FAILED (errno={storagebox_errno})", file=sys.stderr)

    # Analyze mount type
    fstype = mount_info.get("fstype", "unknown")
    source = mount_info.get("source", "unknown")
    target = mount_info.get("target", "unknown")

    if "fuse.sshfs" in fstype:
        mount_type = "direct sshfs mount"
    elif source and ":" not in source:
        mount_type = "bind mount (from local path)"
    else:
        mount_type = "unknown"

    print(f"  Mount type: {mount_type}", file=sys.stderr)
    print(f"  Mount source: {source}", file=sys.stderr)
    print(f"  Mount target: {target}", file=sys.stderr)
    print(f"  Filesystem type: {fstype}", file=sys.stderr)

    # Check if path is under storagebox (indicates bind mount relationship)
    if str(storagebox_mount) in str(path):
        print("  Note: Path is under Storage Box mount (bind mount scenario)", file=sys.stderr)

    # Probe findmnt for more details
    try:
        result = _run_read(["findmnt", "-T", path, "-o", "SOURCE,TARGET,FSTYPE,OPTIONS", "-n"])
        if result.returncode == 0 and result.stdout.strip():
            print(f"  findmnt output: {result.stdout.strip()}", file=sys.stderr)
    except Exception:
        pass

    # Check if parent directory is readable (helps isolate issue)
    try:
        parent = str(Path(path).parent)
        parent_ok, parent_errno = _probe_readable_dir(Path(parent))
        if parent_ok == 1:
            print(f"  Parent dir ({parent}): readable", file=sys.stderr)
        else:
            print(f"  Parent dir ({parent}): unreadable (errno={parent_errno})", file=sys.stderr)
    except Exception as e:
        print(f"  Parent dir check failed: {e}", file=sys.stderr)

    print("", file=sys.stderr)


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

    If probing fails unexpectedly, fall back to an mtime-based heuristic
    (backwards compatible with the initial implementation).
    """
    age_seconds = _file_age_seconds(deploy_lock_file, now_utc=now_utc)
    if age_seconds is None:
        return 0, None

    try:
        # Open read-only so root can probe locks on user-owned files in sticky
        # directories like /tmp (some systems restrict write opens here).
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


def _write_metrics(
    *,
    out_dir: Path,
    out_file: str,
    sentinel_file: Path,
    now_utc: datetime,
    state: dict,
    metrics_ok: int,
    last_apply_ok: int,
    last_apply_epoch: int,
    detected_targets: int,
    deploy_lock_active: int,
    deploy_lock_age_seconds: float | None,
) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / out_file
    tmp = path.with_suffix(path.suffix + f".{os.getpid()}.tmp")

    enabled = 1 if sentinel_file.is_file() else 0

    rec = state.get("recoveries") or {}
    global_items = rec.get("global") if isinstance(rec, dict) else None
    if not isinstance(global_items, list):
        global_items = []

    lines: list[str] = []
    lines.append(
        "# HELP healtharchive_storage_hotpath_auto_recover_metrics_ok 1 if the hot-path auto-recover script ran to completion."
    )
    lines.append("# TYPE healtharchive_storage_hotpath_auto_recover_metrics_ok gauge")
    lines.append(f"healtharchive_storage_hotpath_auto_recover_metrics_ok {int(metrics_ok)}")

    lines.append(
        "# HELP healtharchive_storage_hotpath_auto_recover_last_run_timestamp_seconds UNIX timestamp of the last watchdog run."
    )
    lines.append(
        "# TYPE healtharchive_storage_hotpath_auto_recover_last_run_timestamp_seconds gauge"
    )
    lines.append(
        f"healtharchive_storage_hotpath_auto_recover_last_run_timestamp_seconds {_dt_to_epoch_seconds(now_utc)}"
    )

    last_healthy_epoch = int(state.get("last_healthy_epoch") or 0)
    lines.append(
        "# HELP healtharchive_storage_hotpath_auto_recover_last_healthy_timestamp_seconds UNIX timestamp of the last run that observed no stale targets."
    )
    lines.append(
        "# TYPE healtharchive_storage_hotpath_auto_recover_last_healthy_timestamp_seconds gauge"
    )
    lines.append(
        f"healtharchive_storage_hotpath_auto_recover_last_healthy_timestamp_seconds {last_healthy_epoch}"
    )

    lines.append(
        "# HELP healtharchive_storage_hotpath_auto_recover_enabled 1 if the sentinel file exists (automation enabled)."
    )
    lines.append("# TYPE healtharchive_storage_hotpath_auto_recover_enabled gauge")
    lines.append(f"healtharchive_storage_hotpath_auto_recover_enabled {enabled}")

    lines.append(
        "# HELP healtharchive_storage_hotpath_auto_recover_deploy_lock_active 1 if the deploy lock is currently held (apply actions suppressed)."
    )
    lines.append("# TYPE healtharchive_storage_hotpath_auto_recover_deploy_lock_active gauge")
    lines.append(
        f"healtharchive_storage_hotpath_auto_recover_deploy_lock_active {int(deploy_lock_active)}"
    )
    lines.append(
        "# HELP healtharchive_storage_hotpath_auto_recover_deploy_lock_age_seconds Age (mtime) of the deploy lock file, or -1 if missing/unreadable."
    )
    lines.append("# TYPE healtharchive_storage_hotpath_auto_recover_deploy_lock_age_seconds gauge")
    if deploy_lock_age_seconds is None:
        lines.append("healtharchive_storage_hotpath_auto_recover_deploy_lock_age_seconds -1")
    else:
        lines.append(
            "healtharchive_storage_hotpath_auto_recover_deploy_lock_age_seconds "
            f"{int(deploy_lock_age_seconds)}"
        )

    lines.append(
        "# HELP healtharchive_storage_hotpath_auto_recover_detected_targets Number of targets currently detected as stale/unreadable (Errno 107)."
    )
    lines.append("# TYPE healtharchive_storage_hotpath_auto_recover_detected_targets gauge")
    lines.append(
        f"healtharchive_storage_hotpath_auto_recover_detected_targets {int(detected_targets)}"
    )

    lines.append(
        "# HELP healtharchive_storage_hotpath_auto_recover_last_apply_timestamp_seconds UNIX timestamp of the last apply-mode recovery attempt."
    )
    lines.append(
        "# TYPE healtharchive_storage_hotpath_auto_recover_last_apply_timestamp_seconds gauge"
    )
    lines.append(
        f"healtharchive_storage_hotpath_auto_recover_last_apply_timestamp_seconds {int(last_apply_epoch)}"
    )

    lines.append(
        "# HELP healtharchive_storage_hotpath_auto_recover_last_apply_ok 1 if the last apply-mode recovery attempt succeeded."
    )
    lines.append("# TYPE healtharchive_storage_hotpath_auto_recover_last_apply_ok gauge")
    lines.append(f"healtharchive_storage_hotpath_auto_recover_last_apply_ok {int(last_apply_ok)}")

    lines.append(
        "# HELP healtharchive_storage_hotpath_auto_recover_apply_total Total number of apply-mode recovery attempts recorded."
    )
    lines.append("# TYPE healtharchive_storage_hotpath_auto_recover_apply_total counter")
    lines.append(f"healtharchive_storage_hotpath_auto_recover_apply_total {len(global_items)}")

    lines.append(
        "# HELP healtharchive_storage_hotpath_auto_recover_apply_24h Number of apply-mode recovery attempts in the last 24 hours."
    )
    lines.append("# TYPE healtharchive_storage_hotpath_auto_recover_apply_24h gauge")
    lines.append(
        "healtharchive_storage_hotpath_auto_recover_apply_24h "
        f"{_count_recent_timestamps(global_items, since_utc=now_utc - timedelta(days=1))}"
    )

    lines.append(
        "# HELP healtharchive_storage_hotpath_auto_recover_apply_1h Number of apply-mode recovery attempts in the last hour."
    )
    lines.append("# TYPE healtharchive_storage_hotpath_auto_recover_apply_1h gauge")
    lines.append(
        "healtharchive_storage_hotpath_auto_recover_apply_1h "
        f"{_count_recent_timestamps(global_items, since_utc=now_utc - timedelta(hours=1))}"
    )

    tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
    tmp.chmod(0o644)
    tmp.replace(path)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description=(
            "HealthArchive VPS helper: conservative auto-recovery for stale/unreadable "
            "Storage Box / sshfs hot paths (Errno 107)."
        )
    )
    p.add_argument(
        "--apply",
        action="store_true",
        default=False,
        help="Apply recovery actions (default: dry-run).",
    )
    p.add_argument(
        "--sentinel-file",
        default="/etc/healtharchive/storage-hotpath-auto-recover-enabled",
        help="Sentinel file that indicates automation is enabled (written by operator).",
    )
    p.add_argument(
        "--state-file",
        default="/srv/healtharchive/ops/watchdog/storage-hotpath-auto-recover.json",
        help="Where to store watchdog state/history.",
    )
    p.add_argument(
        "--lock-file",
        default="/srv/healtharchive/ops/watchdog/storage-hotpath-auto-recover.lock",
        help="Lock file to prevent concurrent runs.",
    )
    p.add_argument(
        "--jobs-root",
        default="/srv/healtharchive/jobs",
        help="Safety guard: only unmount paths under this root.",
    )
    p.add_argument(
        "--storagebox-mount",
        default="/srv/healtharchive/storagebox",
        help="Storage Box base mountpoint.",
    )
    p.add_argument(
        "--manifest",
        default="/etc/healtharchive/warc-tiering.binds",
        help="WARC tiering bind-mount manifest (for hot path checks).",
    )
    p.add_argument(
        "--next-jobs-limit",
        type=int,
        default=10,
        help="Probe the output dirs of the next N queued/retryable jobs (default: 10).",
    )
    p.add_argument(
        "--simulate-broken-path",
        action="append",
        default=[],
        help=(
            "DRILL ONLY (dry-run): treat the given path as if it were failing with "
            "Errno 107 (Transport endpoint is not connected). "
            "Use a temporary --state-file/--lock-file for drills to avoid affecting production watchdog state."
        ),
    )
    p.add_argument(
        "--min-failure-age-seconds",
        type=int,
        default=120,
        help="Minimum time a target must be observed broken before acting.",
    )
    p.add_argument(
        "--confirm-runs",
        type=int,
        default=2,
        help="Require this many consecutive watchdog runs observing the failure before acting.",
    )
    p.add_argument(
        "--cooldown-seconds",
        type=int,
        default=15 * 60,
        help="Minimum time between apply-mode recoveries (global cooldown).",
    )
    p.add_argument(
        "--max-recoveries-per-hour",
        type=int,
        default=2,
        help="Global safety cap (last 60 minutes).",
    )
    p.add_argument(
        "--max-recoveries-per-day",
        type=int,
        default=6,
        help="Global safety cap (last 24 hours).",
    )
    p.add_argument(
        "--max-recoveries-per-job-per-day",
        type=int,
        default=3,
        help="Per-job safety cap (last 24 hours).",
    )
    p.add_argument(
        "--restart-wait-seconds",
        type=int,
        default=60,
        help="Max seconds to wait for the Storage Box mount to become readable after restart.",
    )
    p.add_argument(
        "--restart-probe-interval-seconds",
        type=int,
        default=5,
        help="Seconds between Storage Box mount probes while waiting after restart.",
    )
    p.add_argument(
        "--recover-older-than-minutes",
        type=int,
        default=2,
        help="Pass-through to ha-backend recover-stale-jobs --older-than-minutes.",
    )
    p.add_argument(
        "--worker-unit",
        default="healtharchive-worker.service",
        help="Worker systemd unit to stop/start during recovery.",
    )
    p.add_argument(
        "--storagebox-unit",
        default="healtharchive-storagebox-sshfs.service",
        help="Storage Box mount systemd unit to restart if base mount is unhealthy.",
    )
    p.add_argument(
        "--replay-unit",
        default="healtharchive-replay.service",
        help="Replay systemd unit to restart after successful mount recovery (best-effort).",
    )
    p.add_argument(
        "--replay-smoke-unit",
        default="healtharchive-replay-smoke.service",
        help="Replay smoke systemd unit to run after successful replay restart (best-effort).",
    )
    p.add_argument(
        "--annual-output-tiering-script",
        default="/opt/healtharchive-backend/scripts/vps-annual-output-tiering.py",
        help="Annual output tiering script to re-run after recovery.",
    )
    p.add_argument(
        "--tiering-apply-script",
        default="/opt/healtharchive-backend/scripts/vps-warc-tiering-bind-mounts.sh",
        help="Script to re-apply tiering bind mounts.",
    )
    p.add_argument(
        "--ha-backend",
        default="/opt/healtharchive-backend/.venv/bin/ha-backend",
        help="Path to ha-backend CLI (used for recover-stale-jobs).",
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
        default="healtharchive_storage_hotpath_auto_recover.prom",
        help="Output filename under --textfile-out-dir.",
    )
    args = p.parse_args(argv)
    simulate_broken_raw = list(getattr(args, "simulate_broken_path", []) or [])
    simulate_mode = bool(simulate_broken_raw)
    if simulate_mode and bool(args.apply):
        print(
            "ERROR: --simulate-broken-path is only allowed in dry-run mode (omit --apply).",
            file=sys.stderr,
        )
        return 2

    now = _utc_now()

    lock_path = Path(args.lock_file)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_f = lock_path.open("a", encoding="utf-8")
    try:
        fcntl.flock(lock_f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return 0

    state_path = Path(args.state_file)
    state = _load_state(state_path)

    jobs_root = Path(args.jobs_root).resolve()
    storagebox_mount = Path(args.storagebox_mount)
    sentinel_file = Path(args.sentinel_file)
    manifest_path = Path(args.manifest)

    deploy_lock_file = Path(str(args.deploy_lock_file))
    deploy_lock_active, deploy_lock_age_seconds = _deploy_lock_is_active(
        deploy_lock_file,
        now_utc=now,
        deploy_lock_max_age_seconds=float(args.deploy_lock_max_age_seconds),
    )
    requested_apply = bool(args.apply)
    apply_mode = requested_apply and (deploy_lock_active == 0)
    if deploy_lock_active == 1:
        # Do not perform any recovery actions while a deploy is in progress, but still
        # probe and record detection signals so operators can see what happened during
        # the deploy window.
        state["last_skip_utc"] = now.replace(microsecond=0).isoformat()
        state["last_skip_reason"] = "deploy_lock"
        state["last_skip_deploy_lock_file"] = str(deploy_lock_file)
        if deploy_lock_age_seconds is None:
            state["last_skip_deploy_lock_age_seconds"] = None
        else:
            state["last_skip_deploy_lock_age_seconds"] = int(deploy_lock_age_seconds)

    running_jobs: list[RunningJob] = []
    next_jobs: list[NextJob] = []
    running_jobs_query_ok = True
    impacted_sources: set[str] = set()

    # Discovery: running jobs (DB) is best-effort; manifest hot paths are independent.
    try:
        from ha_backend.db import get_session
        from ha_backend.models import ArchiveJob, Source

        with get_session() as session:
            rows = (
                session.query(
                    ArchiveJob.id,
                    Source.code,
                    ArchiveJob.started_at,
                    ArchiveJob.output_dir,
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
                )
                for job_id, source_code, started_at, output_dir in rows
            ]

            next_rows = (
                session.query(
                    ArchiveJob.id,
                    Source.code,
                    ArchiveJob.status,
                    ArchiveJob.queued_at,
                    ArchiveJob.output_dir,
                )
                .join(Source, ArchiveJob.source_id == Source.id)
                .filter(ArchiveJob.status.in_(["queued", "retryable"]))
                .order_by(ArchiveJob.queued_at.asc().nullsfirst(), ArchiveJob.created_at.asc())
                .limit(max(0, int(args.next_jobs_limit)))
                .all()
            )
            next_jobs = [
                NextJob(
                    job_id=int(job_id),
                    source_code=str(source_code),
                    status=str(status),
                    queued_at=queued_at,
                    output_dir=str(output_dir) if output_dir is not None else None,
                )
                for job_id, source_code, status, queued_at, output_dir in next_rows
            ]
    except Exception as exc:
        running_jobs_query_ok = False
        msg = str(exc)
        if "no such table" in msg and "archive_jobs" in msg:
            print("ERROR: database schema is missing required tables (archive_jobs).")
            print("Hint: load the backend env so HEALTHARCHIVE_DATABASE_URL points at the real DB.")
            return 2
        print(f"WARNING: failed to query running jobs (continuing): {exc}")
        running_jobs = []

    hot_paths = _read_manifest_hot_paths(manifest_path)

    def _canon_path(path: Path) -> str:
        try:
            return str(path.expanduser().resolve())
        except Exception:
            return str(path)

    simulate_broken_paths = {_canon_path(Path(p)) for p in simulate_broken_raw if str(p).strip()}

    detected: dict[str, dict] = {}
    simulated: dict[str, dict] = {}

    # Probe running job output dirs (primary signal).
    for job in running_jobs:
        if not job.output_dir:
            continue
        out_dir = Path(job.output_dir)
        ok, errno = _probe_readable_dir(out_dir)
        if ok == 0 and errno == 107:
            key = f"job:{job.job_id}"
            mount_info = _get_mount_info(str(out_dir)) or {}
            detected[key] = {
                "kind": "job_output_dir",
                "job_id": job.job_id,
                "source": job.source_code,
                "path": str(out_dir),
                "errno": errno,
                "mount": mount_info,
            }
            impacted_sources.add(job.source_code)
            # Log diagnostics for root cause investigation
            _log_stale_mount_diagnostics(
                str(out_dir), job.job_id, mount_info, storagebox_mount=storagebox_mount
            )

    # Probe output dirs for the next queued/retryable jobs (secondary signal; prevents retry storms).
    for job in next_jobs:
        if not job.output_dir:
            continue
        out_dir = Path(job.output_dir)
        ok, errno = _probe_readable_dir(out_dir)
        if ok == 0 and errno == 107:
            key = f"next_job:{job.job_id}"
            mount_info = _get_mount_info(str(out_dir)) or {}
            detected[key] = {
                "kind": "next_job_output_dir",
                "job_id": job.job_id,
                "source": job.source_code,
                "status": job.status,
                "path": str(out_dir),
                "errno": errno,
                "mount": mount_info,
            }
            # Log diagnostics for next jobs too (helps understand scope of issue)
            _log_stale_mount_diagnostics(
                str(out_dir), job.job_id, mount_info, storagebox_mount=storagebox_mount
            )

    # Probe manifest hot paths (secondary signal; catches imports/etc).
    for hot in hot_paths:
        ok, errno = _probe_readable_dir(hot)
        if ok == 0 and errno == 107:
            key = f"hot:{hot}"
            mount_info = _get_mount_info(str(hot)) or {}
            detected[key] = {
                "kind": "tiering_hot_path",
                "path": str(hot),
                "errno": errno,
                "mount": mount_info,
            }
            # Log diagnostics for tiering hot paths
            _log_stale_mount_diagnostics(
                str(hot), None, mount_info, storagebox_mount=storagebox_mount
            )

    if simulate_broken_paths:
        running_by_output_dir = {
            _canon_path(Path(job.output_dir)): job for job in running_jobs if job.output_dir
        }
        hot_by_path = {_canon_path(p): p for p in hot_paths}
        detected_paths = {str(info.get("path") or "") for info in detected.values()}

        for raw in sorted(simulate_broken_paths):
            if raw in detected_paths:
                continue

            job = running_by_output_dir.get(raw)
            hot = hot_by_path.get(raw)
            kind = "simulated_path"
            info: dict[str, object] = {"simulated": True}
            if job is not None and job.output_dir is not None:
                kind = "job_output_dir"
                info.update(
                    {
                        "kind": kind,
                        "job_id": int(job.job_id),
                        "source": str(job.source_code),
                        "path": str(job.output_dir),
                        "errno": 107,
                        "mount": _get_mount_info(str(job.output_dir)) or {},
                    }
                )
                impacted_sources.add(str(job.source_code))
            elif hot is not None:
                kind = "tiering_hot_path"
                info.update(
                    {
                        "kind": kind,
                        "path": str(hot),
                        "errno": 107,
                        "mount": _get_mount_info(str(hot)) or {},
                    }
                )
            else:
                info.update(
                    {
                        "kind": kind,
                        "path": raw,
                        "errno": 107,
                        "mount": _get_mount_info(raw) or {},
                    }
                )

            simulated[f"simulated:{raw}"] = info

    # Track observations and confirm persistence across runs.
    obs = state.setdefault("observations", {})
    if not isinstance(obs, dict):
        obs = {}
        state["observations"] = obs

    seen_keys = set(detected.keys())
    for key, info in detected.items():
        entry = obs.get(key)
        if not isinstance(entry, dict) or int(entry.get("errno") or 0) != 107:
            entry = {
                "first_seen_utc": now.replace(microsecond=0).isoformat(),
                "consecutive": 0,
                "errno": 107,
            }
        entry["last_seen_utc"] = now.replace(microsecond=0).isoformat()
        entry["consecutive"] = int(entry.get("consecutive") or 0) + 1
        entry["path"] = str(info.get("path") or "")
        entry["kind"] = str(info.get("kind") or "")
        if "job_id" in info:
            entry["job_id"] = int(info["job_id"])
            entry["source"] = str(info.get("source") or "")
        obs[key] = entry

    # Prune old observations that did not reproduce on this run.
    for key in list(obs.keys()):
        if key not in seen_keys:
            del obs[key]

    eligible: list[dict] = []
    for key, info in detected.items():
        entry = obs.get(key) or {}
        first_seen = _parse_utc(str(entry.get("first_seen_utc") or "")) or now
        age = max(0.0, (now - first_seen).total_seconds())
        consecutive = int(entry.get("consecutive") or 0)
        if consecutive >= int(args.confirm_runs) and age >= float(args.min_failure_age_seconds):
            eligible.append(info)

    if simulated:
        eligible.extend(simulated.values())

    last_apply_ok = int(state.get("last_apply_ok") or 0)
    last_apply_epoch = int(state.get("last_apply_epoch") or 0)

    def finish(rc: int) -> int:
        try:
            _write_metrics(
                out_dir=Path(str(args.textfile_out_dir)),
                out_file=str(args.textfile_out_file),
                sentinel_file=sentinel_file,
                now_utc=now,
                state=state,
                metrics_ok=1,
                last_apply_ok=int(state.get("last_apply_ok") or last_apply_ok),
                last_apply_epoch=int(state.get("last_apply_epoch") or last_apply_epoch),
                detected_targets=(len(detected) + len(simulated)),
                deploy_lock_active=int(deploy_lock_active),
                deploy_lock_age_seconds=deploy_lock_age_seconds,
            )
        except Exception:
            pass
        _save_state(state_path, state)
        return rc

    if not eligible:
        if not simulate_mode and (len(detected) + len(simulated)) == 0:
            state["last_healthy_utc"] = now.replace(microsecond=0).isoformat()
            state["last_healthy_epoch"] = _dt_to_epoch_seconds(now)
        return finish(0)

    # Rate limiting (bypassed in drill simulation mode).
    hour_cutoff = now - timedelta(hours=1)
    day_cutoff = now - timedelta(days=1)
    if simulate_mode:
        print(
            "DRILL: simulate-broken-path active; bypassing recovery cooldown/caps (dry-run only)."
        )
        rec = state.get("recoveries") or {}
        global_items: list[str] = []
    else:
        rec = state.get("recoveries") or {}
        global_items = rec.get("global") if isinstance(rec, dict) else []
        if not isinstance(global_items, list):
            global_items = []

        last_apply = _parse_utc(str(state.get("last_apply_utc") or ""))
        if last_apply is not None:
            cooldown_age = (now - last_apply).total_seconds()
            if cooldown_age < float(args.cooldown_seconds):
                print(
                    f"SKIP: last recovery was {cooldown_age:.0f}s ago (cooldown={int(args.cooldown_seconds)}s)."
                )
                return finish(0)

        if _count_recent_timestamps(global_items, since_utc=hour_cutoff) >= int(
            args.max_recoveries_per_hour
        ):
            print("SKIP: global hourly recovery cap reached.")
            return finish(0)
        if _count_recent_timestamps(global_items, since_utc=day_cutoff) >= int(
            args.max_recoveries_per_day
        ):
            print("SKIP: global daily recovery cap reached.")
            return finish(0)

    impacted_job_ids: set[int] = set()
    for info in eligible:
        job_id = info.get("job_id")
        if job_id is not None:
            impacted_job_ids.add(int(job_id))

    if impacted_job_ids and not simulate_mode:
        jobs_rec = rec.get("jobs") if isinstance(rec, dict) else {}
        if not isinstance(jobs_rec, dict):
            jobs_rec = {}
        for job_id in sorted(impacted_job_ids):
            items = jobs_rec.get(str(job_id))
            if not isinstance(items, list):
                items = []
            n = _count_recent_timestamps(items, since_utc=day_cutoff)
            if n >= int(args.max_recoveries_per_job_per_day):
                print(
                    f"SKIP: job_id={job_id} per-job recovery cap reached ({n}/{args.max_recoveries_per_job_per_day} in last 24h)."
                )
                return finish(0)

    mode_label = "APPLY" if apply_mode else "DRY-RUN"
    if requested_apply and not apply_mode and deploy_lock_active == 1:
        mode_label = "DRY-RUN (deploy lock active)"
    print(
        f"{mode_label}: detected {len(eligible)} stale target(s) (Errno 107) eligible for recovery."
    )
    for info in eligible:
        kind = str(info.get("kind") or "")
        path = str(info.get("path") or "")
        job_id = info.get("job_id")
        source = info.get("source")
        extra = f" job_id={job_id} source={source}" if job_id is not None else ""
        print(f"  - kind={kind}{extra} path={path}")

    worker_was_active = _systemctl_is_active(str(args.worker_unit))

    stale_mountpoints: list[dict] = []
    for info in eligible:
        path = str(info.get("path") or "")
        if not path:
            continue
        try:
            p = Path(path).resolve()
        except Exception:
            continue
        if not str(p).startswith(f"{jobs_root}/"):
            continue
        mount = info.get("mount") if isinstance(info.get("mount"), dict) else {}
        target = str(mount.get("target") or "")
        errno_detected = int(info.get("errno") or 0)

        # Accept paths that are either:
        # 1. Confirmed mountpoints (target == path from findmnt), OR
        # 2. Detected as Errno 107 (transport endpoint not connected) - this is
        #    strong evidence of a stale FUSE/sshfs bind mount. When _get_mount_info()
        #    fails to retrieve mount details for a stale path, errno 107 itself is
        #    sufficient evidence to attempt unmount.
        is_confirmed_mountpoint = bool(target and target == path)
        is_stale_errno_107 = errno_detected == 107
        if not is_confirmed_mountpoint and not is_stale_errno_107:
            continue

        stale_mountpoints.append(
            {
                "path": path,
                "mount_source": str(mount.get("source") or ""),
                "mount_fstype": str(mount.get("fstype") or ""),
                "errno": errno_detected,
            }
        )

    if not apply_mode:
        storage_ok, storage_errno = _probe_readable_dir(storagebox_mount)
        storage_unit_present = _is_unit_present(str(args.storagebox_unit))
        tiering_script = Path(str(args.tiering_apply_script))
        annual_tiering_script = Path(str(args.annual_output_tiering_script))

        should_quiesce_worker = worker_was_active and (
            bool(impacted_sources) or (running_jobs_query_ok and (not running_jobs))
        )
        allow_running_repairs = bool(should_quiesce_worker)

        print("")
        print("Planned actions (dry-run):")
        if should_quiesce_worker:
            print(
                f"  1) systemctl stop {args.worker_unit} "
                "(if a running job output dir is stale, or when there are no running jobs to prevent mount-repair races)"
            )
        else:
            if not worker_was_active:
                print(f"  1) (skip) worker not active: {args.worker_unit}")
            else:
                print(f"  1) (skip) keep worker running (crawl healthy): {args.worker_unit}")

        if stale_mountpoints:
            print(f"  2) unmount stale mountpoints ({len(stale_mountpoints)}):")
            for item in stale_mountpoints:
                mount_source = item.get("mount_source") or ""
                mount_fstype = item.get("mount_fstype") or ""
                item_errno = item.get("errno", 0)
                if mount_source and mount_fstype:
                    print(f"     - {item['path']} (source={mount_source} fstype={mount_fstype})")
                else:
                    # Mount info unavailable (common for stale Errno 107 mounts)
                    print(f"     - {item['path']} (errno={item_errno}, mount info unavailable)")
        else:
            print("  2) (skip) no stale mountpoints eligible for unmount")

        if storage_ok == 0:
            if storage_unit_present:
                print(
                    f"  3) systemctl restart {args.storagebox_unit} (base mount errno={storage_errno})"
                )
            else:
                print(
                    f"  3) (blocked) base mount unreadable (errno={storage_errno}) and unit missing: {args.storagebox_unit}"
                )
        else:
            print(f"  3) (skip) base Storage Box mount is readable: {storagebox_mount}")

        if tiering_script.is_file():
            print(
                f"  4) {tiering_script} --apply --manifest {manifest_path} --storagebox-mount {storagebox_mount}"
            )
        else:
            print(f"  4) (blocked) tiering apply script not found: {tiering_script}")

        if annual_tiering_script.is_file():
            cmd = (
                f"  5) /opt/healtharchive-backend/.venv/bin/python3 {annual_tiering_script} "
                f"--apply --repair-stale-mounts "
                f"{'--allow-repair-running-jobs ' if allow_running_repairs else ''}"
                f"--year {now.year}"
            )
            print(cmd)
        else:
            print(f"  5) (blocked) annual output tiering script not found: {annual_tiering_script}")

        if impacted_sources:
            for source_code in sorted(impacted_sources):
                print(
                    f"  6) {args.ha_backend} recover-stale-jobs --older-than-minutes {args.recover_older_than_minutes} --apply --source {source_code} --limit 10"
                )
        else:
            print("  6) (skip) no impacted sources detected; no recover-stale-jobs call planned")

        if worker_was_active and impacted_sources:
            print(f"  7) systemctl start {args.worker_unit}")
        else:
            if not worker_was_active:
                print("  7) (skip) worker was not active; will not start it")
            else:
                print("  7) (skip) worker was kept running; no start needed")

        if _is_unit_present(str(args.replay_unit)):
            print(
                f"  8) systemctl restart {args.replay_unit} (best-effort; replay should see clean mounts)"
            )
            if _is_unit_present(str(args.replay_smoke_unit)):
                print(f"  9) systemctl start {args.replay_smoke_unit} (best-effort)")
        else:
            print(f"  8) (skip) replay unit not present: {args.replay_unit}")

        return finish(0)

    # Apply recovery.
    state["last_apply_utc"] = now.replace(microsecond=0).isoformat()
    state["last_apply_ok"] = 0
    state["last_apply_epoch"] = int(_dt_to_epoch_seconds(now))

    _record_global_recovery(state, when_utc=now)
    for job_id in sorted(impacted_job_ids):
        _record_job_recovery(state, job_id, when_utc=now)

    critical_errors: list[str] = []
    warnings: list[str] = []

    def note_err(
        label: str, cp: subprocess.CompletedProcess[str], *, critical: bool = True
    ) -> None:
        s = f"{label}: rc={cp.returncode}"
        if cp.stdout:
            s += f" stdout={cp.stdout.strip()[:400]}"
        if cp.stderr:
            s += f" stderr={cp.stderr.strip()[:400]}"
        if critical:
            critical_errors.append(s)
        else:
            warnings.append(s)

    worker_was_active = _systemctl_is_active(str(args.worker_unit))
    worker_stopped = False
    should_quiesce_worker = worker_was_active and (
        bool(impacted_sources) or (running_jobs_query_ok and (not running_jobs))
    )
    if should_quiesce_worker:
        cp = _run_apply(["systemctl", "stop", str(args.worker_unit)], timeout_seconds=30)
        if cp.returncode != 0:
            note_err("systemctl stop worker failed", cp, critical=True)
            state["last_apply_ok"] = 0
            state["last_apply_errors"] = critical_errors
            state["last_apply_warnings"] = warnings
            return finish(1)
        worker_stopped = True

    stale_mountpoints: list[dict] = []
    for info in eligible:
        path = str(info.get("path") or "")
        if not path:
            continue
        try:
            p = Path(path).resolve()
        except Exception:
            continue
        if not str(p).startswith(f"{jobs_root}/"):
            continue
        mount = info.get("mount") if isinstance(info.get("mount"), dict) else {}
        target = str(mount.get("target") or "")
        errno_detected = int(info.get("errno") or 0)

        # Accept paths that are either:
        # 1. Confirmed mountpoints (target == path from findmnt), OR
        # 2. Detected as Errno 107 (transport endpoint not connected) - this is
        #    strong evidence of a stale FUSE/sshfs bind mount. When _get_mount_info()
        #    fails to retrieve mount details for a stale path, errno 107 itself is
        #    sufficient evidence to attempt unmount.
        is_confirmed_mountpoint = bool(target and target == path)
        is_stale_errno_107 = errno_detected == 107
        if not is_confirmed_mountpoint and not is_stale_errno_107:
            continue

        stale_mountpoints.append(
            {
                "path": path,
                "mount_source": str(mount.get("source") or ""),
                "mount_fstype": str(mount.get("fstype") or ""),
                "errno": errno_detected,
            }
        )

    did_unmount = False
    for item in stale_mountpoints:
        path = item["path"]
        cp = _run_apply(["umount", path], timeout_seconds=10)
        if cp.returncode == 0:
            did_unmount = True
            continue
        cp2 = _run_apply(["umount", "-l", path], timeout_seconds=10)
        if cp2.returncode != 0:
            note_err(f"umount failed path={path}", cp2, critical=True)
        else:
            did_unmount = True

    storage_ok, storage_errno = _probe_readable_dir(storagebox_mount)
    did_restart_storagebox = False
    did_apply_tiering = False
    if storage_ok == 0:
        if _is_unit_present(str(args.storagebox_unit)):
            cp = _run_apply(["systemctl", "restart", str(args.storagebox_unit)], timeout_seconds=60)
            if cp.returncode != 0:
                note_err("systemctl restart storagebox failed", cp, critical=True)
            else:
                did_restart_storagebox = True
                deadline = time.monotonic() + float(args.restart_wait_seconds)
                while time.monotonic() < deadline:
                    ok, _errno = _probe_readable_dir(storagebox_mount)
                    if ok == 1:
                        storage_ok = 1
                        storage_errno = -1
                        break
                    time.sleep(float(args.restart_probe_interval_seconds))
                if storage_ok == 0:
                    critical_errors.append(
                        f"storagebox mount still unreadable after restart: errno={storage_errno}"
                    )
        else:
            critical_errors.append(
                f"storagebox mount unhealthy (errno={storage_errno}) and unit missing: {args.storagebox_unit}"
            )

    if storage_ok == 1:
        tiering_script = str(args.tiering_apply_script)
        if Path(tiering_script).is_file():
            cp = _run_apply(
                [
                    tiering_script,
                    "--apply",
                    "--manifest",
                    str(manifest_path),
                    "--storagebox-mount",
                    str(storagebox_mount),
                ],
                timeout_seconds=120,
            )
            if cp.returncode != 0:
                note_err("tiering bind mounts apply failed", cp, critical=False)
            else:
                did_apply_tiering = True
        else:
            warnings.append(f"tiering apply script not found: {tiering_script}")

        annual_tiering_script = str(args.annual_output_tiering_script)
        if Path(annual_tiering_script).is_file():
            cp = _run_apply(
                [
                    "/opt/healtharchive-backend/.venv/bin/python3",
                    annual_tiering_script,
                    "--apply",
                    "--repair-stale-mounts",
                    *(["--allow-repair-running-jobs"] if worker_stopped else []),
                    "--year",
                    str(now.year),
                ],
                timeout_seconds=300,
            )
            if cp.returncode != 0:
                # Treat as a warning; the post-check below is the source of truth for whether mounts were repaired.
                note_err("annual output tiering failed", cp, critical=False)
        else:
            warnings.append(f"annual output tiering script not found: {annual_tiering_script}")

    # Recover stale jobs for impacted sources only.
    for source_code in sorted(impacted_sources):
        cp = _run_apply(
            [
                str(args.ha_backend),
                "recover-stale-jobs",
                "--older-than-minutes",
                str(int(args.recover_older_than_minutes)),
                "--apply",
                "--source",
                source_code,
                "--limit",
                "10",
            ],
            timeout_seconds=120,
        )
        if cp.returncode != 0:
            note_err(f"recover-stale-jobs failed source={source_code}", cp, critical=False)

    # Post-check: ensure previously-stale mountpoints are readable again.
    # Primary success indicator is readability; mount info is secondary.
    post_ok = True
    for item in stale_mountpoints:
        path = item["path"]
        ok, errno = _probe_readable_dir(Path(path))
        if ok == 1:
            # Path is readable - success, even if _get_mount_info() fails
            continue
        # Path is still unreadable - check if mount was restored
        mount = _get_mount_info(path) or {}
        target = str(mount.get("target") or "")
        if target != path:
            post_ok = False
            critical_errors.append(
                f"mountpoint not restored for path={path} (target={target or '??'}, errno={errno})"
            )
        else:
            post_ok = False
            critical_errors.append(
                f"path still unreadable after recovery: path={path} errno={errno}"
            )

    if critical_errors:
        post_ok = False

    # If we changed mounts, restart replay so it sees a clean view of /srv/healtharchive/jobs.
    if post_ok and (did_unmount or did_restart_storagebox or did_apply_tiering):
        if _is_unit_present(str(args.replay_unit)):
            cp = _run_apply(["systemctl", "restart", str(args.replay_unit)], timeout_seconds=60)
            if cp.returncode != 0:
                note_err("systemctl restart replay failed", cp, critical=False)
        if _is_unit_present(str(args.replay_smoke_unit)):
            # Best-effort: a missing sentinel (ConditionPathExists) will cause the unit to skip.
            _run_apply(["systemctl", "start", str(args.replay_smoke_unit)], timeout_seconds=60)

    if post_ok and worker_stopped:
        cp = _run_apply(["systemctl", "start", str(args.worker_unit)], timeout_seconds=30)
        if cp.returncode != 0:
            note_err("systemctl start worker failed", cp, critical=True)
            post_ok = False

    state["last_apply_ok"] = 1 if post_ok else 0
    state["last_apply_errors"] = critical_errors
    state["last_apply_warnings"] = warnings

    return finish(0 if post_ok else 1)


if __name__ == "__main__":
    raise SystemExit(main())
