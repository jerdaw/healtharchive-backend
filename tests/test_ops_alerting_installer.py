from __future__ import annotations

from pathlib import Path


def _script_text() -> str:
    repo_root = Path(__file__).resolve().parents[1]
    script_path = repo_root / "scripts" / "vps-install-observability-alerting.sh"
    return script_path.read_text(encoding="utf-8")


def test_alertmanager_routing_uses_severity_aware_receivers() -> None:
    text = _script_text()

    assert "receiver: healtharchive-webhook-noncritical" in text
    assert '- severity="critical"' in text
    assert "receiver: healtharchive-webhook-critical" in text
    assert "repeat_interval: 24h" in text
    assert "repeat_interval: 6h" in text
    assert "send_resolved: true" in text
    assert "send_resolved: false" in text


def test_alertmanager_unit_detection_dry_run_fallback_exists() -> None:
    text = _script_text()

    assert 'if [[ "${APPLY}" != "true" ]]; then' in text
    assert 'AM_UNIT="prometheus-alertmanager.service"' in text
    assert "Could not discover Alertmanager unit in dry-run" in text
