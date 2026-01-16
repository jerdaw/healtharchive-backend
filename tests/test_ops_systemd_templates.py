from __future__ import annotations

from pathlib import Path


def test_warc_tiering_systemd_template_repairs_stale_mounts() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    unit_path = repo_root / "docs" / "deployment" / "systemd" / "healtharchive-warc-tiering.service"
    text = unit_path.read_text(encoding="utf-8")
    assert "--repair-stale-mounts" in text
    assert "vps-warc-tiering-bind-mounts.sh --apply --repair-stale-mounts" in text
