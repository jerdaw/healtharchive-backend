from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from sqlalchemy.orm import Session

from .archive_contract import ArchiveJobConfig, ArchiveToolOptions, validate_tool_options
from .config import get_archive_tool_config
from .models import ArchiveJob as ORMArchiveJob
from .models import Source

HC_CANADA_CA_SCOPE_INCLUDE_RX = (
    r"^https://www[.]canada[.]ca/"
    r"(?:"
    r"en/health-canada[.]html"
    r"|fr/sante-canada[.]html"
    # Exclude query-string and fragment variants of content pages to avoid
    # duplicate/trap-like expansions under a completeness-first crawl.
    r"|en/health-canada/[^?#]*"
    r"|fr/sante-canada/[^?#]*"
    r"|etc/designs/canada/wet-boew/.*"
    r"|content/dam/canada/sitemenu/.*"
    r"|content/dam/themes/health/.*"
    # Only match web-renderable assets from the HC DAM path. PDFs and binary
    # documents are excluded from scope to prevent navigation-timeout thrashing.
    # They are still captured as subresources when embedded in HTML pages.
    # Rationale: "PDF indexing non-goal for v1" (2026-01-23 throughput plan).
    r"|content/dam/hc-sc/.*\.(?:css|js|json|svg|png|jpe?g|gif|ico|webp|avif|woff2?|ttf|eot)(?:\?[^#]*)?"
    r")$"
)

PHAC_CANADA_CA_SCOPE_INCLUDE_RX = (
    r"^https://www[.]canada[.]ca/"
    r"(?:"
    r"en/public-health[.]html"
    r"|fr/sante-publique[.]html"
    r"|en/public-health/[^?#]*"
    r"|fr/sante-publique/[^?#]*"
    r"|etc/designs/canada/wet-boew/.*"
    r"|content/dam/canada/sitemenu/.*"
    r"|content/dam/themes/health/.*"
    # Only match web-renderable assets from the PHAC DAM path (same rationale
    # as the HC DAM filter above).
    r"|content/dam/phac-aspc/.*\.(?:css|js|json|svg|png|jpe?g|gif|ico|webp|avif|woff2?|ttf|eot)(?:\?[^#]*)?"
    r")$"
)

# Exclude binary/document URLs from top-level page queueing. These files can
# still be captured as subresources from crawled HTML pages, but avoiding direct
# page navigation to them reduces timeout thrashing substantially.
_CANADA_CA_BINARY_TOP_LEVEL_EXCLUDE_RX_BODY = (
    r"https://www[.]canada[.]ca/.*[.](?:pdf|mp4|zip|docx?|pptx?|xlsx?)(?:[?#].*)?"
)
# The deployed zimit image currently forwards ``--extraChromeArgs`` into its
# warc2zim preflight check, which causes immediate RC=2 failures before crawl
# startup. Keep the canonical canada.ca profiles free of Browsertrix chrome
# passthrough args until that upstream/container behavior changes.
_CANADA_CA_EXTRA_CHROME_ARGS: tuple[str, ...] = ()
_PHAC_PUBLIC_HEALTH_NOTICES_EXCLUDE_RX_BODY = (
    r"https://www[.]canada[.]ca/en/public-health/services/public-health-notices"
    r"(?:/[^?#]*)?(?:[?#].*)?"
)
_PHAC_TRAVEL_HEALTH_ARTESUNATE_EXCLUDE_RX_BODY = (
    r"https://www[.]canada[.]ca/"
    r"(?:"
    r"en/public-health/services/travel-health/medical-access-artesunate-quinine-malaria-treatment[.]html"
    r"|fr/sante-publique/services/sante-voyageurs/acces-medicale-a-artesunate-quinine-traitement-paludisme[.]html"
    r")(?:[?#].*)?"
)
_PHAC_NACI_EXCLUDE_RX_BODY = (
    r"https://www[.]canada[.]ca/en/public-health/services/immunization/"
    r"national-advisory-committee-on-immunization-naci(?:/[^?#]*)?(?:[?#].*)?"
)
_PHAC_CCDR_EXCLUDE_RX_BODY = (
    r"https://www[.]canada[.]ca/en/public-health/services/reports-publications/"
    r"canada-communicable-disease-report-ccdr(?:/[^?#]*)?(?:[?#].*)?"
)
_PHAC_CANADIAN_IMMUNIZATION_GUIDE_EXCLUDE_RX_BODY = (
    r"https://www[.]canada[.]ca/en/public-health/services/canadian-immunization-guide"
    r"(?:/[^?#]*)?(?:[?#].*)?"
)

_CANADA_CA_BINARY_TOP_LEVEL_EXCLUDE_RX = rf"^(?:{_CANADA_CA_BINARY_TOP_LEVEL_EXCLUDE_RX_BODY})$"

HC_CANADA_CA_SCOPE_EXCLUDE_RX = _CANADA_CA_BINARY_TOP_LEVEL_EXCLUDE_RX
PHAC_CANADA_CA_SCOPE_EXCLUDE_RX = (
    rf"^(?:{_CANADA_CA_BINARY_TOP_LEVEL_EXCLUDE_RX_BODY}"
    rf"|{_PHAC_PUBLIC_HEALTH_NOTICES_EXCLUDE_RX_BODY}"
    # Temporary PHAC-only exclusions based on the 2026-03-23 content-cost and
    # timeout diagnosis. These HTML families repeatedly dominated timeout churn
    # and resume loops, so exclude them from frontier expansion while deeper
    # canada.ca runtime compatibility work continues.
    rf"|{_PHAC_TRAVEL_HEALTH_ARTESUNATE_EXCLUDE_RX_BODY}"
    rf"|{_PHAC_NACI_EXCLUDE_RX_BODY}"
    rf"|{_PHAC_CCDR_EXCLUDE_RX_BODY}"
    rf"|{_PHAC_CANADIAN_IMMUNIZATION_GUIDE_EXCLUDE_RX_BODY})$"
)


def canonical_scope_filters_for_source(source_code: str) -> tuple[str, str] | None:
    """
    Return canonical scope include/exclude regexes for sources with managed scope.

    Only HC and PHAC currently use custom canada.ca scope filters that may need
    backfilling on older annual jobs.
    """
    code = str(source_code or "").strip().lower()
    if code == "hc":
        return HC_CANADA_CA_SCOPE_INCLUDE_RX, HC_CANADA_CA_SCOPE_EXCLUDE_RX
    if code == "phac":
        return PHAC_CANADA_CA_SCOPE_INCLUDE_RX, PHAC_CANADA_CA_SCOPE_EXCLUDE_RX
    return None


def normalize_scope_passthrough_args(
    args: Iterable[str],
    *,
    scope_include_rx: str,
    scope_exclude_rx: str,
    extra_chrome_args: Iterable[str] = (),
) -> list[str]:
    """
    Canonicalize managed passthrough args while preserving unrelated args.

    Managed args are emitted in a deterministic order so drift detection remains
    stable across retries and one-off reconciliation runs. Existing
    ``--extraChromeArgs`` values are preserved after the canonical ones.
    """
    remaining: list[str] = []
    existing_extra_chrome_args: list[str] = []
    raw_args = [str(arg) for arg in args]
    i = 0
    while i < len(raw_args):
        tok = raw_args[i]
        if tok in {"--scopeType", "--scopeIncludeRx", "--scopeExcludeRx"}:
            i += 2 if (i + 1) < len(raw_args) else 1
            continue
        if tok == "--extraChromeArgs":
            if (i + 1) < len(raw_args):
                existing_extra_chrome_args.append(raw_args[i + 1])
                i += 2
            else:
                i += 1
            continue
        remaining.append(tok)
        i += 1

    normalized_extra_chrome_args: list[str] = []
    seen_extra_chrome_args: set[str] = set()
    for value in [*(str(arg) for arg in extra_chrome_args), *existing_extra_chrome_args]:
        if value in seen_extra_chrome_args:
            continue
        seen_extra_chrome_args.add(value)
        normalized_extra_chrome_args.extend(["--extraChromeArgs", value])

    return [
        "--scopeType",
        "custom",
        "--scopeIncludeRx",
        scope_include_rx,
        "--scopeExcludeRx",
        scope_exclude_rx,
        *normalized_extra_chrome_args,
        *remaining,
    ]


def reconcile_scope_passthrough_args(
    source_code: str, args: Iterable[str]
) -> tuple[list[str], bool]:
    """
    Normalize managed scope args for a source and report whether drift existed.
    """
    existing_args = [str(arg) for arg in args]
    canonical = canonical_scope_filters_for_source(source_code)
    if canonical is None:
        return existing_args, False
    include_rx, exclude_rx = canonical
    extra_chrome_args: tuple[str, ...] = ()
    if str(source_code or "").strip().lower() in {"hc", "phac"}:
        extra_chrome_args = _CANADA_CA_EXTRA_CHROME_ARGS
    normalized = normalize_scope_passthrough_args(
        existing_args,
        scope_include_rx=include_rx,
        scope_exclude_rx=exclude_rx,
        extra_chrome_args=extra_chrome_args,
    )
    return normalized, normalized != existing_args


def _canonical_extra_chrome_passthrough_tokens() -> list[str]:
    tokens: list[str] = []
    for value in _CANADA_CA_EXTRA_CHROME_ARGS:
        tokens.extend(["--extraChromeArgs", value])
    return tokens


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
        default_seeds=[
            "https://www.canada.ca/en/health-canada.html",
            "https://www.canada.ca/fr/sante-canada.html",
        ],
        # Zimit arguments passed after the "--" separator. Keep conservative
        # defaults for now and tune later as needed.
        default_zimit_passthrough_args=[
            "--scopeType",
            "custom",
            "--scopeIncludeRx",
            HC_CANADA_CA_SCOPE_INCLUDE_RX,
            "--scopeExcludeRx",
            HC_CANADA_CA_SCOPE_EXCLUDE_RX,
            *_canonical_extra_chrome_passthrough_tokens(),
        ],
        default_tool_options={
            "cleanup": False,
            "overwrite": False,
            "skip_final_build": True,
            "enable_monitoring": True,
            "enable_adaptive_workers": True,
            "enable_adaptive_restart": True,
            "enable_vpn_rotation": False,
            "initial_workers": 2,
            # HC is noisy but broad; prefer fewer false-positive stalls.
            "stall_timeout_minutes": 75,
            "docker_shm_size": "1g",
            # Keep a non-trivial restart budget for long annual runs.
            "max_container_restarts": 24,
            # Tolerate long-tail slowness without excessive churn.
            "error_threshold_timeout": 55,
            "error_threshold_http": 55,
            "backoff_delay_minutes": 15,
            "log_level": "INFO",
            "relax_perms": True,  # ensure WARCs are readable on host in dev
        },
        schedule_hint="annual",
    ),
    "phac": SourceJobConfig(
        source_code="phac",
        name_template="phac-{date:%Y%m%d}",
        default_seeds=[
            "https://www.canada.ca/en/public-health.html",
            "https://www.canada.ca/fr/sante-publique.html",
        ],
        default_zimit_passthrough_args=[
            "--scopeType",
            "custom",
            "--scopeIncludeRx",
            PHAC_CANADA_CA_SCOPE_INCLUDE_RX,
            "--scopeExcludeRx",
            PHAC_CANADA_CA_SCOPE_EXCLUDE_RX,
            *_canonical_extra_chrome_passthrough_tokens(),
        ],
        default_tool_options={
            "cleanup": False,
            "overwrite": False,
            "skip_final_build": True,
            "enable_monitoring": True,
            "enable_adaptive_workers": True,
            "enable_adaptive_restart": True,
            "enable_vpn_rotation": False,
            "initial_workers": 2,
            # PHAC has shown the highest restart churn; be more tolerant.
            "stall_timeout_minutes": 90,
            "docker_shm_size": "1g",
            "max_container_restarts": 30,
            "error_threshold_timeout": 65,
            "error_threshold_http": 65,
            "backoff_delay_minutes": 3,
            "log_level": "INFO",
            "relax_perms": True,
        },
        schedule_hint="annual",
    ),
    "cihr": SourceJobConfig(
        source_code="cihr",
        name_template="cihr-{date:%Y%m%d}",
        default_seeds=[
            "https://cihr-irsc.gc.ca/e/193.html",
            "https://cihr-irsc.gc.ca/f/193.html",
        ],
        default_zimit_passthrough_args=[
            "--scopeType",
            "host",
        ],
        default_tool_options={
            "cleanup": False,
            "overwrite": False,
            "skip_final_build": True,
            "enable_monitoring": True,
            "enable_adaptive_workers": True,
            "enable_adaptive_restart": True,
            "enable_vpn_rotation": False,
            # CIHR is typically cleaner/faster; start a bit more aggressively.
            "initial_workers": 3,
            "stall_timeout_minutes": 45,
            "docker_shm_size": "1g",
            "max_container_restarts": 20,
            "error_threshold_timeout": 35,
            "error_threshold_http": 35,
            "backoff_delay_minutes": 1,
            "log_level": "INFO",
            "relax_perms": True,
        },
        schedule_hint="annual",
    ),
    "hc_canary": SourceJobConfig(
        source_code="hc",  # Uses hc source for DB purposes
        name_template="hc-canary",
        default_seeds=[
            "https://www.canada.ca/en.html",  # Small, stable entry page
        ],
        default_zimit_passthrough_args=[
            "--limit",
            "2",  # Crawl only 2 pages
            "--maxDepth",
            "0",  # Stay on seed page only
        ],
        default_tool_options={
            "cleanup": False,
            "overwrite": False,
            "skip_final_build": True,
            "enable_monitoring": False,  # Not needed for tiny canary
            "enable_adaptive_workers": False,
            "enable_adaptive_restart": False,
            "enable_vpn_rotation": False,
            "initial_workers": 1,
            "stall_timeout_minutes": 10,
            "docker_shm_size": "512m",
            "max_container_restarts": 3,
            "log_level": "WARNING",  # Reduce noise
            "relax_perms": True,
        },
        schedule_hint=None,  # Not scheduled, created on-demand
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

    tool_options_data: Dict[str, Any] = dict(source_cfg.default_tool_options)
    if overrides:
        tool_options_data.update(overrides)

    tool_options = ArchiveToolOptions.from_dict(tool_options_data)
    validate_tool_options(tool_options)

    job_cfg = ArchiveJobConfig(
        seeds=list(seeds),
        zimit_passthrough_args=list(zimit_args),
        tool_options=tool_options,
    )
    return job_cfg.to_dict()


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
