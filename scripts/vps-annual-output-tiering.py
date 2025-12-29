#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from ha_backend.db import get_session
from ha_backend.models import ArchiveJob, Source


def _now_year_utc() -> int:
    return datetime.now(timezone.utc).year


def _is_mountpoint(path: Path) -> bool:
    if not path.exists():
        return False
    try:
        r = subprocess.run(["mountpoint", "-q", str(path)], check=False)
        return r.returncode == 0
    except FileNotFoundError:
        pass
    out = subprocess.run(["mount"], check=False, capture_output=True, text=True).stdout
    return f" on {path} " in out


def _require_storagebox_mounted(storagebox_mount: Path) -> None:
    if not _is_mountpoint(storagebox_mount):
        raise RuntimeError(f"Storage Box is not mounted at: {storagebox_mount}")
    # Also ensure it's readable.
    try:
        os.listdir(storagebox_mount)
    except OSError as e:
        raise RuntimeError(f"Storage Box mount is not readable: {storagebox_mount} ({e})") from e


def _cold_path_for_output_dir(
    *,
    output_dir: Path,
    archive_root: Path,
    campaign_archive_root: Path,
) -> Path:
    out = output_dir.resolve()
    hot = archive_root.resolve()
    cold_root = campaign_archive_root.resolve()
    try:
        rel = out.relative_to(hot)
    except ValueError as e:
        raise ValueError(f"output_dir is not under archive_root: {out} (root={hot})") from e
    return cold_root / rel


@dataclass(frozen=True)
class TierPlanItem:
    job_id: int
    source_code: str
    job_name: str
    output_dir: Path
    cold_dir: Path
    already_mounted: bool


def _plan(
    *,
    year: int,
    sources: list[str],
    archive_root: Path,
    campaign_archive_root: Path,
) -> list[TierPlanItem]:
    start = datetime(year, 1, 1, tzinfo=timezone.utc)
    end = datetime(year, 1, 3, tzinfo=timezone.utc)

    with get_session() as session:
        src_rows = session.query(Source).filter(Source.code.in_(sources)).all()
        src_by_id = {int(s.id): str(s.code) for s in src_rows}

        jobs = (
            session.query(ArchiveJob)
            .filter(ArchiveJob.source_id.in_(list(src_by_id.keys())))
            .filter(ArchiveJob.created_at >= start)
            .filter(ArchiveJob.created_at < end)
            .order_by(ArchiveJob.created_at.asc())
            .all()
        )

        plan: list[TierPlanItem] = []
        for j in jobs:
            cfg = j.config or {}
            if cfg.get("campaign_kind") != "annual":
                continue
            if int(cfg.get("campaign_year") or 0) != int(year):
                continue

            output_dir = Path(str(j.output_dir))
            cold_dir = _cold_path_for_output_dir(
                output_dir=output_dir,
                archive_root=archive_root,
                campaign_archive_root=campaign_archive_root,
            )
            plan.append(
                TierPlanItem(
                    job_id=int(j.id),
                    source_code=str(src_by_id.get(int(j.source_id or 0), "unknown")),
                    job_name=str(j.name),
                    output_dir=output_dir,
                    cold_dir=cold_dir,
                    already_mounted=_is_mountpoint(output_dir),
                )
            )
        return plan


def _mount_bind(cold_dir: Path, output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    cold_dir.mkdir(parents=True, exist_ok=True)
    r = subprocess.run(["mount", "--bind", str(cold_dir), str(output_dir)], check=False)
    if r.returncode != 0:
        raise RuntimeError(
            f"mount --bind failed for {output_dir} <= {cold_dir} (rc={r.returncode})"
        )


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description=(
            "Ensure annual campaign job output directories are bind-mounted onto the Storage Box tier "
            "so crawls write to cold storage while DB paths remain stable."
        )
    )
    p.add_argument(
        "--year",
        type=int,
        default=_now_year_utc(),
        help="Campaign year (default: current UTC year).",
    )
    p.add_argument(
        "--sources",
        nargs="+",
        default=["hc", "phac", "cihr"],
        help="Source codes to consider (default: hc phac cihr).",
    )
    p.add_argument(
        "--archive-root",
        default=os.environ.get("HEALTHARCHIVE_ARCHIVE_ROOT", "/srv/healtharchive/jobs"),
        help="Canonical archive root (default: HEALTHARCHIVE_ARCHIVE_ROOT or /srv/healtharchive/jobs).",
    )
    p.add_argument(
        "--campaign-archive-root",
        default="/srv/healtharchive/storagebox/jobs",
        help="Cold tier root (default: /srv/healtharchive/storagebox/jobs).",
    )
    p.add_argument(
        "--storagebox-mount",
        default="/srv/healtharchive/storagebox",
        help="Storage Box mountpoint on the VPS (default: /srv/healtharchive/storagebox).",
    )
    p.add_argument(
        "--apply", action="store_true", help="Actually perform bind mounts (default: dry-run)."
    )
    args = p.parse_args(argv)

    year = int(args.year)
    sources = [str(s).strip().lower() for s in list(args.sources or []) if str(s).strip()]
    if not sources:
        print("ERROR: no sources requested", file=sys.stderr)
        return 2

    archive_root = Path(str(args.archive_root))
    campaign_archive_root = Path(str(args.campaign_archive_root))
    storagebox_mount = Path(str(args.storagebox_mount))

    if year < 1970 or year > 2100:
        print(f"ERROR: invalid year: {year}", file=sys.stderr)
        return 2

    try:
        _require_storagebox_mounted(storagebox_mount)
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1

    plan = _plan(
        year=year,
        sources=sources,
        archive_root=archive_root,
        campaign_archive_root=campaign_archive_root,
    )

    print("HealthArchive annual output tiering")
    print("-----------------------------------")
    print(f"mode={'APPLY' if args.apply else 'DRY-RUN'} year={year} sources={', '.join(sources)}")
    print(f"archive_root={archive_root}")
    print(f"campaign_archive_root={campaign_archive_root}")
    print("")

    if not plan:
        print("No annual jobs found in the expected window; nothing to do.")
        return 0

    changed = 0
    for item in plan:
        if item.already_mounted:
            print(f"OK   job={item.job_id} {item.source_code} {item.job_name} (already mounted)")
            continue
        print(f"MOUNT job={item.job_id} {item.source_code} {item.job_name}")
        print(f"     hot={item.output_dir}")
        print(f"     cold={item.cold_dir}")
        if args.apply:
            _mount_bind(item.cold_dir, item.output_dir)
        changed += 1

    print("")
    print(f"planned={len(plan)} mounted_now={changed}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
