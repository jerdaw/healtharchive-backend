from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any


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


def test_simulate_stalled_job_requires_dry_run() -> None:
    module = _load_script_module()

    rc = module.main(["--simulate-stalled-job-id", "1", "--apply"])
    assert rc == 2


def test_simulate_stalled_job_requires_non_production_paths() -> None:
    module = _load_script_module()

    # Drill mode must not be allowed to write production watchdog state/metrics by default.
    rc = module.main(["--simulate-stalled-job-id", "1"])
    assert rc == 2
