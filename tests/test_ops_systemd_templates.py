from __future__ import annotations

import ast
from pathlib import Path


def test_warc_tiering_systemd_template_repairs_stale_mounts() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    unit_path = repo_root / "docs" / "deployment" / "systemd" / "healtharchive-warc-tiering.service"
    text = unit_path.read_text(encoding="utf-8")
    assert "--repair-stale-mounts" in text
    assert "vps-warc-tiering-bind-mounts.sh --apply --repair-stale-mounts" in text


def test_storage_hotpath_auto_recover_systemd_template_requires_venv() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    unit_path = (
        repo_root
        / "docs"
        / "deployment"
        / "systemd"
        / "healtharchive-storage-hotpath-auto-recover.service"
    )
    text = unit_path.read_text(encoding="utf-8")
    assert "ConditionPathExists=/opt/healtharchive-backend/.venv/bin/python3" in text
    assert (
        "ExecStart=/opt/healtharchive-backend/.venv/bin/python3 "
        "/opt/healtharchive-backend/scripts/vps-storage-hotpath-auto-recover.py --apply"
    ) in text


def test_storage_hotpath_auto_recover_script_has_no_top_level_backend_imports() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    script_path = repo_root / "scripts" / "vps-storage-hotpath-auto-recover.py"
    mod = ast.parse(script_path.read_text(encoding="utf-8"))
    top_level_imports = [
        node
        for node in mod.body
        if isinstance(node, (ast.Import, ast.ImportFrom))
        and (
            (isinstance(node, ast.ImportFrom) and (node.module or "").startswith("ha_backend"))
            or any(
                (alias.name or "").startswith("ha_backend")
                for alias in getattr(node, "names", [])
                if isinstance(alias, ast.alias)
            )
        )
    ]
    assert top_level_imports == []


def test_worker_auto_start_systemd_template_requires_venv() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    unit_path = (
        repo_root / "docs" / "deployment" / "systemd" / "healtharchive-worker-auto-start.service"
    )
    text = unit_path.read_text(encoding="utf-8")
    assert "ConditionPathExists=/etc/healtharchive/worker-auto-start-enabled" in text
    assert "ConditionPathExists=/opt/healtharchive-backend/.venv/bin/python3" in text
    assert (
        "ExecStart=/opt/healtharchive-backend/.venv/bin/python3 "
        "/opt/healtharchive-backend/scripts/vps-worker-auto-start.py --apply"
    ) in text
