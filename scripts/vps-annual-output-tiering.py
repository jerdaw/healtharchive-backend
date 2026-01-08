#!/usr/bin/env python3
from __future__ import annotations

import argparse
import errno
import os
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ha_backend.db import get_session
from ha_backend.models import ArchiveJob, Source


def _now_year_utc() -> int:
    return datetime.now(timezone.utc).year


def _is_exact_mountpoint(path: Path) -> bool:
    """
    Return True if `path` itself is a mountpoint target.

    Note: this does not require `path` to be stat'able; it uses mount tables so it
    can still detect stale FUSE mountpoints (Errno 107).
    """
    try:
        r = subprocess.run(
            ["findmnt", "-T", str(path), "-o", "TARGET", "-n"],
            check=False,
            capture_output=True,
            text=True,
        )
        if r.returncode == 0 and r.stdout.strip() == str(path):
            return True
    except FileNotFoundError:
        # findmnt not available; fall back to parsing `mount` output.
        pass
    out = subprocess.run(["mount"], check=False, capture_output=True, text=True).stdout
    return f" on {path} " in out


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
    if not st or not st.st_mode:
        return 0, 0
    if not os.path.isdir(path):
        return 0, 0
    try:
        os.listdir(path)
    except OSError as exc:
        return 0, int(exc.errno or -1)
    return 1, -1


def _require_storagebox_mounted(storagebox_mount: Path) -> None:
    if not _is_exact_mountpoint(storagebox_mount):
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
    mount_present: bool
    output_dir_ok: int
    output_dir_errno: int


def _plan(
    *,
    year: int,
    sources: list[str],
    archive_root: Path,
    campaign_archive_root: Path,
    created_after: datetime | None = None,
    created_before: datetime | None = None,
) -> list[TierPlanItem]:
    if created_after is not None or created_before is not None:
        start = created_after or datetime(year, 1, 1, tzinfo=timezone.utc)
        end = created_before or datetime.now(timezone.utc) + timedelta(minutes=5)
    else:
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
            mount_present = _is_exact_mountpoint(output_dir)
            ok, err = _probe_readable_dir(output_dir) if mount_present else (0, 0)
            plan.append(
                TierPlanItem(
                    job_id=int(j.id),
                    source_code=str(src_by_id.get(int(j.source_id or 0), "unknown")),
                    job_name=str(j.name),
                    output_dir=output_dir,
                    cold_dir=cold_dir,
                    mount_present=mount_present,
                    output_dir_ok=int(ok),
                    output_dir_errno=int(err),
                )
            )
        return plan


def _mount_bind(cold_dir: Path, output_dir: Path) -> None:
    try:
        output_dir.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        if e.errno == errno.ENOTCONN:
            raise RuntimeError(
                f"hot path is a stale mountpoint (Errno 107): {output_dir}\n"
                f"Hint: run: sudo umount -l {output_dir}\n"
                "Then re-run this script (or the Phase 2 watchdog)."
            ) from e
        raise
    cold_dir.mkdir(parents=True, exist_ok=True)
    r = subprocess.run(["mount", "--bind", str(cold_dir), str(output_dir)], check=False)
    if r.returncode != 0:
        raise RuntimeError(
            f"mount --bind failed for {output_dir} <= {cold_dir} (rc={r.returncode})"
        )


def _parse_dt(s: str) -> datetime:
    v = s.strip()
    if v.endswith("Z"):
        v = v[:-1] + "+00:00"
    dt = datetime.fromisoformat(v)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


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
        "--created-after",
        help=(
            "Override the default selection window; include annual jobs created at/after this UTC timestamp "
            "(ISO 8601, e.g. 2026-01-01T00:00:00Z). Useful for rehearsals before Jan 01."
        ),
    )
    p.add_argument(
        "--created-before",
        help=(
            "Override the default selection window; include annual jobs created before this UTC timestamp "
            "(ISO 8601, e.g. 2026-01-03T00:00:00Z). Useful for rehearsals."
        ),
    )
    p.add_argument(
        "--apply", action="store_true", help="Actually perform bind mounts (default: dry-run)."
    )
    p.add_argument(
        "--repair-stale-mounts",
        action="store_true",
        default=False,
        help=(
            "If an existing output_dir mountpoint is stale (Errno 107), attempt a targeted unmount and retry. "
            "Use only during maintenance (stop the worker first)."
        ),
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
    created_after = _parse_dt(str(args.created_after)) if args.created_after else None
    created_before = _parse_dt(str(args.created_before)) if args.created_before else None
    if created_after is not None and created_before is not None and created_before < created_after:
        print("ERROR: --created-before must be >= --created-after", file=sys.stderr)
        return 2

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
        created_after=created_after,
        created_before=created_before,
    )

    print("HealthArchive annual output tiering")
    print("-----------------------------------")
    print(f"mode={'APPLY' if args.apply else 'DRY-RUN'} year={year} sources={', '.join(sources)}")
    print(f"archive_root={archive_root}")
    print(f"campaign_archive_root={campaign_archive_root}")
    if created_after is not None or created_before is not None:
        print(
            f"created_window_utc=[{created_after.isoformat() if created_after else '-inf'}, "
            f"{created_before.isoformat() if created_before else '+inf'}]"
        )
    print("")

    if not plan:
        print("No annual jobs found in the expected window; nothing to do.")
        return 0

    errors: list[str] = []
    changed = 0
    for item in plan:
        if item.mount_present:
            if item.output_dir_ok == 1:
                print(
                    f"OK   job={item.job_id} {item.source_code} {item.job_name} (already mounted)"
                )
                continue

            if item.output_dir_errno == errno.ENOTCONN:
                print(f"STALE job={item.job_id} {item.source_code} {item.job_name} (Errno 107)")
                print(f"     hot={item.output_dir}")
                print(
                    "     Hint: sudo umount -l <hot> then re-run tiering or the Phase 2 watchdog."
                )
                if not args.apply:
                    continue
                if not args.repair_stale_mounts:
                    errors.append(
                        f"stale mountpoint (Errno 107) at output_dir={item.output_dir} (job_id={item.job_id})"
                    )
                    continue

                r = subprocess.run(["umount", str(item.output_dir)], check=False)
                if r.returncode != 0:
                    r2 = subprocess.run(["umount", "-l", str(item.output_dir)], check=False)
                    if r2.returncode != 0:
                        errors.append(
                            f"failed to unmount stale mountpoint at {item.output_dir} (rc={r2.returncode})"
                        )
                        continue
            else:
                print(f"UNHEALTHY job={item.job_id} {item.source_code} {item.job_name}")
                print(f"     hot={item.output_dir} errno={item.output_dir_errno}")
                if args.apply:
                    errors.append(
                        f"output_dir mountpoint unreadable (errno={item.output_dir_errno}) at {item.output_dir}"
                    )
                continue

        print(f"MOUNT job={item.job_id} {item.source_code} {item.job_name}")
        print(f"     hot={item.output_dir}")
        print(f"     cold={item.cold_dir}")
        if args.apply:
            try:
                _mount_bind(item.cold_dir, item.output_dir)
            except Exception as e:
                errors.append(
                    f"mount failed for job_id={item.job_id} output_dir={item.output_dir}: {e}"
                )
                continue
        changed += 1

    print("")
    print(f"planned={len(plan)} mounted_now={changed}")
    if errors:
        print("ERROR: one or more mounts failed:", file=sys.stderr)
        for e in errors:
            print(f"- {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
