from __future__ import annotations

import re
from pathlib import Path


def _rules_text() -> str:
    repo_root = Path(__file__).resolve().parents[1]
    rules_path = repo_root / "ops" / "observability" / "alerting" / "healtharchive-alerts.yml"
    return rules_path.read_text(encoding="utf-8")


def _extract_alert_block(text: str, alert_name: str) -> str:
    pattern = re.compile(
        rf"(?ms)^\s*-\s*alert:\s*{re.escape(alert_name)}\s*\n(?P<body>.*?)(?=^\s*-\s*alert:\s|\Z)"
    )
    match = pattern.search(text)
    assert match is not None, f"alert block not found: {alert_name}"
    return match.group("body")


def test_alert_rule_names_are_unique() -> None:
    text = _rules_text()

    names: list[str] = []
    for line in text.splitlines():
        m = re.match(r"^\s*-\s*alert:\s*(\S+)\s*$", line)
        if m:
            names.append(m.group(1))

    assert names, "no alert rules found"
    seen: set[str] = set()
    dupes: list[str] = []
    for name in names:
        if name in seen and name not in dupes:
            dupes.append(name)
        seen.add(name)
    assert not dupes, f"duplicate alert names found: {dupes}"


def test_crawl_rate_alerts_are_source_specific() -> None:
    text = _rules_text()

    names: set[str] = set()
    for line in text.splitlines():
        m = re.match(r"^\s*-\s*alert:\s*(\S+)\s*$", line)
        if m:
            names.add(m.group(1))

    assert "HealthArchiveCrawlRateSlowHC" in names
    assert "HealthArchiveCrawlRateSlowPHAC" in names
    assert "HealthArchiveCrawlRateSlowCIHR" in names
    assert "HealthArchiveCrawlRateSlow" not in names


def test_storage_hotpath_apply_failed_persistent_alert_semantics() -> None:
    text = _rules_text()
    body = _extract_alert_block(text, "HealthArchiveStorageHotpathApplyFailedPersistent")

    assert "healtharchive_storage_hotpath_auto_recover_enabled == 1" in body
    assert "healtharchive_storage_hotpath_auto_recover_apply_total > 0" in body
    assert "healtharchive_storage_hotpath_auto_recover_last_apply_ok == 0" in body
    assert (
        "(time() - healtharchive_storage_hotpath_auto_recover_last_apply_timestamp_seconds) > 86400"
        in body
    )
    assert re.search(r"^\s*for:\s*30m\s*$", body, re.MULTILINE)
    assert re.search(r"^\s*severity:\s*warning\s*$", body, re.MULTILINE)


def test_annual_output_dir_not_writable_alert_semantics() -> None:
    text = _rules_text()
    body = _extract_alert_block(text, "HealthArchiveAnnualOutputDirNotWritable")

    assert "healtharchive_crawl_annual_pending_output_dir_probe_user_ok == 1" in body
    assert "healtharchive_crawl_annual_pending_job_output_dir_writable == 0" in body
    assert re.search(r"^\s*for:\s*10m\s*$", body, re.MULTILINE)
    assert re.search(r"^\s*severity:\s*warning\s*$", body, re.MULTILINE)
