#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import re
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


@dataclass(frozen=True)
class RunConfig:
    job_id: int
    unit: str
    working_dir: Path
    env_file: Path
    user: str
    group: str
    ha_backend: Path
    retry_first: bool


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _format_unit(prefix: str, job_id: int, *, now_utc: datetime) -> str:
    ts = now_utc.strftime("%Y%m%dT%H%M%SZ")
    raw = f"{prefix}{job_id}-{ts}"
    return re.sub(r"[^A-Za-z0-9_.@:-]+", "-", raw)


def build_systemd_run_cmd(cfg: RunConfig) -> list[str]:
    cmd: list[str] = [
        "systemd-run",
        f"--unit={cfg.unit}",
        f"--property=WorkingDirectory={cfg.working_dir}",
        f"--property=EnvironmentFile={cfg.env_file}",
        f"--property=User={cfg.user}",
        f"--property=Group={cfg.group}",
        str(cfg.ha_backend),
        "run-db-job",
        "--id",
        str(cfg.job_id),
    ]
    return cmd


def _run(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, check=False, capture_output=True, text=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=("Run a HealthArchive DB-backed crawl job detached via systemd-run.")
    )
    parser.add_argument("--id", type=int, action="append", required=True, help="Job ID.")
    parser.add_argument(
        "--unit-prefix",
        default="healtharchive-job",
        help="Prefix for the generated systemd unit name.",
    )
    parser.add_argument(
        "--unit",
        default=None,
        help="Explicit unit name (only allowed with a single --id).",
    )
    parser.add_argument(
        "--working-dir",
        default="/opt/healtharchive-backend",
        help="Working directory for the command.",
    )
    parser.add_argument(
        "--env-file",
        default="/etc/healtharchive/backend.env",
        help="EnvironmentFile path for systemd.",
    )
    parser.add_argument(
        "--ha-backend",
        default="/opt/healtharchive-backend/.venv/bin/ha-backend",
        help="Path to the ha-backend CLI.",
    )
    parser.add_argument("--user", default="haadmin", help="User for the transient unit.")
    parser.add_argument("--group", default="haadmin", help="Group for the transient unit.")
    parser.add_argument(
        "--retry-first",
        action="store_true",
        help="Run `ha-backend retry-job` before dispatching the unit.",
    )

    args = parser.parse_args(argv)

    if os.geteuid() != 0:
        print("ERROR: must run this helper as root (uses systemd-run with User=...).")
        return 2

    job_ids = [int(x) for x in (args.id or [])]
    if not job_ids:
        print("ERROR: missing --id")
        return 2
    if args.unit and len(job_ids) != 1:
        print("ERROR: --unit is only allowed with a single --id.")
        return 2

    now = _utc_now()
    base_working_dir = Path(str(args.working_dir))
    env_file = Path(str(args.env_file))
    ha_backend = Path(str(args.ha_backend))

    rc = 0
    for job_id in job_ids:
        unit_name = (
            str(args.unit)
            if args.unit
            else _format_unit(str(args.unit_prefix), job_id, now_utc=now)
        )
        cfg = RunConfig(
            job_id=job_id,
            unit=unit_name,
            working_dir=base_working_dir,
            env_file=env_file,
            user=str(args.user),
            group=str(args.group),
            ha_backend=ha_backend,
            retry_first=bool(args.retry_first),
        )

        if cfg.retry_first:
            retry_cmd = [str(cfg.ha_backend), "retry-job", "--id", str(cfg.job_id)]
            retry = _run(retry_cmd)
            if retry.returncode != 0:
                print(f"WARNING: retry-job failed for {cfg.job_id}: rc={retry.returncode}")
                if retry.stderr.strip():
                    print(retry.stderr.rstrip())

        cmd = build_systemd_run_cmd(cfg)
        result = _run(cmd)
        if result.returncode != 0:
            rc = 1
            print(f"ERROR: failed to start {cfg.job_id} as unit {cfg.unit}.service")
            if result.stdout.strip():
                print(result.stdout.rstrip())
            if result.stderr.strip():
                print(result.stderr.rstrip())
            continue

        print(f"OK: started job_id={cfg.job_id} via {cfg.unit}.service")
        print(f"  Tail logs: journalctl -u {cfg.unit}.service -f --no-pager")
        print(f"  Status:    systemctl --no-pager --full status {cfg.unit}.service")

    return rc


if __name__ == "__main__":
    raise SystemExit(main())
