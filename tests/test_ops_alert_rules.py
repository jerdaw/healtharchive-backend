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


def test_crawl_rate_alerts_removed_in_favor_of_dashboard_signals() -> None:
    text = _rules_text()

    names: set[str] = set()
    for line in text.splitlines():
        m = re.match(r"^\s*-\s*alert:\s*(\S+)\s*$", line)
        if m:
            names.add(m.group(1))

    assert "HealthArchiveCrawlRateSlowHC" not in names
    assert "HealthArchiveCrawlRateSlowPHAC" not in names
    assert "HealthArchiveCrawlRateSlowCIHR" not in names
    assert "HealthArchiveCrawlRateSlow" not in names
    assert "HealthArchiveCrawlNewPhaseChurn" not in names


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
    assert "healtharchive_crawl_annual_pending_job_output_dir_writable_errno != 107" in body
    assert re.search(r"^\s*for:\s*10m\s*$", body, re.MULTILINE)
    assert re.search(r"^\s*severity:\s*warning\s*$", body, re.MULTILINE)


def test_crawl_container_restarts_high_alert_semantics() -> None:
    text = _rules_text()
    body = _extract_alert_block(text, "HealthArchiveCrawlContainerRestartsHigh")

    assert 'healtharchive_crawl_running_job_container_restarts_done{source="hc"} >= 19' in body
    assert 'healtharchive_crawl_running_job_container_restarts_done{source="phac"} >= 24' in body
    assert 'healtharchive_crawl_running_job_container_restarts_done{source="cihr"} >= 16' in body
    assert (
        'healtharchive_crawl_running_job_container_restarts_done{source!~"hc|phac|cihr"} >= 16'
        in body
    )
    assert re.search(r"^\s*for:\s*30m\s*$", body, re.MULTILINE)
    assert re.search(r"^\s*severity:\s*warning\s*$", body, re.MULTILINE)


def test_worker_down_alert_is_automation_aware() -> None:
    text = _rules_text()
    body = _extract_alert_block(text, "HealthArchiveWorkerDownWhileJobsPending")

    assert "healtharchive_worker_should_be_running == 1" in body
    assert "healtharchive_worker_active == 0" in body
    assert "healtharchive_worker_auto_start_enabled == 1" in body
    assert "healtharchive_worker_auto_start_last_run_timestamp_seconds" in body
    assert "healtharchive_worker_auto_start_deploy_lock_present == 0" in body
    assert "absent(healtharchive_worker_auto_start_enabled)" in body
    assert re.search(r"^\s*for:\s*20m\s*$", body, re.MULTILINE)
    assert re.search(r"^\s*severity:\s*critical\s*$", body, re.MULTILINE)


def test_crawl_output_dir_unreadable_excludes_errno_107() -> None:
    text = _rules_text()
    body = _extract_alert_block(text, "HealthArchiveCrawlOutputDirUnreadable")

    assert "healtharchive_crawl_running_job_output_dir_ok == 0" in body
    assert "healtharchive_crawl_running_job_output_dir_errno != 107" in body
    assert re.search(r"^\s*for:\s*2m\s*$", body, re.MULTILINE)


def test_watchdog_metrics_freshness_alerts_exist() -> None:
    text = _rules_text()
    worker = _extract_alert_block(text, "HealthArchiveWorkerAutoStartMetricsStale")
    crawl = _extract_alert_block(text, "HealthArchiveCrawlAutoRecoverMetricsStale")

    assert "healtharchive_worker_auto_start_enabled == 1" in worker
    assert "healtharchive_worker_auto_start_last_run_timestamp_seconds" in worker
    assert re.search(r"^\s*for:\s*5m\s*$", worker, re.MULTILINE)

    assert "healtharchive_crawl_auto_recover_enabled == 1" in crawl
    assert "healtharchive_crawl_auto_recover_last_run_timestamp_seconds" in crawl
    assert re.search(r"^\s*for:\s*10m\s*$", crawl, re.MULTILINE)


def test_deploy_lock_persistent_alert_semantics() -> None:
    text = _rules_text()
    body = _extract_alert_block(text, "HealthArchiveDeployLockPersistent")

    assert "healtharchive_crawl_auto_recover_deploy_lock_present == 1" in body
    assert re.search(r"^\s*for:\s*4h\s*$", body, re.MULTILINE)
    assert re.search(r"^\s*severity:\s*warning\s*$", body, re.MULTILINE)


def test_crawl_temp_dirs_high_alert_semantics() -> None:
    text = _rules_text()
    body = _extract_alert_block(text, "HealthArchiveCrawlTempDirsHigh")

    assert "healtharchive_crawl_running_job_temp_dirs_count > 100" in body
    assert re.search(r"^\s*for:\s*1h\s*$", body, re.MULTILINE)
    assert re.search(r"^\s*severity:\s*warning\s*$", body, re.MULTILINE)


def test_crawl_rate_degraded_alert_semantics() -> None:
    text = _rules_text()
    body = _extract_alert_block(text, "HealthArchiveCrawlRateDegraded")

    assert 'healtharchive_crawl_running_job_crawl_rate_ppm{source=~"hc|phac"} >= 0' in body
    assert 'healtharchive_crawl_running_job_crawl_rate_ppm{source=~"hc|phac"} < 2' in body
    assert (
        'healtharchive_crawl_running_job_last_progress_age_seconds{source=~"hc|phac"} <= 300'
        in body
    )
    assert 'healtharchive_crawl_running_job_stalled{source=~"hc|phac"} == 0' in body
    assert re.search(r"^\s*for:\s*45m\s*$", body, re.MULTILINE)
    assert re.search(r"^\s*severity:\s*warning\s*$", body, re.MULTILINE)
