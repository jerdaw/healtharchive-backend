#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import subprocess
import tomllib
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from ha_backend.db import get_session
from ha_backend.models import ArchiveJob, Source


@dataclass(frozen=True)
class CleanupConfig:
    min_age_days: int
    keep_latest_per_source: int
    max_jobs_per_run: int


@dataclass(frozen=True)
class CleanupCandidate:
    job_id: int
    source_code: str
    name: str
    output_dir: str
    status: str
    cleanup_status: str
    created_at: datetime | None
    finished_at: datetime | None

    def timestamp(self) -> datetime | None:
        return self.finished_at or self.created_at


def _dt_to_epoch_seconds(dt: datetime) -> int:
    return int(dt.astimezone(timezone.utc).timestamp())


def _age_days(dt: datetime, now_utc: datetime) -> float:
    return (now_utc - dt.astimezone(timezone.utc)).total_seconds() / 86400.0


def _emit(lines: list[str], line: str) -> None:
    lines.append(line.rstrip("\n"))


def _load_config(path: Path) -> CleanupConfig:
    raw = tomllib.loads(path.read_text(encoding="utf-8"))
    min_age_days = int(raw.get("min_age_days", 14))
    keep_latest = int(raw.get("keep_latest_per_source", 2))
    max_jobs = int(raw.get("max_jobs_per_run", 1))
    return CleanupConfig(
        min_age_days=min_age_days,
        keep_latest_per_source=keep_latest,
        max_jobs_per_run=max_jobs,
    )


def _iter_candidates(session, sources: Iterable[str] | None) -> list[CleanupCandidate]:
    query = (
        session.query(
            ArchiveJob.id,
            Source.code,
            ArchiveJob.name,
            ArchiveJob.output_dir,
            ArchiveJob.status,
            ArchiveJob.cleanup_status,
            ArchiveJob.created_at,
            ArchiveJob.finished_at,
        )
        .join(Source, ArchiveJob.source_id == Source.id)
        .filter(ArchiveJob.status.in_(["indexed", "index_failed"]))
        .order_by(ArchiveJob.id.desc())
    )
    sources_list = list(sources or [])
    if sources_list:
        query = query.filter(Source.code.in_(sources_list))
    rows = query.all()
    candidates: list[CleanupCandidate] = []
    for (
        job_id,
        source_code,
        name,
        output_dir,
        status,
        cleanup_status,
        created_at,
        finished_at,
    ) in rows:
        candidates.append(
            CleanupCandidate(
                job_id=int(job_id),
                source_code=str(source_code),
                name=str(name),
                output_dir=str(output_dir),
                status=str(status),
                cleanup_status=str(cleanup_status or "none"),
                created_at=created_at,
                finished_at=finished_at,
            )
        )
    return candidates


def _find_ha_backend_bin() -> str:
    preferred = Path("/opt/healtharchive-backend/.venv/bin/ha-backend")
    if preferred.is_file():
        return str(preferred)
    return "ha-backend"


def _run_cleanup(job_id: int, *, apply: bool) -> tuple[bool, int]:
    cmd = [
        _find_ha_backend_bin(),
        "cleanup-job",
        "--id",
        str(job_id),
        "--mode",
        "temp-nonwarc",
    ]
    if not apply:
        cmd.append("--dry-run")
    try:
        subprocess.run(cmd, check=True)  # nosec: B603
        return True, 0
    except subprocess.CalledProcessError as exc:
        return False, int(getattr(exc, "returncode", 1) or 1)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "HealthArchive VPS helper: cleanup temp dirs for indexed jobs "
            "using safe temp-nonwarc mode (sentinel-gated)."
        )
    )
    parser.add_argument(
        "--config",
        default="/opt/healtharchive-backend/ops/automation/cleanup-automation.toml",
        help="Config TOML path.",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        default=False,
        help="Apply cleanup (default is dry-run).",
    )
    parser.add_argument(
        "--out-dir",
        default="/var/lib/node_exporter/textfile_collector",
        help="node_exporter textfile collector directory.",
    )
    parser.add_argument(
        "--out-file",
        default="healtharchive_cleanup.prom",
        help="Output filename under --out-dir.",
    )
    args = parser.parse_args(argv)

    now = datetime.now(timezone.utc)
    metrics_ok = 1
    config: CleanupConfig | None = None
    try:
        config = _load_config(Path(args.config))
    except Exception:
        metrics_ok = 0

    lines: list[str] = []
    _emit(lines, "# HELP healtharchive_cleanup_metrics_ok 1 if script ran.")
    _emit(lines, "# TYPE healtharchive_cleanup_metrics_ok gauge")
    _emit(lines, f"healtharchive_cleanup_metrics_ok {metrics_ok}")
    _emit(
        lines,
        "# HELP healtharchive_cleanup_timestamp_seconds UNIX timestamp when these metrics were generated.",
    )
    _emit(lines, "# TYPE healtharchive_cleanup_timestamp_seconds gauge")
    _emit(lines, f"healtharchive_cleanup_timestamp_seconds {_dt_to_epoch_seconds(now)}")
    _emit(
        lines,
        "# HELP healtharchive_cleanup_candidates_total Jobs eligible for cleanup after filters.",
    )
    _emit(lines, "# TYPE healtharchive_cleanup_candidates_total gauge")
    _emit(
        lines,
        "# HELP healtharchive_cleanup_selected_total Jobs selected for cleanup (capped).",
    )
    _emit(lines, "# TYPE healtharchive_cleanup_selected_total gauge")
    _emit(
        lines,
        "# HELP healtharchive_cleanup_applied_total Jobs actually cleaned in this run.",
    )
    _emit(lines, "# TYPE healtharchive_cleanup_applied_total gauge")
    _emit(
        lines,
        "# HELP healtharchive_cleanup_apply_errors_total Cleanup commands that failed.",
    )
    _emit(lines, "# TYPE healtharchive_cleanup_apply_errors_total gauge")

    if metrics_ok == 0 or config is None:
        out_dir = Path(args.out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        out_file = out_dir / str(args.out_file)
        tmp = out_file.with_suffix(out_file.suffix + f".{os.getpid()}.tmp")
        tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
        tmp.chmod(0o644)
        tmp.replace(out_file)
        return 0

    selected: list[CleanupCandidate] = []
    with get_session() as session:
        rows = _iter_candidates(session, None)
        if not rows:
            _emit(lines, "healtharchive_cleanup_candidates_total 0")
            _emit(lines, "healtharchive_cleanup_selected_total 0")
            _emit(lines, "healtharchive_cleanup_applied_total 0")
            _emit(lines, "healtharchive_cleanup_apply_errors_total 0")
            out_dir = Path(args.out_dir)
            out_dir.mkdir(parents=True, exist_ok=True)
            out_file = out_dir / str(args.out_file)
            tmp = out_file.with_suffix(out_file.suffix + f".{os.getpid()}.tmp")
            tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
            tmp.chmod(0o644)
            tmp.replace(out_file)
            return 0

    candidates = rows

    by_source: dict[str, list[CleanupCandidate]] = {}
    for c in candidates:
        if c.cleanup_status != "none":
            continue
        ts = c.timestamp()
        if ts is None:
            continue
        by_source.setdefault(c.source_code, []).append(c)

    eligible: list[CleanupCandidate] = []
    for source_code, items in by_source.items():
        items_sorted = sorted(
            items,
            key=lambda c: c.timestamp() or datetime.min.replace(tzinfo=timezone.utc),
            reverse=True,
        )
        for c in items_sorted[int(config.keep_latest_per_source) :]:
            ts = c.timestamp()
            if ts is None:
                continue
            if _age_days(ts, now) < float(config.min_age_days):
                continue
            if not Path(c.output_dir).is_dir():
                continue
            eligible.append(c)

    eligible_sorted = sorted(
        eligible,
        key=lambda c: c.timestamp() or datetime.min.replace(tzinfo=timezone.utc),
    )
    selected = eligible_sorted[: int(config.max_jobs_per_run)]

    _emit(lines, f"healtharchive_cleanup_candidates_total {len(eligible_sorted)}")
    _emit(lines, f"healtharchive_cleanup_selected_total {len(selected)}")

    applied = 0
    errors = 0
    for c in selected:
        ok, _rc = _run_cleanup(c.job_id, apply=bool(args.apply))
        if ok:
            applied += 1
        else:
            errors += 1

    _emit(lines, f"healtharchive_cleanup_applied_total {applied}")
    _emit(lines, f"healtharchive_cleanup_apply_errors_total {errors}")

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / str(args.out_file)
    tmp = out_file.with_suffix(out_file.suffix + f".{os.getpid()}.tmp")
    tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
    tmp.chmod(0o644)
    tmp.replace(out_file)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
