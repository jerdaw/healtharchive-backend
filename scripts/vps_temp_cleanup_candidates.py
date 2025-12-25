#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from ha_backend.archive_storage import compute_tree_bytes
from ha_backend.db import get_session
from ha_backend.models import ArchiveJob, Source


def _dt_str(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat()


def _human_bytes(n: int) -> str:
    if n < 0:
        return f"{n} B"
    units = ["B", "KiB", "MiB", "GiB", "TiB"]
    value = float(n)
    for unit in units:
        if value < 1024.0 or unit == units[-1]:
            if unit == "B":
                return f"{int(value)} {unit}"
            return f"{value:.2f} {unit}"
        value /= 1024.0
    return f"{int(n)} B"


def _host_du_bytes(path: Path) -> Optional[int]:
    if not shutil.which("du"):
        return None
    try:
        out = subprocess.check_output(
            ["du", "-sb", str(path)], text=True, stderr=subprocess.DEVNULL
        )
        return int(out.strip().split()[0])
    except Exception:
        return None


@dataclass(frozen=True)
class Candidate:
    job_id: int
    source_code: str
    status: str
    cleanup_status: str
    name: str
    output_dir: str
    tmp_dir_count: int
    tmp_bytes_total: int | None
    newest_tmp_mtime: float | None


SAFE_STATUSES = {"indexed", "index_failed"}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "HealthArchive VPS helper: report jobs that still have `.tmp*` directories "
            "and are safe to cleanup (indexed/index_failed)."
        )
    )
    parser.add_argument(
        "--limit", type=int, default=30, help="Max candidates to display (default: 30)."
    )
    parser.add_argument(
        "--json", action="store_true", default=False, help="Emit machine-readable JSON output."
    )
    args = parser.parse_args(argv)

    with get_session() as session:
        rows = (
            session.query(
                ArchiveJob.id,
                Source.code,
                ArchiveJob.status,
                ArchiveJob.cleanup_status,
                ArchiveJob.name,
                ArchiveJob.output_dir,
            )
            .join(Source, ArchiveJob.source_id == Source.id)
            .filter(ArchiveJob.status.in_(sorted(SAFE_STATUSES)))
            .order_by(ArchiveJob.id.desc())
            .limit(200)
            .all()
        )

    candidates: list[Candidate] = []
    for job_id, source_code, status, cleanup_status, name, output_dir_value in rows:
        output_dir = Path(str(output_dir_value)).resolve()
        if not output_dir.is_dir():
            continue
        tmp_dirs = [p for p in output_dir.glob(".tmp*") if p.is_dir()]
        if not tmp_dirs:
            continue

        newest_mtime: float | None = None
        for p in tmp_dirs:
            try:
                newest_mtime = max(newest_mtime or 0.0, float(p.stat().st_mtime))
            except OSError:
                continue

        tmp_total_b: int | None = 0
        for p in tmp_dirs:
            size_b = _host_du_bytes(p)
            if size_b is None:
                try:
                    size_b = compute_tree_bytes(p)
                except Exception:
                    size_b = None
            if size_b is None:
                tmp_total_b = None
                break
            tmp_total_b += int(size_b)

        candidates.append(
            Candidate(
                job_id=int(job_id),
                source_code=str(source_code),
                status=str(status),
                cleanup_status=str(cleanup_status or "none"),
                name=str(name),
                output_dir=str(output_dir),
                tmp_dir_count=len(tmp_dirs),
                tmp_bytes_total=tmp_total_b,
                newest_tmp_mtime=newest_mtime,
            )
        )

    def sort_key(c: Candidate) -> tuple[int, int]:
        size = -1 if c.tmp_bytes_total is None else int(c.tmp_bytes_total)
        return (size, int(c.job_id))

    candidates_sorted = sorted(candidates, key=sort_key, reverse=True)[: int(args.limit)]

    payload = {
        "candidates": [
            {
                "jobId": c.job_id,
                "source": c.source_code,
                "status": c.status,
                "cleanupStatus": c.cleanup_status,
                "name": c.name,
                "outputDir": c.output_dir,
                "tmpDirCount": c.tmp_dir_count,
                "tmpBytesTotal": c.tmp_bytes_total,
                "newestTmpMtimeUtc": datetime.fromtimestamp(c.newest_tmp_mtime, tz=timezone.utc)
                .replace(microsecond=0)
                .isoformat()
                if c.newest_tmp_mtime is not None
                else None,
            }
            for c in candidates_sorted
        ],
        "notes": {
            "safeCleanupCommand": "ha-backend cleanup-job --id <JOB_ID> --mode temp-nonwarc",
            "dryRunCommand": "ha-backend cleanup-job --id <JOB_ID> --mode temp-nonwarc --dry-run",
        },
    }

    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0

    print("Temp cleanup candidates (safe jobs)")
    print("----------------------------------")
    print(f"Found {len(candidates)} job(s) with leftover .tmp* dirs (indexed/index_failed).")
    if not candidates_sorted:
        print("OK: no cleanup candidates detected.")
        return 0

    print("")
    print("Top candidates:")
    for c in candidates_sorted:
        size = "(unknown)" if c.tmp_bytes_total is None else _human_bytes(int(c.tmp_bytes_total))
        newest = (
            datetime.fromtimestamp(c.newest_tmp_mtime, tz=timezone.utc)
            .replace(microsecond=0)
            .isoformat()
            if c.newest_tmp_mtime is not None
            else "(unknown)"
        )
        print(
            f"- job_id={c.job_id} source={c.source_code} status={c.status} cleanup={c.cleanup_status} "
            f"tmp_dirs={c.tmp_dir_count} tmp_size={size} newest_tmp={newest} name={c.name}"
        )
        print(f"  output_dir={c.output_dir}")
        print(f"  dry_run:   ha-backend cleanup-job --id {c.job_id} --mode temp-nonwarc --dry-run")
        print(f"  apply:     ha-backend cleanup-job --id {c.job_id} --mode temp-nonwarc")

    print("")
    print(
        "NOTE: cleanup-job is safe-by-default when using --mode temp-nonwarc (preserves WARCs for replay)."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
