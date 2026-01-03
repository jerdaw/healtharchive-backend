from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any


def _load_script_module() -> Any:
    repo_root = Path(__file__).resolve().parents[1]
    script_path = repo_root / "scripts" / "verify_public_surface.py"
    spec = importlib.util.spec_from_file_location("verify_public_surface", script_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_verify_public_surface_build_frontend_pages_includes_en_and_fr() -> None:
    module = _load_script_module()

    pages = module._build_frontend_pages("https://www.healtharchive.ca", first_snapshot_id=123)
    urls = {url for _name, url in pages}

    assert "https://www.healtharchive.ca/archive" in urls
    assert "https://www.healtharchive.ca/fr/archive" in urls
    assert "https://www.healtharchive.ca/snapshot/123" in urls
    assert "https://www.healtharchive.ca/fr/snapshot/123" in urls
