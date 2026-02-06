#!/usr/bin/env python3
from __future__ import annotations

import argparse
import fcntl
import json
import os
import re
import shlex
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
DEFAULT_JOB_LOCK_DIR = Path("/tmp/healtharchive-job-locks")
JOB_LOCK_DIR_ENV = "HEALTHARCHIVE_JOB_LOCK_DIR"

DEFAULT_START_WORKING_DIR = "/opt/healtharchive-backend"
DEFAULT_START_ENV_FILE = "/etc/healtharchive/backend.env"
DEFAULT_START_USER = "haadmin"
DEFAULT_START_GROUP = "haadmin"
DEFAULT_START_HA_BACKEND = "/opt/healtharchive-backend/.venv/bin/ha-backend"
DEFAULT_START_UNIT_PREFIX = "healtharchive-job"
DEFAULT_START_DOCKER_CPU_LIMIT = "1.0"
DEFAULT_START_DOCKER_MEMORY_LIMIT = "3g"
DEFAULT_START_DISK_CHECK_PATH = "/srv/healtharchive/jobs"
DEFAULT_START_MAX_DISK_USAGE_PERCENT = 90

# NOTE: Use a real word boundary (\b), not a literal "\b" token.
# This allows matching legacy job names like "phac-20260101-retry1" as well as
# canonical "phac-20260101".
_ANNUAL_JOB_SUFFIX_RE = re.compile(r"-(?P<year>[0-9]{4})0101(?:\b|$)")


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


def _get_job_lock_dir() -> Path:
    raw = os.environ.get(JOB_LOCK_DIR_ENV, "").strip()
    if raw:
        return Path(raw).expanduser()
    return DEFAULT_JOB_LOCK_DIR


def _held_job_lock_job_ids(lock_dir: Path) -> set[int]:
    """
    Return job IDs whose per-job lock file exists and appears held by another process.

    This uses non-blocking flock probes. It's best-effort: unreadable directories/files
    simply result in an empty set.
    """
    try:
        candidates = list(lock_dir.glob("job-*.lock"))
    except OSError:
        return set()

    held: set[int] = set()
    for p in candidates:
        name = p.name
        if not name.startswith("job-") or not name.endswith(".lock"):
            continue
        raw_id = name[len("job-") : -len(".lock")]
        try:
            job_id = int(raw_id)
        except ValueError:
            continue
        try:
            fd = os.open(str(p), os.O_RDONLY)
        except PermissionError:
            # Cannot open the lock file (e.g. user mismatch).  Treat it as
            # potentially held — the conservative choice for recovery decisions
            # that avoids soft-recovering a job whose runner is still alive.
            held.add(job_id)
            continue
        except OSError:
            continue
        try:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                held.add(job_id)
            else:
                try:
                    fcntl.flock(fd, fcntl.LOCK_UN)
                except OSError:
                    pass
        finally:
            os.close(fd)
    return held


@dataclass(frozen=True)
class RunningJob:
    job_id: int
    source_code: str
    started_at: datetime | None
    output_dir: str | None
    combined_log_path: str | None


@dataclass(frozen=True)
class JobRunner:
    """
    Best-effort classification of where a DB job is currently running.

    Invariants we try to uphold:
    - Never flip DB status to retryable while a crawl process is still running.
    - Prefer stopping only the runner for the stalled job (unit vs worker) to avoid
      interrupting unrelated jobs.
    """

    kind: str  # one of: none, worker, systemd_unit, unknown
    unit: str | None = None


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


def _record_start(state: dict, job_id: int, *, when_utc: datetime) -> None:
    starts = state.setdefault("starts", {})
    items = list(starts.get(str(job_id)) or [])
    items.append(when_utc.replace(microsecond=0).isoformat())
    starts[str(job_id)] = items


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


def _count_recent_starts(state: dict, job_id: int, *, since_utc: datetime) -> int:
    starts = state.get("starts", {})
    items = list(starts.get(str(job_id)) or [])
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


def _disk_usage_percent(path: Path) -> int | None:
    try:
        stat = os.statvfs(str(path))
    except OSError:
        return None
    total = stat.f_blocks * stat.f_frsize
    free = stat.f_bavail * stat.f_frsize
    if total <= 0:
        return None
    return int(100 * (total - free) / total)


def _sanitize_systemd_unit_name(raw: str) -> str:
    allowed = set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_.@:-")
    cleaned = "".join(ch if ch in allowed else "-" for ch in raw)
    cleaned = cleaned.strip("-")
    return cleaned or "healtharchive-job"


def _format_start_unit(prefix: str, job_id: int, *, now_utc: datetime) -> str:
    ts = now_utc.strftime("%Y%m%dT%H%M%SZ")
    base = f"{prefix}{job_id}-auto-{ts}"
    return _sanitize_systemd_unit_name(base)


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
    """
    Find the most relevant combined log file for a running job.

    Important: running jobs often have a stale ``combined_log_path`` (set during
    a previous attempt), so we always check both the DB path and the newest log
    on disk under ``output_dir`` and pick whichever is most recent by mtime.
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
        return by_output or by_path


def _run(cmd: list[str]) -> None:
    subprocess.run(cmd, check=True)  # nosec: B603


def _run_capture(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, check=False, capture_output=True, text=True)  # nosec: B603


def _parse_cmd_tokens(raw: str) -> list[str]:
    s = str(raw or "").strip()
    if not s:
        return []
    try:
        return shlex.split(s)
    except ValueError:
        return s.split()


def _looks_like_run_db_job_for_id(cmdline: str, job_id: int) -> bool:
    toks = _parse_cmd_tokens(cmdline)
    if not toks:
        return False
    if "run-db-job" not in toks:
        return False
    # Accept both "--id 8" and "--id=8"
    for i, tok in enumerate(toks):
        if tok == "--id" and i + 1 < len(toks) and toks[i + 1] == str(job_id):
            return True
        if tok.startswith("--id=") and tok.split("=", 1)[1] == str(job_id):
            return True
    return False


def _systemctl_show_value(unit: str, prop: str) -> str | None:
    cp = _run_capture(["systemctl", "show", unit, f"--property={prop}", "--value", "--no-pager"])
    if cp.returncode != 0:
        return None
    return cp.stdout.strip() or None


def _systemctl_main_pid(unit: str) -> int | None:
    raw = _systemctl_show_value(unit, "MainPID")
    if raw is None:
        return None
    try:
        pid = int(raw)
    except ValueError:
        return None
    return pid if pid > 0 else None


def _systemctl_list_running_services() -> list[str]:
    cp = _run_capture(
        [
            "systemctl",
            "list-units",
            "--type=service",
            "--state=running",
            "--no-legend",
            "--no-pager",
        ]
    )
    if cp.returncode != 0:
        return []
    units: list[str] = []
    for line in cp.stdout.splitlines():
        # Format: UNIT LOAD ACTIVE SUB DESCRIPTION
        parts = line.strip().split()
        if not parts:
            continue
        units.append(parts[0])
    return units


def _ps_args_for_pid(pid: int) -> str | None:
    cp = _run_capture(["ps", "-p", str(pid), "-o", "args="])
    if cp.returncode != 0:
        return None
    return cp.stdout.strip() or None


def _find_running_job_unit(job_id: int) -> str | None:
    """
    Attempt to find a running systemd unit whose MainPID is `ha-backend run-db-job --id <job_id>`.

    This is the safest way to stop/restart a detached job without touching the worker.
    """
    for unit in _systemctl_list_running_services():
        # Keep this conservative: scan likely HealthArchive job units only.
        if "healtharchive" not in unit:
            continue
        pid = _systemctl_main_pid(unit)
        if pid is None:
            continue
        cmdline = _ps_args_for_pid(pid) or ""
        if _looks_like_run_db_job_for_id(cmdline, job_id):
            return unit
    return None


@dataclass(frozen=True)
class PsRow:
    pid: int
    ppid: int
    args: str


def _ps_snapshot() -> list[PsRow] | None:
    cp = _run_capture(["ps", "-eo", "pid=,ppid=,args="])
    if cp.returncode != 0:
        return None
    rows: list[PsRow] = []
    for line in cp.stdout.splitlines():
        parts = line.strip().split(maxsplit=2)
        if len(parts) < 3:
            continue
        try:
            pid = int(parts[0])
            ppid = int(parts[1])
        except ValueError:
            continue
        rows.append(PsRow(pid=pid, ppid=ppid, args=parts[2]))
    return rows


def _pid_has_ancestor(pid: int, ancestor_pid: int, parent_by_pid: dict[int, int]) -> bool:
    cur = pid
    for _ in range(64):  # defensive upper bound
        if cur == ancestor_pid:
            return True
        ppid = parent_by_pid.get(cur)
        if ppid is None or ppid <= 0 or ppid == cur:
            return False
        cur = ppid
    return False


def _output_dir_has_running_process(output_dir: str | None, ps_rows: list[PsRow]) -> bool:
    if not output_dir:
        return False
    needle = str(output_dir)
    return any(needle in row.args for row in ps_rows)


def _is_likely_crawl_runner_cmd(args: str) -> bool:
    s = str(args or "")
    if "archive-tool" in s:
        return True
    if "docker run" in s and "ghcr.io/openzim/zimit" in s:
        return True
    return False


def _output_dir_has_running_crawl_process(output_dir: str | None, ps_rows: list[PsRow]) -> bool:
    if not output_dir:
        return False
    needle = str(output_dir)
    return any(needle in row.args and _is_likely_crawl_runner_cmd(row.args) for row in ps_rows)


def _output_dir_running_under_pid(
    output_dir: str | None, *, root_pid: int | None, ps_rows: list[PsRow]
) -> bool:
    if not output_dir or root_pid is None or root_pid <= 0:
        return False
    needle = str(output_dir)
    parent_by_pid = {row.pid: row.ppid for row in ps_rows}
    for row in ps_rows:
        if needle not in row.args:
            continue
        if not _is_likely_crawl_runner_cmd(row.args):
            continue
        if _pid_has_ancestor(row.pid, int(root_pid), parent_by_pid):
            return True
    return False


def _detect_job_runner(
    job: RunningJob,
    *,
    simulate_mode: bool,
    simulate_runner: str | None,
    simulate_runner_unit: str | None,
) -> JobRunner:
    """
    Detect where a job is running (if at all).

    In drill mode, detection can be overridden to prove the planned actions
    without requiring real systemd services or processes.
    """
    if simulate_mode and simulate_runner:
        kind = str(simulate_runner).strip().lower()
        if kind in {"none", "worker", "systemd_unit", "unknown"}:
            unit = str(simulate_runner_unit).strip() if simulate_runner_unit else None
            return JobRunner(kind=kind, unit=unit or None)

    unit = _find_running_job_unit(int(job.job_id))
    if unit:
        return JobRunner(kind="systemd_unit", unit=unit)

    ps_rows = _ps_snapshot()
    if ps_rows is None:
        return JobRunner(kind="unknown")
    worker_pid = _systemctl_main_pid("healtharchive-worker.service")
    if _output_dir_running_under_pid(job.output_dir, root_pid=worker_pid, ps_rows=ps_rows):
        return JobRunner(kind="worker", unit="healtharchive-worker.service")
    if _output_dir_has_running_process(job.output_dir, ps_rows):
        return JobRunner(kind="unknown")

    # No crawl process found, but check if the job lock is held.  The crawl
    # subprocess may have died while the worker (or a run-db-job unit) still
    # owns the job.  Soft recovery cannot work when the lock is held — the
    # caller must stop the runner first.
    held = _held_job_lock_job_ids(_get_job_lock_dir())
    if int(job.job_id) in held:
        if worker_pid is not None and worker_pid > 0:
            return JobRunner(kind="worker", unit="healtharchive-worker.service")
        return JobRunner(kind="unknown")

    return JobRunner(kind="none")


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
    is_annual = campaign_kind == "annual" or _infer_annual_campaign_year(job, cfg) is not None

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


def _infer_annual_campaign_year(job: ArchiveJob, cfg: dict) -> int | None:
    """
    Best-effort annual campaign year inference for legacy jobs.

    Older annual jobs (created before campaign metadata was added) may not have:
      - config.campaign_kind == "annual"
      - config.campaign_year

    In those cases we infer annual campaigns by the canonical YYYY0101 date suffix
    in the job name or output_dir (e.g. "phac-20260101", "__phac-20260101").
    """
    try:
        cfg_year = int(cfg.get("campaign_year") or 0)
    except (TypeError, ValueError):
        cfg_year = 0
    if cfg_year >= 1970:
        return cfg_year

    candidates = [
        str(getattr(job, "name", "") or ""),
        str(getattr(job, "output_dir", "") or ""),
    ]
    output_dir = str(getattr(job, "output_dir", "") or "")
    if output_dir:
        candidates.append(Path(output_dir).name)

    for text in candidates:
        m = _ANNUAL_JOB_SUFFIX_RE.search(text)
        if not m:
            continue
        try:
            year = int(m.group("year"))
        except (TypeError, ValueError):
            continue
        if year >= 1970:
            return year
    return None


def _ensure_annual_campaign_metadata(job: ArchiveJob, *, campaign_year: int) -> bool:
    """
    Backfill missing annual campaign metadata for legacy jobs.

    This makes annual automation (tiering + watchdogs) robust against older jobs
    that predate config.campaign_kind/campaign_year.
    """
    cfg = dict(job.config or {})
    changed = False

    if str(cfg.get("campaign_kind") or "").strip().lower() != "annual":
        cfg["campaign_kind"] = "annual"
        changed = True
    if cfg.get("campaign_year") != campaign_year:
        cfg["campaign_year"] = campaign_year
        changed = True

    # These are informational but help operators/debugging consistency.
    campaign_date = f"{campaign_year}-01-01"
    if cfg.get("campaign_date") is None:
        cfg["campaign_date"] = campaign_date
        changed = True
    if cfg.get("campaign_date_utc") is None:
        cfg["campaign_date_utc"] = f"{campaign_date}T00:00:00Z"
        changed = True
    if cfg.get("scheduler_version") is None:
        cfg["scheduler_version"] = "v1"
        changed = True

    if changed:
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

    starts = state.get("starts", {})
    total_starts = sum(len(v) for v in starts.values() if isinstance(v, list))
    lines.append(
        "# HELP healtharchive_crawl_auto_recover_starts_total Total number of auto-start attempts recorded."
    )
    lines.append("# TYPE healtharchive_crawl_auto_recover_starts_total counter")
    lines.append(f"healtharchive_crawl_auto_recover_starts_total {int(total_starts)}")

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
        "--simulate-stalled-job-runner",
        default=None,
        choices=["none", "worker", "systemd_unit", "unknown"],
        help=(
            "DRILL ONLY: override runner detection for the simulated stalled job. "
            "Use this to validate that the watchdog would stop the correct runner "
            "before marking a job retryable."
        ),
    )
    parser.add_argument(
        "--simulate-stalled-job-runner-unit",
        default=None,
        help=(
            "DRILL ONLY: when using --simulate-stalled-job-runner=systemd_unit, use this unit name "
            "in the planned actions output."
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
    parser.add_argument(
        "--ensure-min-running-jobs",
        type=int,
        default=0,
        help=(
            "Optional: if no stalled jobs are detected, ensure at least this many annual campaign jobs "
            "are running by starting a queued/retryable annual job via systemd-run. "
            "Default: 0 (disabled)."
        ),
    )
    parser.add_argument(
        "--ensure-campaign-year",
        type=int,
        default=None,
        help=(
            "Optional: only consider annual jobs with config campaign_year=YYYY when auto-starting "
            "queued/retryable jobs. Default: current UTC year."
        ),
    )
    parser.add_argument(
        "--max-starts-per-job-per-day",
        type=int,
        default=3,
        help="Safety cap: maximum auto-start attempts per job per 24h.",
    )
    parser.add_argument(
        "--start-unit-prefix",
        default=DEFAULT_START_UNIT_PREFIX,
        help="Prefix for systemd-run unit names created by auto-start.",
    )
    parser.add_argument(
        "--start-working-dir",
        default=DEFAULT_START_WORKING_DIR,
        help="Working directory for auto-started transient units.",
    )
    parser.add_argument(
        "--start-env-file",
        default=DEFAULT_START_ENV_FILE,
        help="EnvironmentFile for auto-started transient units (must include DB URL).",
    )
    parser.add_argument(
        "--start-user", default=DEFAULT_START_USER, help="User for auto-started units."
    )
    parser.add_argument(
        "--start-group", default=DEFAULT_START_GROUP, help="Group for auto-started units."
    )
    parser.add_argument(
        "--start-ha-backend",
        default=DEFAULT_START_HA_BACKEND,
        help="Path to ha-backend CLI for auto-started units.",
    )
    parser.add_argument(
        "--start-docker-cpu-limit",
        default=DEFAULT_START_DOCKER_CPU_LIMIT,
        help=(
            "Value for HEALTHARCHIVE_DOCKER_CPU_LIMIT when auto-starting a job "
            "(passed via systemd-run --setenv)."
        ),
    )
    parser.add_argument(
        "--start-docker-memory-limit",
        default=DEFAULT_START_DOCKER_MEMORY_LIMIT,
        help=(
            "Value for HEALTHARCHIVE_DOCKER_MEMORY_LIMIT when auto-starting a job "
            "(passed via systemd-run --setenv)."
        ),
    )
    parser.add_argument(
        "--start-disk-check-path",
        default=DEFAULT_START_DISK_CHECK_PATH,
        help="Path to use for disk usage safety check before auto-starting jobs.",
    )
    parser.add_argument(
        "--start-max-disk-usage-percent",
        type=int,
        default=DEFAULT_START_MAX_DISK_USAGE_PERCENT,
        help=(
            "Safety: do not auto-start additional jobs when disk usage at --start-disk-check-path "
            "is at or above this percent."
        ),
    )
    args = parser.parse_args(argv)

    now = _utc_now()
    simulate_job_ids_raw = list(getattr(args, "simulate_stalled_job_id", []) or [])
    simulate_job_ids = [int(x) for x in simulate_job_ids_raw if str(x).strip()]
    simulate_mode = bool(simulate_job_ids)
    if bool(getattr(args, "simulate_stalled_job_runner", None)) and not simulate_mode:
        print(
            "ERROR: --simulate-stalled-job-runner is only allowed with --simulate-stalled-job-id.",
            file=sys.stderr,
        )
        return 2
    if (
        simulate_mode
        and bool(getattr(args, "simulate_stalled_job_runner", None))
        and len(simulate_job_ids) != 1
    ):
        print(
            "ERROR: --simulate-stalled-job-runner currently requires exactly one --simulate-stalled-job-id.",
            file=sys.stderr,
        )
        return 2
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

    # Repair DB drift: if a job is clearly running (job lock held) but DB status is not
    # running, sync it back to running so monitoring + stall detection remains accurate.
    held_lock_job_ids = _held_job_lock_job_ids(_get_job_lock_dir())
    if held_lock_job_ids:
        drift_job_ids: list[int] = []
        with get_session() as session:
            for jid in sorted(held_lock_job_ids):
                orm_job = session.get(ArchiveJob, jid)
                if orm_job is None:
                    continue
                if orm_job.status == "running":
                    continue
                drift_job_ids.append(jid)
                if args.apply and not simulate_mode:
                    orm_job.status = "running"
                    if orm_job.started_at is None:
                        orm_job.started_at = now
                    orm_job.finished_at = None
            if args.apply and not simulate_mode and drift_job_ids:
                session.commit()
        if drift_job_ids:
            if args.apply and not simulate_mode:
                print(
                    f"APPLY: synced {len(drift_job_ids)} job(s) to status=running based on held job locks: "
                    + ", ".join(str(x) for x in drift_job_ids)
                )
            else:
                print(
                    f"DRY-RUN: would sync {len(drift_job_ids)} job(s) to status=running based on held job locks: "
                    + ", ".join(str(x) for x in drift_job_ids)
                )

    # Additional drift repair: if a job is running but lacks a lock (e.g., older
    # runner before lock adoption), sync it to running based on active crawl
    # processes using its output_dir.
    ps_rows = _ps_snapshot()
    if ps_rows is not None:
        process_drift_job_ids: list[int] = []
        with get_session() as session:
            rows = (
                session.query(ArchiveJob.id, ArchiveJob.output_dir, ArchiveJob.started_at)
                .filter(ArchiveJob.status != "running")
                .filter(ArchiveJob.started_at.isnot(None))
                .filter(ArchiveJob.finished_at.is_(None))
                .order_by(ArchiveJob.id.asc())
                .all()
            )
            for job_id, output_dir, started_at in rows:
                jid = int(job_id)
                if jid in held_lock_job_ids:
                    continue
                if not _output_dir_has_running_crawl_process(
                    str(output_dir) if output_dir else None, ps_rows
                ):
                    continue
                process_drift_job_ids.append(jid)
                if args.apply and not simulate_mode:
                    orm_job = session.get(ArchiveJob, jid)
                    if orm_job is None:
                        continue
                    orm_job.status = "running"
                    if orm_job.started_at is None:
                        orm_job.started_at = started_at or now
                    orm_job.finished_at = None
            if args.apply and not simulate_mode and process_drift_job_ids:
                session.commit()
        if process_drift_job_ids:
            if args.apply and not simulate_mode:
                print(
                    "APPLY: synced "
                    f"{len(process_drift_job_ids)} job(s) to status=running based on active crawl processes: "
                    + ", ".join(str(x) for x in process_drift_job_ids)
                )
            else:
                print(
                    "DRY-RUN: would sync "
                    f"{len(process_drift_job_ids)} job(s) to status=running based on active crawl processes: "
                    + ", ".join(str(x) for x in process_drift_job_ids)
                )

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
        ensure_min_running = int(getattr(args, "ensure_min_running_jobs", 0) or 0)
        if ensure_min_running <= 0:
            write_metrics(
                running_jobs=len(running_jobs),
                stalled_jobs=0,
                result="skip",
                reason="no_stalled_jobs",
                state=state,
            )
            return 0

        running_count = len(running_jobs)
        if running_count >= ensure_min_running:
            write_metrics(
                running_jobs=running_count,
                stalled_jobs=0,
                result="skip",
                reason="no_stalled_jobs",
                state=state,
            )
            return 0

        ensure_year = (
            int(args.ensure_campaign_year)
            if args.ensure_campaign_year is not None
            else int(now.year)
        )

        disk_percent = _disk_usage_percent(Path(str(args.start_disk_check_path)))
        if disk_percent is not None and disk_percent >= int(args.start_max_disk_usage_percent):
            print(
                f"SKIP: underfilled running jobs ({running_count}/{ensure_min_running}), "
                f"but disk usage at {args.start_disk_check_path} is {disk_percent}% "
                f"(>= {int(args.start_max_disk_usage_percent)}%)."
            )
            write_metrics(
                running_jobs=running_count,
                stalled_jobs=0,
                result="skip",
                reason="start_disk_high",
                state=state,
            )
            return 0

        with get_session() as session:
            rows = (
                session.query(ArchiveJob, Source.code)
                .outerjoin(Source, ArchiveJob.source_id == Source.id)
                .filter(ArchiveJob.status.in_(["queued", "retryable"]))
                .order_by(ArchiveJob.queued_at.asc().nullsfirst(), ArchiveJob.created_at.asc())
                .all()
            )

            candidate: tuple[int, str, str, int, bool] | None = None
            debug_total = 0
            debug_year_counts: dict[int, int] = {}
            debug_examples: list[dict[str, object]] = []
            for orm_job, source_code in rows:
                debug_total += 1
                cfg = orm_job.config or {}
                campaign_kind = str(cfg.get("campaign_kind") or "").strip().lower()
                inferred_year = _infer_annual_campaign_year(orm_job, cfg)
                if inferred_year is not None:
                    debug_year_counts[int(inferred_year)] = (
                        debug_year_counts.get(int(inferred_year), 0) + 1
                    )

                if campaign_kind != "annual" and inferred_year is None:
                    if len(debug_examples) < 5:
                        debug_examples.append(
                            {
                                "job_id": int(orm_job.id),
                                "source": str(source_code) if source_code else "unknown",
                                "status": str(orm_job.status),
                                "reason": "non_annual",
                                "campaign_kind": campaign_kind,
                                "campaign_year": cfg.get("campaign_year"),
                                "inferred_year": inferred_year,
                                "name": getattr(orm_job, "name", None),
                            }
                        )
                    continue
                try:
                    cfg_year = int(cfg.get("campaign_year") or 0)
                except (TypeError, ValueError):
                    cfg_year = 0
                if cfg_year <= 0:
                    cfg_year = int(inferred_year or 0)
                if cfg_year != ensure_year:
                    if len(debug_examples) < 5:
                        debug_examples.append(
                            {
                                "job_id": int(orm_job.id),
                                "source": str(source_code) if source_code else "unknown",
                                "status": str(orm_job.status),
                                "reason": "year_mismatch",
                                "campaign_kind": campaign_kind,
                                "campaign_year": cfg.get("campaign_year"),
                                "inferred_year": inferred_year,
                                "effective_year": cfg_year,
                                "name": getattr(orm_job, "name", None),
                            }
                        )
                    continue
                needs_backfill = (
                    campaign_kind != "annual"
                    or cfg.get("campaign_year") != ensure_year
                    or cfg.get("campaign_date") is None
                    or cfg.get("campaign_date_utc") is None
                    or cfg.get("scheduler_version") is None
                )
                candidate = (
                    int(orm_job.id),
                    str(source_code) if source_code else "unknown",
                    str(orm_job.status),
                    cfg_year,
                    needs_backfill,
                )
                break

        if candidate is None:
            print(
                f"SKIP: underfilled running jobs ({running_count}/{ensure_min_running}), "
                f"but no eligible annual queued/retryable jobs found for campaign_year={ensure_year}."
            )
            if debug_total > 0:
                years = ", ".join(
                    f"{y}:{n}" for y, n in sorted(debug_year_counts.items(), key=lambda x: x[0])
                )
                print(
                    "DEBUG: queued/retryable jobs scanned="
                    f"{debug_total}; inferred_annual_year_counts={{{years}}}."
                )
                for ex in debug_examples:
                    print(
                        "DEBUG: skip_candidate "
                        + " ".join(
                            f"{k}={ex.get(k)!r}"
                            for k in (
                                "job_id",
                                "source",
                                "status",
                                "reason",
                                "campaign_kind",
                                "campaign_year",
                                "inferred_year",
                                "effective_year",
                                "name",
                            )
                            if ex.get(k) is not None
                        )
                    )
            write_metrics(
                running_jobs=running_count,
                stalled_jobs=0,
                result="skip",
                reason="no_start_candidates",
                state=state,
            )
            return 0

        (
            candidate_id,
            candidate_source,
            candidate_status,
            candidate_campaign_year,
            candidate_needs_backfill,
        ) = candidate

        recent_starts = _count_recent_starts(state, candidate_id, since_utc=recent_cutoff)
        if recent_starts >= int(args.max_starts_per_job_per_day):
            print(
                f"SKIP: would auto-start job_id={candidate_id} source={candidate_source} status={candidate_status}, "
                f"but max starts reached ({recent_starts}/{int(args.max_starts_per_job_per_day)} in last 24h)."
            )
            write_metrics(
                running_jobs=running_count,
                stalled_jobs=0,
                result="skip",
                reason="max_starts",
                state=state,
            )
            return 0

        unit = _format_start_unit(str(args.start_unit_prefix), candidate_id, now_utc=now)
        systemd_run_cmd: list[str] = [
            "systemd-run",
            f"--unit={unit}",
            f"--property=WorkingDirectory={str(args.start_working_dir)}",
            f"--property=EnvironmentFile={str(args.start_env_file)}",
            f"--property=User={str(args.start_user)}",
            f"--property=Group={str(args.start_group)}",
        ]
        cpu = str(args.start_docker_cpu_limit or "").strip()
        mem = str(args.start_docker_memory_limit or "").strip()
        if cpu:
            systemd_run_cmd.append(f"--setenv=HEALTHARCHIVE_DOCKER_CPU_LIMIT={cpu}")
        if mem:
            systemd_run_cmd.append(f"--setenv=HEALTHARCHIVE_DOCKER_MEMORY_LIMIT={mem}")
        systemd_run_cmd += [
            str(args.start_ha_backend),
            "run-db-job",
            "--id",
            str(candidate_id),
        ]

        print(
            f"{'APPLY' if args.apply else 'DRY-RUN'}: would auto-start annual job_id={candidate_id} "
            f"source={candidate_source} status={candidate_status} to reach "
            f"ensure-min-running-jobs={ensure_min_running} (currently {running_count})."
        )

        if not args.apply:
            print("")
            print("Planned actions (dry-run):")
            print("  1) " + " ".join(shlex.quote(x) for x in systemd_run_cmd))
            write_metrics(
                running_jobs=running_count,
                stalled_jobs=0,
                result="skip",
                reason="dry_run_start",
                state=state,
            )
            return 0

        # Before starting, ensure annual metadata + self-healing options are set (idempotent).
        try:
            with get_session() as session:
                orm_job = session.get(ArchiveJob, candidate_id)
                if orm_job is not None:
                    changed = False
                    if candidate_needs_backfill and _ensure_annual_campaign_metadata(
                        orm_job, campaign_year=int(candidate_campaign_year)
                    ):
                        changed = True
                    if _ensure_recovery_tool_options(orm_job):
                        changed = True
                    if changed:
                        session.commit()
        except Exception as exc:
            print(f"WARNING: failed to update job config before auto-start: {exc}")

        cp = _run_capture(systemd_run_cmd)
        if cp.returncode != 0:
            print(
                f"ERROR: failed to auto-start job_id={candidate_id} via systemd-run unit={unit}.service",
                file=sys.stderr,
            )
            if cp.stdout.strip():
                print(cp.stdout.rstrip())
            if cp.stderr.strip():
                print(cp.stderr.rstrip(), file=sys.stderr)
            write_metrics(
                running_jobs=running_count,
                stalled_jobs=0,
                result="skip",
                reason="start_failed",
                state=state,
            )
            return 2

        print(f"APPLY: started job_id={candidate_id} via {unit}.service")
        print(f"  Tail logs: journalctl -u {unit}.service -f --no-pager")
        print(f"  Status:    systemctl --no-pager --full status {unit}.service")

        _record_start(state, candidate_id, when_utc=now)
        _save_state(state_path, state)
        write_metrics(
            running_jobs=running_count,
            stalled_jobs=0,
            result="ok",
            reason="started_job",
            state=state,
        )
        return 0

    # Only recover one job per run (worker processes one job at a time).
    job, age = stalled[0]
    runner = _detect_job_runner(
        job,
        simulate_mode=simulate_mode,
        simulate_runner=getattr(args, "simulate_stalled_job_runner", None),
        simulate_runner_unit=getattr(args, "simulate_stalled_job_runner_unit", None),
    )
    guard_seconds = int(args.skip_if_any_job_progress_within_seconds or 0)
    if guard_seconds > 0:
        other_recent = [
            (jid, a)
            for jid, a in progress_age_by_job_id.items()
            if jid != int(job.job_id) and a < float(guard_seconds)
        ]
        if other_recent:
            jid, a = sorted(other_recent, key=lambda item: item[1])[0]
            # If the stalled job is *not* currently running (zombie DB row),
            # soft recovery can safely clean it up without touching the worker.
            #
            # If we can detect an active runner, we must stop it first to avoid
            # leaving an orphaned crawl with DB status != running.
            if runner.kind == "none":
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
                # This is only safe when we detect no active runner (zombie DB row).
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

            print(
                f"NOTE: guard window is active (job_id={jid} progressed {a:.0f}s ago), but stalled job_id={job.job_id} "
                f"appears to have an active runner ({runner.kind}); proceeding with recovery."
            )

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
        f"source={job.source_code} stalled_age_seconds={age:.0f} runner={runner.kind}"
    )
    if not args.apply:
        print("")
        print("Planned actions (dry-run):")
        step = 1
        if runner.kind == "systemd_unit" and runner.unit:
            print(f"  {step}) systemctl stop {runner.unit}")
            step += 1
        elif runner.kind == "worker":
            print(f"  {step}) systemctl stop healtharchive-worker.service")
            step += 1
        elif runner.kind == "unknown":
            print(f"  {step}) (skip) runner unknown; operator intervention required")
            step += 1

        print(
            f"  {step}) /opt/healtharchive-backend/.venv/bin/ha-backend recover-stale-jobs "
            f"--older-than-minutes {int(args.recover_older_than_minutes)} "
            f"--require-no-progress-seconds {int(args.stall_threshold_seconds)} "
            f"--apply --source {job.source_code} --limit 5"
        )
        step += 1
        if runner.kind == "systemd_unit" and runner.unit:
            print(f"  {step}) systemctl start {runner.unit}")
        elif runner.kind == "worker":
            print(f"  {step}) systemctl start healtharchive-worker.service")
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

    if runner.kind == "systemd_unit" and runner.unit:
        _run(["systemctl", "stop", runner.unit])
    elif runner.kind == "worker":
        _run(["systemctl", "stop", "healtharchive-worker.service"])
    elif runner.kind == "unknown":
        print(
            f"ERROR: stalled job_id={job.job_id} appears to have an active runner, but could not identify a safe stop target.",
            file=sys.stderr,
        )
        write_metrics(
            running_jobs=len(running_jobs),
            stalled_jobs=len(stalled),
            result="skip",
            reason="runner_unknown",
            state=state,
        )
        return 2
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
    if runner.kind == "systemd_unit" and runner.unit:
        _run(["systemctl", "start", runner.unit])
    elif runner.kind == "worker":
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
