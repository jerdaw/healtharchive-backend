from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from sqlalchemy.orm import Session

from .config import get_archive_tool_config
from .models import ArchiveJob as ORMArchiveJob, Source


@dataclass
class SourceJobConfig:
    """
    Configuration template for how to crawl a particular source.

    This is the single source of truth for:
    - default seeds
    - default zimit passthrough args
    - default archive_tool options (monitoring, cleanup, etc.)
    - naming patterns and scheduling hints
    """

    source_code: str
    name_template: str
    default_seeds: List[str]
    default_zimit_passthrough_args: List[str]
    default_tool_options: Dict[str, Any]
    schedule_hint: Optional[str] = None


# Initial registry entries for core sources.
SOURCE_JOB_CONFIGS: Dict[str, SourceJobConfig] = {
    "hc": SourceJobConfig(
        source_code="hc",
        name_template="hc-{date:%Y%m%d}",
        default_seeds=["https://www.canada.ca/en/health-canada.html"],
        # Zimit arguments passed after the "--" separator. Keep conservative
        # defaults for now and tune later as needed.
        default_zimit_passthrough_args=[],
        default_tool_options={
            "cleanup": False,
            "overwrite": False,
            "enable_monitoring": False,
            "enable_adaptive_workers": False,
            "enable_vpn_rotation": False,
            "initial_workers": 1,
            "log_level": "INFO",
        },
        schedule_hint="monthly",
    ),
    "phac": SourceJobConfig(
        source_code="phac",
        name_template="phac-{date:%Y%m%d}",
        default_seeds=["https://www.canada.ca/en/public-health.html"],
        default_zimit_passthrough_args=[],
        default_tool_options={
            "cleanup": False,
            "overwrite": False,
            "enable_monitoring": False,
            "enable_adaptive_workers": False,
            "enable_vpn_rotation": False,
            "initial_workers": 1,
            "log_level": "INFO",
        },
        schedule_hint="monthly",
    ),
}


def get_config_for_source(source_code: str) -> Optional[SourceJobConfig]:
    """
    Look up the SourceJobConfig for a given source code (case-insensitive).
    """
    code = source_code.lower()
    return SOURCE_JOB_CONFIGS.get(code)


def generate_job_name(
    source_cfg: SourceJobConfig,
    *,
    now: Optional[datetime] = None,
) -> str:
    """
    Generate a logical job name from the template and current time.

    The template can reference the datetime via the 'date' placeholder, e.g.:
    'hc-{date:%Y%m%d}' -> 'hc-20251209'
    """
    if now is None:
        now = datetime.now(timezone.utc)
    return source_cfg.name_template.format(date=now)


def build_output_dir_for_job(
    source_code: str,
    job_name: str,
    *,
    archive_root: Path,
    now: Optional[datetime] = None,
) -> Path:
    """
    Build the output directory path for a job, without creating it.

    Pattern:
      <archive_root>/<source_code>/<YYYYMMDDThhmmssZ>__<job_name>
    """
    if now is None:
        now = datetime.now(timezone.utc)
    ts = now.strftime("%Y%m%dT%H%M%SZ")
    safe_name = job_name.strip().replace(" ", "_") or "job"
    dir_name = f"{ts}__{safe_name}"
    return archive_root / source_code.lower() / dir_name


def build_job_config(
    source_cfg: SourceJobConfig,
    *,
    extra_seeds: Optional[Iterable[str]] = None,
    overrides: Optional[Dict[str, Any]] = None,
    extra_zimit_args: Optional[Iterable[str]] = None,
) -> Dict[str, Any]:
    """
    Construct the configuration JSON for an ArchiveJob row from a registry
    template, optional extra seeds, and optional tool option overrides.
    """
    seeds: List[str] = list(source_cfg.default_seeds)
    if extra_seeds:
        seeds.extend(extra_seeds)

    zimit_args: List[str] = list(source_cfg.default_zimit_passthrough_args)
    if extra_zimit_args:
        zimit_args.extend(extra_zimit_args)

    tool_options: Dict[str, Any] = dict(source_cfg.default_tool_options)
    if overrides:
        tool_options.update(overrides)

    # Basic validation to catch obviously invalid combinations early. This
    # mirrors archive_tool's expectations:
    #
    # - enable_adaptive_workers requires enable_monitoring.
    # - enable_vpn_rotation requires enable_monitoring and vpn_connect_command.
    enable_monitoring = bool(tool_options.get("enable_monitoring", False))
    enable_adaptive_workers = bool(tool_options.get("enable_adaptive_workers", False))
    enable_vpn_rotation = bool(tool_options.get("enable_vpn_rotation", False))
    vpn_connect_command = tool_options.get("vpn_connect_command")

    if enable_adaptive_workers and not enable_monitoring:
        raise ValueError(
            "tool_options.enable_adaptive_workers requires enable_monitoring=True"
        )

    if enable_vpn_rotation and not enable_monitoring:
        raise ValueError(
            "tool_options.enable_vpn_rotation requires enable_monitoring=True"
        )

    if enable_vpn_rotation and not vpn_connect_command:
        raise ValueError(
            "tool_options.enable_vpn_rotation requires vpn_connect_command to be set"
        )

    return {
        "seeds": seeds,
        "zimit_passthrough_args": zimit_args,
        "tool_options": tool_options,
    }


def create_job_for_source(
    source_code: str,
    *,
    session: Session,
    overrides: Optional[Dict[str, Any]] = None,
    extra_zimit_args: Optional[Iterable[str]] = None,
) -> ORMArchiveJob:
    """
    Create and persist a new ArchiveJob for the given source.

    The job is created with status 'queued' and does not start running; it is
    ready for a worker or CLI command to execute later.
    """
    cfg = get_config_for_source(source_code)
    if cfg is None:
        raise ValueError(f"Unknown source code {source_code!r}")

    # Ensure a Source row exists in the DB.
    source = session.query(Source).filter_by(code=cfg.source_code).one_or_none()
    if source is None:
        raise ValueError(
            f"Source with code {cfg.source_code!r} does not exist in the database. "
            "Run 'ha-backend seed-sources' or insert it manually."
        )

    tool_cfg = get_archive_tool_config()
    now = datetime.now(timezone.utc)

    job_name = generate_job_name(cfg, now=now)
    output_dir = build_output_dir_for_job(
        cfg.source_code,
        job_name,
        archive_root=tool_cfg.archive_root,
        now=now,
    )

    job_config = build_job_config(
        cfg,
        overrides=overrides,
        extra_zimit_args=extra_zimit_args,
    )

    job = ORMArchiveJob(
        source=source,
        name=job_name,
        output_dir=str(output_dir),
        status="queued",
        queued_at=now,
        config=job_config,
    )
    session.add(job)
    session.flush()
    return job


__all__ = [
    "SourceJobConfig",
    "SOURCE_JOB_CONFIGS",
    "get_config_for_source",
    "generate_job_name",
    "build_output_dir_for_job",
    "build_job_config",
    "create_job_for_source",
]
