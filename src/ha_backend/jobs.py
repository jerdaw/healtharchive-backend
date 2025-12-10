from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Sequence

from sqlalchemy.orm import Session

from .config import get_archive_tool_config
from .db import get_session
from .models import ArchiveJob as ORMArchiveJob


@dataclass
class RuntimeArchiveJob:
    """
    Minimal representation of a single archive_tool run.

    For Phase 1A this is just an in-memory object that knows:
    - what seeds to crawl
    - what logical name to use
    - where its output directory lives
    and can construct + execute the archive_tool command.
    """

    name: str
    seeds: list[str]

    def _make_job_dir_name(self) -> str:
        """
        Construct a filesystem-friendly directory name that includes
        a UTC timestamp and the job name.

        Example: 20251209T204530Z__restoredcdc
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
                result = subprocess.run(cmd)
            else:
                # Capture output (not used yet, but ready for future log piping).
                result = subprocess.run(cmd, capture_output=True, text=True)
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
    - executes archive_tool using the stored configuration
    - updates status, timestamps, and exit code on completion
    """
    # First session: validate and mark as running, and snapshot configuration.
    with get_session() as session:
        job_row = _load_job_for_update(session, job_id)

        if job_row.status not in ("queued", "retryable"):
            raise ValueError(
                f"Job {job_id} has status {job_row.status!r} and is not runnable."
            )

        config = job_row.config or {}
        tool_options = config.get("tool_options") or {}
        zimit_args = list(config.get("zimit_passthrough_args") or [])
        seeds = list(config.get("seeds") or [])

        if not seeds:
            raise ValueError(
                f"Job {job_id} has no seeds configured; cannot build archive_tool command."
            )

        output_dir_str = job_row.output_dir
        job_name = job_row.name

        now = datetime.now(timezone.utc)
        job_row.status = "running"
        job_row.started_at = now

    # Execute outside of an open Session to keep the database interaction
    # simple and avoid long-lived transactions.
    output_dir = Path(output_dir_str)
    runtime_job = RuntimeArchiveJob(name=job_name, seeds=seeds)

    initial_workers = int(tool_options.get("initial_workers", 1))
    cleanup = bool(tool_options.get("cleanup", False))
    overwrite = bool(tool_options.get("overwrite", False))
    log_level = str(tool_options.get("log_level", "INFO"))

    # Build archive_tool-specific CLI options (before the '--' separator).
    extra_tool_args: list[str] = []

    enable_monitoring = bool(tool_options.get("enable_monitoring", False))
    if enable_monitoring:
        extra_tool_args.append("--enable-monitoring")
        if "monitor_interval_seconds" in tool_options:
            extra_tool_args.extend(
                [
                    "--monitor-interval-seconds",
                    str(tool_options["monitor_interval_seconds"]),
                ]
            )
        if "stall_timeout_minutes" in tool_options:
            extra_tool_args.extend(
                [
                    "--stall-timeout-minutes",
                    str(tool_options["stall_timeout_minutes"]),
                ]
            )
        if "error_threshold_timeout" in tool_options:
            extra_tool_args.extend(
                [
                    "--error-threshold-timeout",
                    str(tool_options["error_threshold_timeout"]),
                ]
            )
        if "error_threshold_http" in tool_options:
            extra_tool_args.extend(
                [
                    "--error-threshold-http",
                    str(tool_options["error_threshold_http"]),
                ]
            )

    enable_adaptive_workers = bool(tool_options.get("enable_adaptive_workers", False))
    if enable_monitoring and enable_adaptive_workers:
        extra_tool_args.append("--enable-adaptive-workers")
        if "min_workers" in tool_options:
            extra_tool_args.extend(
                ["--min-workers", str(tool_options["min_workers"])]
            )
        if "max_worker_reductions" in tool_options:
            extra_tool_args.extend(
                [
                    "--max-worker-reductions",
                    str(tool_options["max_worker_reductions"]),
                ]
            )

    enable_vpn_rotation = bool(tool_options.get("enable_vpn_rotation", False))
    vpn_connect_command = tool_options.get("vpn_connect_command")
    if enable_monitoring and enable_vpn_rotation and vpn_connect_command:
        extra_tool_args.append("--enable-vpn-rotation")
        extra_tool_args.extend(["--vpn-connect-command", str(vpn_connect_command)])
        if "max_vpn_rotations" in tool_options:
            extra_tool_args.extend(
                ["--max-vpn-rotations", str(tool_options["max_vpn_rotations"])]
            )
        if "vpn_rotation_frequency_minutes" in tool_options:
            extra_tool_args.extend(
                [
                    "--vpn-rotation-frequency-minutes",
                    str(tool_options["vpn_rotation_frequency_minutes"]),
                ]
            )

    if enable_monitoring and "backoff_delay_minutes" in tool_options:
        extra_tool_args.extend(
            ["--backoff-delay-minutes", str(tool_options["backoff_delay_minutes"])]
        )

    # Compose final extra args: tool args first, then the Zimit passthrough
    # arguments (no additional '--' separator needed; archive_tool will pass
    # these directly through to zimit).
    full_extra_args: list[str] = list(extra_tool_args)
    if zimit_args:
        full_extra_args.extend(zimit_args)

    rc = runtime_job.run(
        initial_workers=initial_workers,
        cleanup=cleanup,
        overwrite=overwrite,
        log_level=log_level,
        extra_args=full_extra_args,
        stream_output=True,
        output_dir_override=output_dir,
    )

    # Second session: record final status and exit code.
    finished = datetime.now(timezone.utc)
    with get_session() as session:
        job_row = _load_job_for_update(session, job_id)
        job_row.crawler_exit_code = rc
        job_row.finished_at = finished

        if rc == 0:
            job_row.status = "completed"
            job_row.crawler_status = "success"
        else:
            job_row.status = "failed"
            job_row.crawler_status = "failed"

    return rc
