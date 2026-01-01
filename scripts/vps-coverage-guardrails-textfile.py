#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import tomllib
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Iterable

from ha_backend.db import get_session
from ha_backend.models import ArchiveJob, Source


@dataclass(frozen=True)
class CoverageConfig:
    sources: list[str]
    min_prev_pages: int
    critical_ratio: float
    warning_ratio: float


@dataclass(frozen=True)
class AnnualJob:
    job_id: int
    source_code: str
    year: int
    pages_crawled: int
    finished_at: datetime | None


def _dt_to_epoch_seconds(dt: datetime) -> int:
    return int(dt.astimezone(timezone.utc).timestamp())


def _parse_annual_job_date(name: str, source_code: str) -> date | None:
    prefix = f"{source_code}-"
    if not name.startswith(prefix):
        return None
    suffix = name[len(prefix) :]
    if len(suffix) != 8 or not suffix.isdigit():
        return None
    try:
        parsed = datetime.strptime(suffix, "%Y%m%d").date()
    except ValueError:
        return None
    if parsed.month != 1 or parsed.day != 1:
        return None
    return parsed


def _emit(lines: list[str], line: str) -> None:
    lines.append(line.rstrip("\n"))


def _load_config(path: Path) -> CoverageConfig:
    raw = tomllib.loads(path.read_bytes())
    sources = [str(s).strip() for s in raw.get("sources", []) if str(s).strip()]
    min_prev_pages = int(raw.get("min_prev_pages", 1000))
    critical_ratio = float(raw.get("critical_ratio", 0.7))
    warning_ratio = float(raw.get("warning_ratio", 0.8))
    return CoverageConfig(
        sources=sources,
        min_prev_pages=min_prev_pages,
        critical_ratio=critical_ratio,
        warning_ratio=warning_ratio,
    )


def _iter_indexed_jobs(session, sources: Iterable[str]) -> Iterable[AnnualJob]:
    rows = (
        session.query(
            ArchiveJob.id,
            Source.code,
            ArchiveJob.name,
            ArchiveJob.pages_crawled,
            ArchiveJob.finished_at,
        )
        .join(Source, ArchiveJob.source_id == Source.id)
        .filter(ArchiveJob.status == "indexed")
        .filter(Source.code.in_(list(sources)))
        .order_by(ArchiveJob.id.desc())
        .all()
    )
    for job_id, source_code, name, pages_crawled, finished_at in rows:
        annual_date = _parse_annual_job_date(str(name), str(source_code))
        if annual_date is None:
            continue
        yield AnnualJob(
            job_id=int(job_id),
            source_code=str(source_code),
            year=int(annual_date.year),
            pages_crawled=int(pages_crawled or 0),
            finished_at=finished_at,
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "HealthArchive VPS helper: write coverage regression guardrail metrics "
            "to the node_exporter textfile collector."
        )
    )
    parser.add_argument(
        "--config",
        default="/opt/healtharchive-backend/ops/automation/coverage-guardrails.toml",
        help="Config TOML path.",
    )
    parser.add_argument(
        "--out-dir",
        default="/var/lib/node_exporter/textfile_collector",
        help="node_exporter textfile collector directory.",
    )
    parser.add_argument(
        "--out-file",
        default="healtharchive_coverage.prom",
        help="Output filename under --out-dir.",
    )
    args = parser.parse_args(argv)

    now = datetime.now(timezone.utc)
    metrics_ok = 1
    config: CoverageConfig | None = None
    try:
        config = _load_config(Path(args.config))
    except Exception:
        metrics_ok = 0

    lines: list[str] = []
    _emit(lines, "# HELP healtharchive_coverage_metrics_ok 1 if the guardrails script ran.")
    _emit(lines, "# TYPE healtharchive_coverage_metrics_ok gauge")
    _emit(lines, f"healtharchive_coverage_metrics_ok {metrics_ok}")
    _emit(
        lines,
        "# HELP healtharchive_coverage_timestamp_seconds UNIX timestamp when these metrics were generated.",
    )
    _emit(lines, "# TYPE healtharchive_coverage_timestamp_seconds gauge")
    _emit(lines, f"healtharchive_coverage_timestamp_seconds {_dt_to_epoch_seconds(now)}")

    _emit(
        lines,
        "# HELP healtharchive_coverage_curr_pages Pages crawled for the latest annual job.",
    )
    _emit(lines, "# TYPE healtharchive_coverage_curr_pages gauge")
    _emit(
        lines,
        "# HELP healtharchive_coverage_prev_pages Pages crawled for the prior annual job.",
    )
    _emit(lines, "# TYPE healtharchive_coverage_prev_pages gauge")
    _emit(
        lines,
        "# HELP healtharchive_coverage_ratio Current/previous annual coverage ratio.",
    )
    _emit(lines, "# TYPE healtharchive_coverage_ratio gauge")
    _emit(
        lines,
        "# HELP healtharchive_coverage_regression 1 if coverage ratio is below critical threshold.",
    )
    _emit(lines, "# TYPE healtharchive_coverage_regression gauge")
    _emit(
        lines,
        "# HELP healtharchive_coverage_warning 1 if coverage ratio is below warning threshold.",
    )
    _emit(lines, "# TYPE healtharchive_coverage_warning gauge")

    if metrics_ok == 0 or config is None or not config.sources:
        out_dir = Path(args.out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        out_file = out_dir / str(args.out_file)
        tmp = out_file.with_suffix(out_file.suffix + f".{os.getpid()}.tmp")
        tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
        tmp.chmod(0o644)
        tmp.replace(out_file)
        return 0

    annual_by_source: dict[str, dict[int, AnnualJob]] = {s: {} for s in config.sources}
    with get_session() as session:
        for job in _iter_indexed_jobs(session, config.sources):
            existing = annual_by_source[job.source_code].get(job.year)
            if existing is None or job.job_id > existing.job_id:
                annual_by_source[job.source_code][job.year] = job

    for source_code in config.sources:
        by_year = annual_by_source.get(source_code, {})
        if not by_year:
            continue
        latest_year = max(by_year.keys())
        curr = by_year.get(latest_year)
        prev = by_year.get(latest_year - 1)
        if curr is None:
            continue

        curr_labels = f'source="{source_code}",year="{latest_year}"'
        _emit(
            lines,
            f"healtharchive_coverage_curr_pages{{{curr_labels}}} {int(curr.pages_crawled)}",
        )

        if prev is None:
            continue

        prev_labels = f'source="{source_code}",year="{latest_year - 1}"'
        _emit(
            lines,
            f"healtharchive_coverage_prev_pages{{{prev_labels}}} {int(prev.pages_crawled)}",
        )

        ratio = 0.0
        if prev.pages_crawled > 0:
            ratio = float(curr.pages_crawled) / float(prev.pages_crawled)

        _emit(
            lines,
            f"healtharchive_coverage_ratio{{{curr_labels}}} {ratio:.4f}",
        )

        regression = (
            1
            if prev.pages_crawled >= config.min_prev_pages and ratio < config.critical_ratio
            else 0
        )
        warning = (
            1 if prev.pages_crawled >= config.min_prev_pages and ratio < config.warning_ratio else 0
        )
        _emit(lines, f"healtharchive_coverage_regression{{{curr_labels}}} {regression}")
        _emit(lines, f"healtharchive_coverage_warning{{{curr_labels}}} {warning}")

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
