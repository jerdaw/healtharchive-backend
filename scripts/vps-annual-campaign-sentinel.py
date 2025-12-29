#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _default_year_utc() -> int:
    return _utc_now().year


def _run(cmd: list[str], *, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        cwd=str(cwd) if cwd is not None else None,
        check=False,
        text=True,
        capture_output=True,
    )


def _mount_fstype(path: Path) -> str | None:
    """
    Return fstype for an exact mountpoint, if present.
    """
    try:
        mounts = Path("/proc/self/mounts").read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    target = str(path)
    for line in mounts:
        parts = line.split()
        if len(parts) < 3:
            continue
        _src, mnt, fstype = parts[0], parts[1], parts[2]
        if mnt == target:
            return fstype
    return None


def _is_mountpoint(path: Path) -> bool:
    if not path.exists():
        return False
    try:
        r = subprocess.run(["mountpoint", "-q", str(path)], check=False)
        return r.returncode == 0
    except FileNotFoundError:
        out = subprocess.run(["mount"], check=False, capture_output=True, text=True).stdout
        return f" on {path} " in out


@dataclass(frozen=True)
class AnnualStatusSummary:
    total_sources: int
    missing: int
    errors: int


@dataclass(frozen=True)
class AnnualStatusJob:
    source_code: str
    status: str
    job_id: int | None
    output_dir: Path | None


def _parse_annual_status(
    payload: dict[str, object],
) -> tuple[AnnualStatusSummary, list[AnnualStatusJob]]:
    summary_obj = payload.get("summary")
    if not isinstance(summary_obj, dict):
        raise ValueError("annual-status JSON missing summary")

    total_sources = int(summary_obj.get("totalSources") or 0)
    missing = int(summary_obj.get("missing") or 0)
    errors = int(summary_obj.get("errors") or 0)

    sources_obj = payload.get("sources")
    if not isinstance(sources_obj, list):
        raise ValueError("annual-status JSON missing sources list")

    jobs: list[AnnualStatusJob] = []
    for entry in sources_obj:
        if not isinstance(entry, dict):
            continue
        source_code = str(entry.get("sourceCode") or "unknown")
        status = str(entry.get("status") or "unknown")
        job_obj = entry.get("job")
        job_id: int | None = None
        output_dir: Path | None = None
        if isinstance(job_obj, dict) and job_obj:
            if job_obj.get("jobId") is not None:
                job_id = int(job_obj.get("jobId"))
            if job_obj.get("outputDir"):
                output_dir = Path(str(job_obj.get("outputDir")))
        jobs.append(
            AnnualStatusJob(
                source_code=source_code,
                status=status,
                job_id=job_id,
                output_dir=output_dir,
            )
        )

    return AnnualStatusSummary(total_sources=total_sources, missing=missing, errors=errors), jobs


def _write_textfile_metrics(path: Path, *, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(f"{path.suffix}.tmp.{os.getpid()}")
    tmp.write_text(content, encoding="utf-8")
    os.chmod(tmp, 0o644)
    tmp.replace(path)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description=(
            "Annual campaign sentinel: runs campaign preflight + validates annual jobs exist and are tiered "
            "onto the campaign storage root. Writes a Prometheus textfile metric for alerting."
        )
    )
    p.add_argument(
        "--year",
        type=int,
        default=_default_year_utc(),
        help="Campaign year (default: current UTC year).",
    )
    p.add_argument(
        "--sources",
        nargs="+",
        default=["hc", "phac", "cihr"],
        help="Sources to validate (default: hc phac cihr).",
    )
    p.add_argument(
        "--campaign-archive-root",
        default="/srv/healtharchive/storagebox/jobs",
        help="Expected campaign output root (default: /srv/healtharchive/storagebox/jobs).",
    )
    p.add_argument(
        "--metrics-file",
        default="/var/lib/node_exporter/textfile_collector/healtharchive_annual_campaign.prom",
        help="Where to write node_exporter textfile metrics (default: /var/lib/node_exporter/textfile_collector/healtharchive_annual_campaign.prom).",
    )
    args = p.parse_args(argv)

    year = int(args.year)
    sources = [str(s).strip().lower() for s in list(args.sources or []) if str(s).strip()]
    campaign_root = Path(str(args.campaign_archive_root))
    metrics_file = Path(str(args.metrics_file))

    if year < 1970 or year > 2100:
        print(f"ERROR: invalid year: {year}", file=sys.stderr)
        return 2
    if not sources:
        print("ERROR: no sources requested", file=sys.stderr)
        return 2

    repo_root = Path(__file__).resolve().parents[1]
    preflight = repo_root / "scripts" / "vps-preflight-crawl.sh"
    ha_backend = repo_root / ".venv" / "bin" / "ha-backend"

    failures: list[str] = []

    started = _utc_now()
    print("HealthArchive annual campaign sentinel")
    print("-------------------------------------")
    print(f"year={year} sources={', '.join(sources)}")
    print(f"campaign_archive_root={campaign_root}")
    print(f"metrics_file={metrics_file}")
    print("")

    preflight_rc = 1
    if not preflight.exists():
        failures.append(f"missing preflight script: {preflight}")
    else:
        r = _run(
            [
                str(preflight),
                "--year",
                str(year),
                "--campaign-archive-root",
                str(campaign_root),
            ],
            cwd=repo_root,
        )
        preflight_rc = int(r.returncode)
        if preflight_rc != 0:
            failures.append(f"preflight failed (rc={preflight_rc})")
            sys.stderr.write(r.stdout[-4000:] + ("\n" if r.stdout else ""))
            sys.stderr.write(r.stderr[-4000:] + ("\n" if r.stderr else ""))

    summary = AnnualStatusSummary(total_sources=0, missing=0, errors=0)
    jobs: list[AnnualStatusJob] = []
    annual_rc = 1
    if not ha_backend.exists():
        failures.append(f"missing ha-backend binary: {ha_backend}")
    else:
        r = _run(
            [
                str(ha_backend),
                "annual-status",
                "--year",
                str(year),
                "--json",
                "--sources",
                *sources,
            ],
            cwd=repo_root,
        )
        annual_rc = int(r.returncode)
        if annual_rc != 0:
            failures.append(f"annual-status failed (rc={annual_rc})")
            sys.stderr.write(r.stdout[-2000:] + ("\n" if r.stdout else ""))
            sys.stderr.write(r.stderr[-2000:] + ("\n" if r.stderr else ""))
        else:
            try:
                payload = json.loads(r.stdout or "{}")
                if not isinstance(payload, dict):
                    raise ValueError("annual-status JSON is not an object")
                summary, jobs = _parse_annual_status(payload)
            except Exception as e:
                failures.append(f"annual-status JSON parse failed: {e}")

    tiered_ok = 0
    tiered_expected = 0
    for j in jobs:
        if j.status in {"missing", "error"}:
            continue
        if j.output_dir is None:
            continue
        tiered_expected += 1
        if _is_mountpoint(j.output_dir) and _mount_fstype(j.output_dir) == "fuse.sshfs":
            tiered_ok += 1

    if summary.missing != 0 or summary.errors != 0:
        failures.append(
            f"annual-status not clean (missing={summary.missing} errors={summary.errors})"
        )

    if tiered_expected != 0 and tiered_ok != tiered_expected:
        failures.append(f"annual outputs not fully tiered (tiered={tiered_ok}/{tiered_expected})")

    ok = 1 if not failures else 0
    now_ts = int(time.time())
    lines = [
        "# HELP healtharchive_annual_campaign_sentinel_ok 1 if annual campaign sentinel checks passed.",
        "# TYPE healtharchive_annual_campaign_sentinel_ok gauge",
        f'healtharchive_annual_campaign_sentinel_ok{{year="{year}"}} {ok}',
        "",
        "# HELP healtharchive_annual_campaign_sentinel_last_run_timestamp_seconds UNIX timestamp when the sentinel last ran.",
        "# TYPE healtharchive_annual_campaign_sentinel_last_run_timestamp_seconds gauge",
        f'healtharchive_annual_campaign_sentinel_last_run_timestamp_seconds{{year="{year}"}} {now_ts}',
        "",
        "# HELP healtharchive_annual_campaign_jobs_missing Number of missing annual jobs (annual-status).",
        "# TYPE healtharchive_annual_campaign_jobs_missing gauge",
        f'healtharchive_annual_campaign_jobs_missing{{year="{year}"}} {summary.missing}',
        "",
        "# HELP healtharchive_annual_campaign_jobs_errors Number of errors in annual-status.",
        "# TYPE healtharchive_annual_campaign_jobs_errors gauge",
        f'healtharchive_annual_campaign_jobs_errors{{year="{year}"}} {summary.errors}',
        "",
        "# HELP healtharchive_annual_campaign_outputs_tiered Number of annual job output dirs that are mounted onto the campaign tier.",
        "# TYPE healtharchive_annual_campaign_outputs_tiered gauge",
        f'healtharchive_annual_campaign_outputs_tiered{{year="{year}"}} {tiered_ok}',
        "",
        "# HELP healtharchive_annual_campaign_outputs_expected Number of annual job output dirs expected to be tiered (non-missing sources).",
        "# TYPE healtharchive_annual_campaign_outputs_expected gauge",
        f'healtharchive_annual_campaign_outputs_expected{{year="{year}"}} {tiered_expected}',
        "",
        "# HELP healtharchive_annual_campaign_preflight_rc Exit code from vps-preflight-crawl.sh.",
        "# TYPE healtharchive_annual_campaign_preflight_rc gauge",
        f'healtharchive_annual_campaign_preflight_rc{{year="{year}"}} {preflight_rc}',
        "",
        "# HELP healtharchive_annual_campaign_annual_status_rc Exit code from ha-backend annual-status.",
        "# TYPE healtharchive_annual_campaign_annual_status_rc gauge",
        f'healtharchive_annual_campaign_annual_status_rc{{year="{year}"}} {annual_rc}',
        "",
    ]
    _write_textfile_metrics(metrics_file, content="\n".join(lines) + "\n")

    duration_s = int((_utc_now() - started).total_seconds())
    if failures:
        print("")
        print("FAIL")
        for f in failures:
            print(f"  - {f}")
        print(f"duration_seconds={duration_s}")
        return 1

    print("OK")
    print(f"tiered={tiered_ok}/{tiered_expected}")
    print(f"duration_seconds={duration_s}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
