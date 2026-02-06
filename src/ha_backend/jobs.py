from __future__ import annotations

import fcntl
import logging
import os
import stat
import subprocess  # nosec: B404 - expected for running archive_tool
import sys
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Iterator, Sequence

from sqlalchemy.orm import Session

from .archive_contract import ArchiveJobConfig, ArchiveToolOptions
from .config import get_archive_tool_config
from .crawl_stats import update_job_stats_from_logs
from .db import get_session
from .infra_errors import (
    is_output_dir_write_infra_error,
    is_storage_infra_errno,
    is_storage_infra_error,
)
from .models import ArchiveJob as ORMArchiveJob

logger = logging.getLogger("healtharchive.jobs")

# Maximum number of retries for infra errors before giving up.
# This should match the value in worker/main.py to maintain consistent behavior.
# Note: normal failures are handled by worker/main.py; this cap is for infra_error
# jobs which otherwise bypass the retry budget.
MAX_INFRA_ERROR_RETRIES = 5

DEFAULT_JOB_LOCK_DIR = Path("/tmp/healtharchive-job-locks")
JOB_LOCK_DIR_ENV = "HEALTHARCHIVE_JOB_LOCK_DIR"


class JobAlreadyRunningError(RuntimeError):
    def __init__(self, job_id: int, lock_path: Path) -> None:
        super().__init__(f"Job {job_id} appears to already be running (lock held): {lock_path}")
        self.job_id = int(job_id)
        self.lock_path = str(lock_path)


def _get_job_lock_dir() -> Path:
    raw = os.environ.get(JOB_LOCK_DIR_ENV, "").strip()
    if raw:
        return Path(raw).expanduser()
    return DEFAULT_JOB_LOCK_DIR


@contextmanager
def _job_lock(job_id: int) -> Iterator[Path]:
    lock_dir = _get_job_lock_dir()
    try:
        lock_dir.mkdir(parents=True, exist_ok=True)
        try:
            lock_dir.chmod(0o1777)
        except OSError:
            pass
    except OSError:
        lock_dir = Path("/tmp")

    lock_path = lock_dir / f"job-{int(job_id)}.lock"
    try:
        # Open without O_CREAT first to avoid fs.protected_regular (sysctl)
        # restrictions in world-writable sticky directories like /tmp.
        # When O_CREAT is used on an existing file in a sticky dir, the kernel
        # returns EACCES if the caller doesn't own the file.
        fd = os.open(str(lock_path), os.O_RDWR)
    except FileNotFoundError:
        fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR, 0o666)
    try:
        try:
            os.fchmod(fd, 0o666)
        except OSError:
            pass
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise JobAlreadyRunningError(int(job_id), lock_path) from exc
        try:
            os.lseek(fd, 0, os.SEEK_SET)
            os.ftruncate(fd, 0)
            os.write(fd, f"pid={os.getpid()}\n".encode("utf-8"))
        except OSError:
            pass
        yield lock_path
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        except OSError:
            pass
        os.close(fd)


# --- Helper predicates for error classification ---


def _has_remaining_infra_error_retries(retry_count: int) -> bool:
    """Check if job has remaining infra error retry budget."""
    return retry_count < MAX_INFRA_ERROR_RETRIES


def _should_retry_as_infra_error(retry_count: int) -> bool:
    """Determine if job should be retried due to infra error."""
    return _has_remaining_infra_error_retries(retry_count)


def _check_output_dir_is_accessible_directory(output_dir: Path) -> bool:
    """
    Check if output directory exists and is accessible.

    Returns:
        True if directory is accessible, False if there's a config/layout problem.

    Raises:
        OSError: If there's a storage infrastructure error (errno 107, 13, etc.)
    """
    st = output_dir.stat()
    return stat.S_ISDIR(st.st_mode)


_LOG_CONFIG_ERROR_MARKERS = (
    "unrecognized arguments",
    "unknown option",
)

# Markers indicating infrastructure/permission failures that should be
# classified as infra_error (retryable) rather than permanent failure.
_LOG_INFRA_ERROR_MARKERS = (
    "is invalid or not writable",
    "permission denied",
    "transport endpoint is not connected",
    "[errno 107]",  # ENOTCONN
    "[errno 13]",  # EACCES
    "[errno 1]",  # EPERM
)


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
            mtime = float(p.stat().st_mtime)
        except OSError:
            continue
        if latest_mtime is None or mtime > latest_mtime:
            latest = p
            latest_mtime = mtime
    return latest


def _read_log_tail(path: Path, *, max_bytes: int = 64 * 1024) -> str:
    if max_bytes <= 0:
        return ""
    try:
        size = path.stat().st_size
    except OSError:
        return ""
    try:
        with open(path, "rb") as f:
            f.seek(max(0, int(size) - int(max_bytes)))
            return f.read().decode("utf-8", errors="replace")
    except Exception:
        return ""


def _looks_like_config_error_from_log(tail: str) -> bool:
    lower = tail.lower()
    return any(marker in lower for marker in _LOG_CONFIG_ERROR_MARKERS)


def _looks_like_infra_error_from_log(tail: str) -> bool:
    """
    Check if the log tail indicates an infrastructure/permission error
    that should be classified as infra_error (retryable).
    """
    lower = tail.lower()
    return any(marker in lower for marker in _LOG_INFRA_ERROR_MARKERS)


@dataclass
class RuntimeArchiveJob:
    """
    Minimal representation of a single archive_tool run.

    This is an in-memory object that knows:
    - what seeds to crawl
    - what logical name to use
    - where its output directory lives
    and can construct + execute the archive_tool CLI command implemented by
    the in-repo ``archive_tool`` package.
    """

    name: str
    seeds: list[str]

    def _make_job_dir_name(self) -> str:
        """
        Construct a filesystem-friendly directory name that includes
        a UTC timestamp and the job name.

        Example: 20251209T204530Z__hc-2025-12-09
        """
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        safe_name = self.name.strip().replace(" ", "_")
        if not safe_name:
            safe_name = "job"
        return f"{ts}__{safe_name}"

    def ensure_job_dir(self, archive_root: Path) -> Path:
        """
        Ensure the specific job directory exists under the archive root
        and return its path.
        """
        job_dir = archive_root / self._make_job_dir_name()
        job_dir.mkdir(parents=True, exist_ok=False)  # fail if it already exists
        return job_dir

    def build_command(
        self,
        *,
        output_dir: Path,
        initial_workers: int = 1,
        cleanup: bool = True,
        overwrite: bool = False,
        log_level: str = "INFO",
        extra_args: Sequence[str] | None = None,
    ) -> list[str]:
        """
        Build the archive_tool command-line for this job.
        """
        cfg = get_archive_tool_config()

        cmd: list[str] = [
            cfg.archive_tool_cmd,
            "--seeds",
            *self.seeds,
            "--name",
            self.name,
            "--output-dir",
            str(output_dir),
            "--initial-workers",
            str(initial_workers),
            "--log-level",
            log_level,
        ]

        if cleanup:
            cmd.append("--cleanup")
        if overwrite:
            cmd.append("--overwrite")

        if extra_args:
            cmd.extend(extra_args)

        return cmd

    def run(
        self,
        *,
        initial_workers: int = 1,
        cleanup: bool = True,
        overwrite: bool = False,
        log_level: str = "INFO",
        extra_args: Sequence[str] | None = None,
        stream_output: bool = True,
        output_dir_override: Path | None = None,
    ) -> int:
        """
        Execute archive_tool for this job.

        Returns the exit code of the subprocess.
        """
        cfg = get_archive_tool_config()
        cfg.ensure_archive_root()

        if output_dir_override is not None:
            job_dir = output_dir_override
            # Allow creating nested directories for source-specific layout and
            # reuse an existing directory for resume-like behavior.
            job_dir.mkdir(parents=True, exist_ok=True)
        else:
            job_dir = self.ensure_job_dir(cfg.archive_root)

        cmd = self.build_command(
            output_dir=job_dir,
            initial_workers=initial_workers,
            cleanup=cleanup,
            overwrite=overwrite,
            log_level=log_level,
            extra_args=extra_args,
        )

        print("HealthArchive Backend – Run Job")
        print("--------------------------------")
        print(f"Job name:    {self.name}")
        print(f"Job seeds:   {', '.join(self.seeds)}")
        print(f"Job dir:     {job_dir}")
        print(f"Command:     {' '.join(cmd)}")
        print("")

        try:
            if stream_output:
                # Stream directly to this terminal.
                # Command and arguments are derived from configuration and job
                # metadata, not raw end-user input.
                result = subprocess.run(cmd, text=True)  # nosec: B603
            else:
                # Capture output (not used yet, but ready for future log piping).
                result = subprocess.run(  # nosec: B603
                    cmd,
                    capture_output=True,
                    text=True,
                )
                print(result.stdout)
                print(result.stderr)
            return result.returncode
        except KeyboardInterrupt:
            # archive_tool will already have received SIGINT and tried to shut down.
            print("\n[ha-backend] Job interrupted by user (Ctrl-C).", file=sys.stderr)
            # Conventional code for SIGINT.
            return 130


def create_job(name: str, seeds: Iterable[str]) -> RuntimeArchiveJob:
    """
    Convenience function to build an ArchiveJob from basic inputs.
    """
    return RuntimeArchiveJob(name=name, seeds=list(seeds))


# Backwards-compatible alias for callers that imported ArchiveJob directly.
ArchiveJob = RuntimeArchiveJob


def _build_tool_extra_args(tool_options: ArchiveToolOptions) -> list[str]:
    """
    Build archive_tool-specific CLI options from ArchiveToolOptions.
    """
    extra_tool_args: list[str] = []

    docker_image = (tool_options.docker_image or "").strip()
    if docker_image:
        extra_tool_args.extend(["--docker-image", docker_image])

    docker_shm_size = (getattr(tool_options, "docker_shm_size", None) or "").strip()
    if docker_shm_size:
        extra_tool_args.extend(["--docker-shm-size", docker_shm_size])

    enable_monitoring = bool(tool_options.enable_monitoring)
    if enable_monitoring:
        extra_tool_args.append("--enable-monitoring")
        if tool_options.monitor_interval_seconds is not None:
            extra_tool_args.extend(
                [
                    "--monitor-interval-seconds",
                    str(tool_options.monitor_interval_seconds),
                ]
            )
        if tool_options.stall_timeout_minutes is not None:
            extra_tool_args.extend(
                [
                    "--stall-timeout-minutes",
                    str(tool_options.stall_timeout_minutes),
                ]
            )
        if tool_options.error_threshold_timeout is not None:
            extra_tool_args.extend(
                [
                    "--error-threshold-timeout",
                    str(tool_options.error_threshold_timeout),
                ]
            )
        if tool_options.error_threshold_http is not None:
            extra_tool_args.extend(
                [
                    "--error-threshold-http",
                    str(tool_options.error_threshold_http),
                ]
            )

    enable_adaptive_workers = bool(tool_options.enable_adaptive_workers)
    if enable_monitoring and enable_adaptive_workers:
        extra_tool_args.append("--enable-adaptive-workers")
        if tool_options.min_workers is not None:
            extra_tool_args.extend(["--min-workers", str(tool_options.min_workers)])
        if tool_options.max_worker_reductions is not None:
            extra_tool_args.extend(
                [
                    "--max-worker-reductions",
                    str(tool_options.max_worker_reductions),
                ]
            )

    enable_vpn_rotation = bool(tool_options.enable_vpn_rotation)
    vpn_connect_command = tool_options.vpn_connect_command
    if enable_monitoring and enable_vpn_rotation and vpn_connect_command:
        extra_tool_args.append("--enable-vpn-rotation")
        extra_tool_args.extend(["--vpn-connect-command", str(vpn_connect_command)])
        if tool_options.max_vpn_rotations is not None:
            extra_tool_args.extend(["--max-vpn-rotations", str(tool_options.max_vpn_rotations)])
        if tool_options.vpn_rotation_frequency_minutes is not None:
            extra_tool_args.extend(
                [
                    "--vpn-rotation-frequency-minutes",
                    str(tool_options.vpn_rotation_frequency_minutes),
                ]
            )

    enable_adaptive_restart = bool(tool_options.enable_adaptive_restart)
    if enable_monitoring and enable_adaptive_restart:
        extra_tool_args.append("--enable-adaptive-restart")
        if tool_options.max_container_restarts is not None:
            extra_tool_args.extend(
                ["--max-container-restarts", str(tool_options.max_container_restarts)]
            )

    if enable_monitoring and tool_options.backoff_delay_minutes is not None:
        extra_tool_args.extend(["--backoff-delay-minutes", str(tool_options.backoff_delay_minutes)])

    if bool(tool_options.relax_perms):
        extra_tool_args.append("--relax-perms")

    if bool(getattr(tool_options, "skip_final_build", False)):
        extra_tool_args.append("--skip-final-build")

    return extra_tool_args


def _load_job_for_update(session: Session, job_id: int) -> ORMArchiveJob:
    job = session.get(ORMArchiveJob, job_id)
    if job is None:
        raise ValueError(f"ArchiveJob with id={job_id} does not exist.")
    return job


def run_persistent_job(job_id: int) -> int:
    """
    Run a database-backed ArchiveJob by ID.

    This function:
    - loads the ORM job row
    - marks it as running
    - executes the archive_tool CLI using the stored configuration
    - updates status, timestamps, and exit code on completion

    The mapping from ``tool_options`` to CLI flags is intentionally kept in
    sync with the argument model in ``archive_tool.cli``; if you change one
    side, update the other.
    """
    with _job_lock(job_id):
        # First session: validate and mark as running, and snapshot configuration.
        with get_session() as session:
            job_row = _load_job_for_update(session, job_id)

            if job_row.status not in ("queued", "retryable"):
                raise ValueError(f"Job {job_id} has status {job_row.status!r} and is not runnable.")

            raw_config = job_row.config or {}
            job_cfg = ArchiveJobConfig.from_dict(raw_config)
            seeds = list(job_cfg.seeds)
            zimit_args = list(job_cfg.zimit_passthrough_args)
            tool_options = job_cfg.tool_options

            if not seeds:
                raise ValueError(
                    f"Job {job_id} has no seeds configured; cannot build archive_tool command."
                )

            output_dir_str = job_row.output_dir
            job_name = job_row.name

            now = datetime.now(timezone.utc)
            job_row.status = "running"
            job_row.started_at = now
            job_row.finished_at = None
            job_row.crawler_exit_code = None
            job_row.crawler_status = None
            job_row.crawler_stage = None
            job_row.combined_log_path = None

        # Execute outside of an open Session to keep the database interaction
        # simple and avoid long-lived transactions.
        output_dir = Path(output_dir_str)
        runtime_job = RuntimeArchiveJob(name=job_name, seeds=seeds)

        initial_workers = int(tool_options.initial_workers)
        cleanup = bool(tool_options.cleanup)
        overwrite = bool(tool_options.overwrite)
        log_level = str(tool_options.log_level)

        # Build archive_tool-specific CLI options (before the '--' separator).
        extra_tool_args: list[str] = _build_tool_extra_args(tool_options)

        # Compose final extra args: tool args first, then the Zimit passthrough
        # arguments (no additional '--' separator needed; archive_tool will pass
        # these directly through to zimit).
        full_extra_args: list[str] = list(extra_tool_args)
        if zimit_args:
            full_extra_args.extend(zimit_args)

        rc: int | None = None
        run_exc: Exception | None = None
        try:
            rc = runtime_job.run(
                initial_workers=initial_workers,
                cleanup=cleanup,
                overwrite=overwrite,
                log_level=log_level,
                extra_args=full_extra_args,
                stream_output=True,
                output_dir_override=output_dir,
            )
        except Exception as exc:  # noqa: BLE001 - intentional boundary around runtime execution
            run_exc = exc
            logger.warning("Job %s raised during archive_tool execution: %s", job_id, exc)

        # Second session: record final status and exit code.
        finished = datetime.now(timezone.utc)
        with get_session() as session:
            job_row = _load_job_for_update(session, job_id)
            job_row.finished_at = finished

            combined_log_path = _find_latest_combined_log(Path(job_row.output_dir))
            if combined_log_path is not None:
                job_row.combined_log_path = str(combined_log_path)

            if run_exc is not None:
                if is_storage_infra_error(run_exc) or is_output_dir_write_infra_error(
                    run_exc, output_dir=output_dir
                ):
                    # Check retry cap to prevent infinite infra_error retries
                    if job_row.retry_count < MAX_INFRA_ERROR_RETRIES:
                        job_row.status = "retryable"
                        job_row.crawler_status = "infra_error"
                    else:
                        logger.warning(
                            "Job %s exceeded max infra error retries (%d); marking as failed.",
                            job_id,
                            MAX_INFRA_ERROR_RETRIES,
                        )
                        job_row.status = "failed"
                        job_row.crawler_status = "infra_error"
                else:
                    job_row.status = "failed"
                    job_row.crawler_status = "infra_error_config"
                job_row.crawler_exit_code = None
                return 1

            if rc is None:
                job_row.status = "failed"
                job_row.crawler_status = "infra_error_config"
                job_row.crawler_exit_code = None
                return 1
            job_row.crawler_exit_code = rc

            if rc == 0:
                job_row.status = "completed"
                job_row.crawler_status = "success"
            else:
                infra = False
                try:
                    if not _check_output_dir_is_accessible_directory(Path(job_row.output_dir)):
                        # Not a directory; treat as configuration/layout problem
                        job_row.status = "failed"
                        job_row.crawler_status = "infra_error_config"
                        job_row.crawler_exit_code = rc
                        return rc
                except OSError as exc:
                    infra = is_storage_infra_errno(exc.errno) or is_output_dir_write_infra_error(
                        exc, output_dir=Path(job_row.output_dir)
                    )

                if infra:
                    if _should_retry_as_infra_error(job_row.retry_count):
                        job_row.status = "retryable"
                        job_row.crawler_status = "infra_error"
                    else:
                        logger.warning(
                            "Job %s exceeded max infra error retries (%d); marking as failed.",
                            job_id,
                            MAX_INFRA_ERROR_RETRIES,
                        )
                        job_row.status = "failed"
                        job_row.crawler_status = "infra_error"
                else:
                    # Classify common CLI/runtime errors (e.g. invalid Zimit args) as
                    # infra_error_config so the worker doesn't churn retry budget.
                    #
                    # Note: we keep this heuristic intentionally narrow and only
                    # inspect the combined log tail to avoid reading large logs.
                    if combined_log_path is not None:
                        tail = _read_log_tail(combined_log_path)
                        # Check for infra errors from log (e.g. permission denied,
                        # transport endpoint not connected) before config errors.
                        # These are retryable since the underlying storage may recover.
                        if _looks_like_infra_error_from_log(tail):
                            if _should_retry_as_infra_error(job_row.retry_count):
                                job_row.status = "retryable"
                                job_row.crawler_status = "infra_error"
                            else:
                                logger.warning(
                                    "Job %s exceeded max infra error retries (%d); marking as failed.",
                                    job_id,
                                    MAX_INFRA_ERROR_RETRIES,
                                )
                                job_row.status = "failed"
                                job_row.crawler_status = "infra_error"
                            job_row.crawler_exit_code = rc
                            return int(rc)
                        if _looks_like_config_error_from_log(tail):
                            job_row.status = "failed"
                            job_row.crawler_status = "infra_error_config"
                            job_row.crawler_exit_code = rc
                            return int(rc)
                    job_row.status = "failed"
                    job_row.crawler_status = "failed"

            # Best-effort stats sync from archive_tool logs; failures are logged
            # inside the helper and should not interfere with status updates.
            if job_row.crawler_status not in {"infra_error", "infra_error_config"}:
                update_job_stats_from_logs(job_row)

        return int(rc)
