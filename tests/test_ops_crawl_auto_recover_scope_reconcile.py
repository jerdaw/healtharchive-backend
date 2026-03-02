from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

from ha_backend.job_registry import (
    HC_CANADA_CA_SCOPE_EXCLUDE_RX,
    HC_CANADA_CA_SCOPE_INCLUDE_RX,
    PHAC_CANADA_CA_SCOPE_EXCLUDE_RX,
    PHAC_CANADA_CA_SCOPE_INCLUDE_RX,
)
from ha_backend.models import ArchiveJob


def _load_script_module() -> Any:
    repo_root = Path(__file__).resolve().parents[1]
    script_path = repo_root / "scripts" / "vps-crawl-auto-recover.py"
    spec = importlib.util.spec_from_file_location("vps_crawl_auto_recover", script_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_compute_scope_args_rewrites_legacy_hc_scope() -> None:
    mod = _load_script_module()
    job = ArchiveJob(
        name="hc-20260101",
        status="running",
        config={
            "zimit_passthrough_args": [
                "--scopeType",
                "custom",
                "--scopeIncludeRx",
                "^https://www[.]canada[.]ca/(?:en/health-canada[.]html|content/dam/hc-sc/.*)$",
                "--customFlag",
                "value",
            ]
        },
    )

    normalized, drifted = mod._compute_scope_args_for_job(job, source_code="hc")
    assert drifted is True
    assert normalized[:6] == [
        "--scopeType",
        "custom",
        "--scopeIncludeRx",
        HC_CANADA_CA_SCOPE_INCLUDE_RX,
        "--scopeExcludeRx",
        HC_CANADA_CA_SCOPE_EXCLUDE_RX,
    ]
    assert normalized[6:] == ["--customFlag", "value"]


def test_compute_scope_args_noop_when_phac_scope_is_canonical() -> None:
    mod = _load_script_module()
    args = [
        "--scopeType",
        "custom",
        "--scopeIncludeRx",
        PHAC_CANADA_CA_SCOPE_INCLUDE_RX,
        "--scopeExcludeRx",
        PHAC_CANADA_CA_SCOPE_EXCLUDE_RX,
    ]
    job = ArchiveJob(
        name="phac-20260101",
        status="running",
        config={"zimit_passthrough_args": list(args)},
    )

    normalized, drifted = mod._compute_scope_args_for_job(job, source_code="phac")
    assert drifted is False
    assert normalized == args


def test_compute_scope_args_ignores_non_scoped_sources() -> None:
    mod = _load_script_module()
    args = ["--scopeType", "host"]
    job = ArchiveJob(
        name="cihr-20260101",
        status="running",
        config={"zimit_passthrough_args": list(args)},
    )

    normalized, drifted = mod._compute_scope_args_for_job(job, source_code="cihr")
    assert drifted is False
    assert normalized == args
