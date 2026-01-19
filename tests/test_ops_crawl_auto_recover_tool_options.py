from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

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


def test_ensure_recovery_tool_options_adds_defaults() -> None:
    module = _load_script_module()

    job = ArchiveJob(
        status="running",
        config={
            "seeds": ["https://example.com/"],
            "campaign_kind": "annual",
            "tool_options": {
                "initial_workers": 1,
            },
        },
    )

    changed = module._ensure_recovery_tool_options(job)
    assert changed is True
    assert job.config is not None

    tool = job.config["tool_options"]
    assert tool["initial_workers"] == 1
    assert tool["enable_monitoring"] is True
    assert tool["enable_adaptive_restart"] is True
    assert tool["max_container_restarts"] == 20
    assert tool["error_threshold_timeout"] == 50
    assert tool["error_threshold_http"] == 50
    assert tool["backoff_delay_minutes"] == 2


def test_ensure_recovery_tool_options_preserves_existing_values() -> None:
    module = _load_script_module()

    job = ArchiveJob(
        status="running",
        config={
            "campaign_kind": "annual",
            "tool_options": {
                "enable_monitoring": True,
                "enable_adaptive_restart": True,
                "max_container_restarts": 25,
                "error_threshold_timeout": 60,
                "error_threshold_http": 60,
                "backoff_delay_minutes": 1,
                "initial_workers": 1,
            },
        },
    )

    changed = module._ensure_recovery_tool_options(job)
    assert changed is False
    assert job.config is not None

    tool = job.config["tool_options"]
    assert tool["enable_monitoring"] is True
    assert tool["enable_adaptive_restart"] is True
    assert tool["max_container_restarts"] == 25
    assert tool["initial_workers"] == 1


def test_ensure_recovery_tool_options_fixes_bad_max_container_restarts() -> None:
    module = _load_script_module()

    job = ArchiveJob(
        status="running",
        config={
            "campaign_kind": "annual",
            "tool_options": {
                "enable_monitoring": True,
                "enable_adaptive_restart": True,
                "max_container_restarts": "not-an-int",
            },
        },
    )

    changed = module._ensure_recovery_tool_options(job)
    assert changed is True
    assert job.config is not None
    assert job.config["tool_options"]["max_container_restarts"] == 20
