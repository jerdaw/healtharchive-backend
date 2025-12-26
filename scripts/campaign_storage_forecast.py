#!/usr/bin/env python3
from __future__ import annotations

import argparse
import math
import os
import shutil
import subprocess
from dataclasses import dataclass
from datetime import timezone
from pathlib import Path
from typing import Any, Iterable, Optional

from ha_backend.db import get_session
from ha_backend.models import ArchiveJob, Source

GiB = 1024**3

ANNUAL_SOURCES_ORDERED = ("hc", "phac", "cihr")

# If a source has never been crawled, estimate its footprint using a similar
# "proxy" source when possible.
PROXY_SOURCES: dict[str, str] = {
    "phac": "hc",  # both are path-scoped sections of www.canada.ca
}

# Fallback estimates when we cannot find any representative historical job.
# Keep these conservative: the point is to fail preflight when we can't prove headroom.
DEFAULT_ESTIMATES_GIB: dict[str, int] = {
    "hc": 20,
    "phac": 20,
    "cihr": 8,
}

FINISHED_STATUSES = {"indexed", "completed", "index_failed"}


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


def _has_flag(args: Iterable[str], flag: str) -> bool:
    prefix = f"{flag}="
    for a in args:
        if a == flag or a.startswith(prefix):
            return True
    return False


def _job_is_capped(job: ArchiveJob) -> bool:
    cfg = job.config or {}
    zimit_args = cfg.get("zimit_passthrough_args") or []
    if not isinstance(zimit_args, list):
        return False
    zimit_args_s = [str(x) for x in zimit_args]
    return _has_flag(zimit_args_s, "--pageLimit") or _has_flag(zimit_args_s, "--depth")


def _docker_tree_kib(path: Path) -> Optional[int]:
    """
    Best-effort: run `du -sk` inside a root container so we can measure trees
    even when host perms are too strict.
    """
    if not shutil.which("docker"):
        return None
    resolved = path.resolve()
    if not resolved.exists():
        return None

    cmd = [
        "docker",
        "run",
        "--rm",
        "-v",
        f"{resolved}:/output:ro",
        "alpine",
        "sh",
        "-c",
        "du -sk /output | awk '{print $1}'",
    ]
    try:
        out = subprocess.check_output(cmd, text=True, stderr=subprocess.STDOUT).strip()
    except Exception:
        return None

    try:
        return int(out)
    except ValueError:
        return None


def _host_tree_bytes(path: Path) -> Optional[int]:
    """
    Best-effort: use host `du -sb` when accessible.
    """
    if not shutil.which("du"):
        return None
    resolved = path.resolve()
    if not resolved.exists():
        return None
    try:
        out = subprocess.check_output(
            ["du", "-sb", str(resolved)], text=True, stderr=subprocess.DEVNULL
        )
        first = out.strip().split()[0]
        return int(first)
    except Exception:
        return None


def _measure_output_dir_bytes(output_dir: Path) -> tuple[Optional[int], str]:
    """
    Return (bytes, method_label).
    """
    b = _host_tree_bytes(output_dir)
    if b is not None:
        return b, "host_du"

    kib = _docker_tree_kib(output_dir)
    if kib is not None:
        return int(kib) * 1024, "docker_du_kib"

    return None, "unknown"


@dataclass(frozen=True)
class ReferenceJob:
    source_code: str
    job_id: int
    job_name: str
    status: str
    output_dir: str
    capped: bool
    output_bytes_total: int
    storage_scanned_at: str | None


def _pick_reference_job(jobs: list[ArchiveJob], source_code: str) -> Optional[ReferenceJob]:
    """
    Choose a "representative" historical job to use for storage estimates.
    Prefer finished, uncapped jobs with persisted storage accounting; fall back
    to finished uncapped jobs, then any finished job.
    """
    finished = [j for j in jobs if (j.status or "") in FINISHED_STATUSES]
    if not finished:
        return None

    def score(j: ArchiveJob) -> tuple[int, int, int]:
        is_uncapped = 1 if not _job_is_capped(j) else 0
        has_storage = 1 if (getattr(j, "storage_scanned_at", None) is not None) else 0
        has_output_bytes = 1 if int(getattr(j, "output_bytes_total", 0) or 0) > 0 else 0
        return (is_uncapped, has_storage, has_output_bytes)

    chosen = sorted(finished, key=score, reverse=True)[0]

    scanned_at = None
    if getattr(chosen, "storage_scanned_at", None) is not None:
        scanned_at = chosen.storage_scanned_at.astimezone(timezone.utc).isoformat()

    return ReferenceJob(
        source_code=source_code,
        job_id=int(chosen.id),
        job_name=str(chosen.name),
        status=str(chosen.status),
        output_dir=str(chosen.output_dir),
        capped=bool(_job_is_capped(chosen)),
        output_bytes_total=int(getattr(chosen, "output_bytes_total", 0) or 0),
        storage_scanned_at=scanned_at,
    )


def _pick_latest_job_dir_under_archive_root(archive_root: Path, source_code: str) -> Optional[Path]:
    """
    Best-effort fallback when the DB isn't available: try to infer the latest
    per-source job output directory from the archive root layout:

      <archive_root>/<source_code>/<timestamp>__<job_name>
    """
    source_dir = archive_root / source_code
    if not source_dir.is_dir():
        return None
    candidates: list[tuple[float, str, Path]] = []
    try:
        it = list(source_dir.iterdir())
    except OSError:
        return None
    for p in it:
        if not p.is_dir():
            continue
        try:
            st = p.stat()
        except OSError:
            continue
        candidates.append((float(st.st_mtime), p.name, p))
    if not candidates:
        return None
    candidates.sort(reverse=True)
    return candidates[0][2]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Estimate whether there is enough disk headroom for the next annual campaign "
            "by forecasting storage growth from historical job footprints."
        )
    )
    parser.add_argument(
        "--year", type=int, help="Campaign year (enables annual sources hc/phac/cihr)."
    )
    parser.add_argument(
        "--archive-root",
        default=os.environ.get("HEALTHARCHIVE_ARCHIVE_ROOT", "/srv/healtharchive/jobs"),
        help="Archive root directory to base disk usage/forecast on.",
    )
    parser.add_argument(
        "--growth-factor",
        type=float,
        default=1.15,
        help="Multiplier applied to historical job sizes to estimate the next run (default: 1.15).",
    )
    parser.add_argument(
        "--active-overhead-factor",
        type=float,
        default=0.10,
        help=(
            "Extra temporary/peak overhead as a fraction of expected additional output (default: 0.10). "
            "This approximates crawl-time scratch space and consolidation overhead."
        ),
    )
    parser.add_argument(
        "--free-reserve-gib",
        type=float,
        default=10.0,
        help=(
            "Minimum free disk to keep in reserve after accounting for expected growth + overhead "
            "(default: 10 GiB)."
        ),
    )
    parser.add_argument(
        "--policy-review-percent",
        type=int,
        default=80,
        help="Fail if projected disk usage exceeds this percent (default: 80).",
    )
    parser.add_argument(
        "--policy-target-percent",
        type=int,
        default=70,
        help="Warn-only threshold (printed) for projected disk usage (default: 70).",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        default=False,
        help="Emit machine-readable JSON output.",
    )
    args = parser.parse_args(argv)

    archive_root = Path(args.archive_root).resolve()
    if not archive_root.exists():
        print(f"ERROR: archive root not found: {archive_root}")
        return 1
    usage = shutil.disk_usage(archive_root)
    total_b = int(usage.total)
    used_b = int(usage.used)
    free_b = int(usage.free)
    used_pct = int(round((used_b / total_b) * 100)) if total_b else 0

    sources = list(ANNUAL_SOURCES_ORDERED) if args.year else []
    if not sources:
        print("NOTE: --year not provided; skipping campaign storage forecast.")
        return 0

    if args.policy_review_percent <= 0 or args.policy_review_percent > 100:
        raise SystemExit("--policy-review-percent must be between 1 and 100")

    review_used_max_b = int(math.floor(total_b * (args.policy_review_percent / 100.0)))
    allowed_add_b = max(0, review_used_max_b - used_b)

    # Load reference jobs.
    references: dict[str, ReferenceJob] = {}
    db_error: str | None = None
    try:
        with get_session() as session:
            for code in sources:
                src = session.query(Source).filter_by(code=code).one_or_none()
                if src is None:
                    continue
                jobs = (
                    session.query(ArchiveJob)
                    .filter(ArchiveJob.source_id == src.id)
                    .order_by(ArchiveJob.id.desc())
                    .limit(25)
                    .all()
                )
                ref = _pick_reference_job(jobs, code)
                if ref is not None:
                    references[code] = ref
    except Exception as exc:
        db_error = f"{type(exc).__name__}: {exc}"

    # Measure reference sizes.
    measured_sizes_b: dict[str, Optional[int]] = {}
    size_methods: dict[str, str] = {}
    for code, ref in references.items():
        # Prefer DB storage accounting when it exists (fast, no perms problems).
        if ref.output_bytes_total > 0 and ref.storage_scanned_at is not None:
            measured_sizes_b[code] = ref.output_bytes_total
            size_methods[code] = "db_output_bytes_total"
            continue

        size_b, method = _measure_output_dir_bytes(Path(ref.output_dir))
        measured_sizes_b[code] = size_b
        size_methods[code] = method

    # Estimate next campaign growth.
    estimates: list[dict[str, Any]] = []
    missing_estimates: list[str] = []
    expected_add_b = 0
    resolved_reference_sizes_b: dict[str, int] = {}
    resolved_reference_paths: dict[str, str | None] = {}

    for code in sources:
        ref = references.get(code)
        ref_size_b = measured_sizes_b.get(code)
        method = size_methods.get(code) if ref else None
        reference_path = str(ref.output_dir) if ref else None

        if ref_size_b is None:
            disk_dir = _pick_latest_job_dir_under_archive_root(archive_root, code)
            if disk_dir is not None:
                size_b, m = _measure_output_dir_bytes(disk_dir)
                if size_b is not None:
                    ref_size_b = size_b
                    method = "disk_latest_dir"
                    reference_path = str(disk_dir)

        if ref_size_b is None:
            proxy = PROXY_SOURCES.get(code)
            proxy_size_b = resolved_reference_sizes_b.get(proxy) if proxy else None
            proxy_path = resolved_reference_paths.get(proxy) if proxy else None
            if proxy and proxy_size_b is None and measured_sizes_b.get(proxy) is not None:
                proxy_size_b = measured_sizes_b[proxy]
                proxy_ref = references.get(proxy)
                proxy_path = str(proxy_ref.output_dir) if proxy_ref else None
            if proxy and proxy_size_b is None:
                disk_dir = _pick_latest_job_dir_under_archive_root(archive_root, proxy)
                if disk_dir is not None:
                    size_b, _m = _measure_output_dir_bytes(disk_dir)
                    if size_b is not None:
                        proxy_size_b = size_b
                        proxy_path = str(disk_dir)

            if proxy and proxy_size_b is not None:
                ref_size_b = proxy_size_b
                method = f"proxy:{proxy}"
                reference_path = proxy_path
            else:
                fallback_gib = int(DEFAULT_ESTIMATES_GIB.get(code, 10))
                ref_size_b = fallback_gib * GiB
                method = f"fallback:{fallback_gib}GiB"
                missing_estimates.append(code)

        resolved_reference_sizes_b[code] = int(ref_size_b)
        resolved_reference_paths[code] = reference_path

        est_b = int(math.ceil(ref_size_b * float(args.growth_factor)))
        expected_add_b += est_b

        estimates.append(
            {
                "sourceCode": code,
                "referenceJob": {
                    "jobId": ref.job_id,
                    "jobName": ref.job_name,
                    "status": ref.status,
                    "outputDir": ref.output_dir,
                    "capped": ref.capped,
                }
                if ref
                else None,
                "referencePath": reference_path,
                "referenceBytes": int(ref_size_b),
                "estimateBytes": int(est_b),
                "estimateMethod": method,
            }
        )

    projected_used_b = used_b + expected_add_b
    projected_pct = (projected_used_b / total_b) * 100.0 if total_b else 0.0
    active_overhead_b = int(
        math.ceil(expected_add_b * max(0.0, float(args.active_overhead_factor)))
    )
    free_reserve_b = int(max(0.0, float(args.free_reserve_gib)) * GiB)
    required_free_b = expected_add_b + active_overhead_b + free_reserve_b
    projected_peak_used_b = used_b + expected_add_b + active_overhead_b
    projected_peak_pct = (projected_peak_used_b / total_b) * 100.0 if total_b else 0.0

    result = {
        "archiveRoot": str(archive_root),
        "disk": {
            "totalBytes": total_b,
            "usedBytes": used_b,
            "freeBytes": free_b,
            "usedPercent": used_pct,
        },
        "policy": {
            "targetPercent": int(args.policy_target_percent),
            "reviewPercent": int(args.policy_review_percent),
            "reviewMaxUsedBytes": int(review_used_max_b),
            "allowedAdditionalBytesBeforeReview": int(allowed_add_b),
        },
        "campaign": {
            "year": int(args.year),
            "sources": list(sources),
            "growthFactor": float(args.growth_factor),
            "activeOverheadFactor": float(args.active_overhead_factor),
            "freeReserveBytes": int(free_reserve_b),
        },
        "estimates": estimates,
        "summary": {
            "expectedAdditionalBytes": int(expected_add_b),
            "activeOverheadBytes": int(active_overhead_b),
            "requiredFreeBytes": int(required_free_b),
            "projectedUsedBytes": int(projected_used_b),
            "projectedUsedPercent": projected_pct,
            "projectedPeakUsedBytes": int(projected_peak_used_b),
            "projectedPeakUsedPercent": projected_peak_pct,
            "missingHistoricalSources": missing_estimates,
            "dbError": db_error,
        },
    }

    if args.json:
        should_fail = bool(required_free_b > free_b or projected_peak_used_b > review_used_max_b)
        import json

        print(json.dumps(result, indent=2, sort_keys=True))
        return 1 if should_fail else 0

    print("Campaign storage forecast (annual)")
    print("---------------------------------")
    print(f"Archive root: {archive_root}")
    print(
        f"Disk: {used_pct}% used ({_human_bytes(used_b)} used, {_human_bytes(free_b)} free, {_human_bytes(total_b)} total)"
    )
    print(
        f"Policy: target<{args.policy_target_percent}% review<{args.policy_review_percent}% "
        f"(allowed add before review: {_human_bytes(allowed_add_b)})"
    )
    if db_error:
        print(
            f"WARN: DB query failed ({db_error}); using best-effort on-disk / fallback estimates."
        )
    print("")
    print(f"Campaign year: {args.year} (sources: {', '.join(sources)})")
    print(f"Growth factor: {args.growth_factor:.2f}")
    print(
        f"Active overhead: {args.active_overhead_factor:.2f} "
        f"(+{_human_bytes(active_overhead_b)}), free reserve: {_human_bytes(free_reserve_b)}"
    )
    print("")

    for item in estimates:
        code = str(item["sourceCode"])
        ref_job = item.get("referenceJob") or {}
        ref_path = item.get("referencePath")
        ref_label = (
            f"job={ref_job.get('jobId')} {ref_job.get('jobName')} ({ref_job.get('status')})"
            if ref_job
            else "job=(none)"
        )
        capped = bool(ref_job.get("capped")) if ref_job else False
        cap_note = " capped=true" if capped else ""
        print(
            f"{code}: ref={_human_bytes(int(item['referenceBytes']))} est={_human_bytes(int(item['estimateBytes']))} "
            f"method={item.get('estimateMethod')} {ref_label}{cap_note}"
            + (f" path={ref_path}" if ref_path else "")
        )

    print("")
    print(f"Expected additional: {_human_bytes(expected_add_b)}")
    print(f"Required free (add+overhead+reserve): {_human_bytes(required_free_b)}")
    print(
        f"Projected after campaign: {_human_bytes(projected_used_b)} used ({projected_pct:.1f}% used)"
    )
    print(
        f"Projected peak during campaign: {_human_bytes(projected_peak_used_b)} used ({projected_peak_pct:.1f}% used)"
    )

    if required_free_b > free_b:
        shortfall_b = required_free_b - free_b
        print("")
        print(
            "FAIL: insufficient free disk for campaign when accounting for crawl-time overhead and reserve. "
            f"Need {_human_bytes(required_free_b)} but only {_human_bytes(free_b)} free."
        )
        print(f"Shortfall: {_human_bytes(shortfall_b)}")
        return 1

    if projected_peak_used_b > review_used_max_b:
        shortfall_b = projected_peak_used_b - review_used_max_b
        print("")
        print(
            "FAIL: projected disk usage exceeds policy review threshold "
            f"({args.policy_review_percent}%)."
        )
        print(
            f"Projected {_human_bytes(projected_peak_used_b)} used vs policy max {_human_bytes(review_used_max_b)} used."
        )
        print(
            f"To meet policy, free at least {_human_bytes(shortfall_b)} (or expand disk) before running the campaign."
        )
        if missing_estimates:
            print(
                "NOTE: One or more sources had no historical job size; conservative fallbacks were used: "
                + ", ".join(missing_estimates)
            )
        return 1

    if projected_pct >= float(args.policy_target_percent):
        print("")
        print(
            f"WARN: projected disk usage exceeds target threshold ({args.policy_target_percent}%)."
        )

    if missing_estimates:
        print("")
        print(
            "WARN: One or more sources had no historical job size; conservative fallbacks were used: "
            + ", ".join(missing_estimates)
        )

    print("")
    print("OK: storage headroom looks sufficient for the forecast.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
