from __future__ import annotations

import re
from pathlib import Path


def test_alert_rule_names_are_unique() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    rules_path = repo_root / "ops" / "observability" / "alerting" / "healtharchive-alerts.yml"
    text = rules_path.read_text(encoding="utf-8")

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
    repo_root = Path(__file__).resolve().parents[1]
    rules_path = repo_root / "ops" / "observability" / "alerting" / "healtharchive-alerts.yml"
    text = rules_path.read_text(encoding="utf-8")

    names: set[str] = set()
    for line in text.splitlines():
        m = re.match(r"^\s*-\s*alert:\s*(\S+)\s*$", line)
        if m:
            names.add(m.group(1))

    assert "HealthArchiveCrawlRateSlowHC" in names
    assert "HealthArchiveCrawlRateSlowPHAC" in names
    assert "HealthArchiveCrawlRateSlowCIHR" in names
    assert "HealthArchiveCrawlRateSlow" not in names
