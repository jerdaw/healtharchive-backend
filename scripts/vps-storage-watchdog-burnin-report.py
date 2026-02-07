#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_iso_utc(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(str(raw))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _load_state(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _load_prom_metrics(path: Path) -> dict[str, float]:
    metrics: dict[str, float] = {}
    if not path.is_file():
        return metrics
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split(None, 1)
        if len(parts) != 2:
            continue
        key, raw_value = parts
        try:
            metrics[key] = float(raw_value)
        except ValueError:
            continue
    return metrics


def _count_recent_recoveries(state: dict[str, Any], *, since_utc: datetime) -> int:
    recoveries = state.get("recoveries")
    if not isinstance(recoveries, dict):
        return 0
    global_items = recoveries.get("global")
    if not isinstance(global_items, list):
        return 0

    count = 0
    for raw in global_items:
        ts = _parse_iso_utc(str(raw))
        if ts is None:
            continue
        if ts >= since_utc:
            count += 1
    return count


def _evaluate(
    *,
    state_path: Path,
    metrics_path: Path,
    window_hours: int,
    now_utc: datetime,
) -> dict[str, Any]:
    state = _load_state(state_path)
    metrics = _load_prom_metrics(metrics_path)

    now_ts = int(now_utc.timestamp())
    since_utc = now_utc - timedelta(hours=window_hours)

    enabled = int(metrics.get("healtharchive_storage_hotpath_auto_recover_enabled", 0))
    metrics_ok = int(metrics.get("healtharchive_storage_hotpath_auto_recover_metrics_ok", 0))
    detected_targets = int(
        metrics.get("healtharchive_storage_hotpath_auto_recover_detected_targets", 0)
    )
    apply_total = int(metrics.get("healtharchive_storage_hotpath_auto_recover_apply_total", 0))
    last_apply_ok = int(metrics.get("healtharchive_storage_hotpath_auto_recover_last_apply_ok", 0))
    last_apply_ts = int(
        metrics.get("healtharchive_storage_hotpath_auto_recover_last_apply_timestamp_seconds", 0)
    )

    last_apply_age_seconds: int | None
    if last_apply_ts > 0:
        last_apply_age_seconds = max(0, now_ts - last_apply_ts)
    else:
        last_apply_age_seconds = None

    persistent_failed_apply = (
        enabled == 1
        and apply_total > 0
        and last_apply_ok == 0
        and last_apply_age_seconds is not None
        and last_apply_age_seconds > 86400
    )

    recent_recoveries = _count_recent_recoveries(state, since_utc=since_utc)
    has_detected_targets_now = detected_targets > 0
    metrics_writer_unhealthy = metrics_ok == 0

    status = "ok"
    if metrics_writer_unhealthy or persistent_failed_apply:
        status = "fail"
    elif has_detected_targets_now:
        status = "warn"

    recommendations: list[str] = []
    if metrics_writer_unhealthy:
        recommendations.append(
            "Check healtharchive-storage-hotpath-auto-recover service/timer and textfile collector writes."
        )
    if persistent_failed_apply:
        recommendations.append(
            "Run storage stale-mount recovery playbook; inspect last_apply_errors/last_apply_warnings."
        )
    if has_detected_targets_now:
        recommendations.append(
            "Watchdog currently detects stale targets; triage before next crawl retries."
        )
    if recent_recoveries > 0:
        recommendations.append(
            f"{recent_recoveries} recoveries recorded in the last {window_hours}h; OK if expected."
        )
    if status == "ok":
        recommendations.append("No persistent failed-apply signal detected in current snapshot.")
    if not recommendations:
        recommendations.append("No action required.")

    return {
        "generatedAtUtc": now_utc.replace(microsecond=0).isoformat(),
        "windowHours": int(window_hours),
        "stateFile": str(state_path),
        "metricsFile": str(metrics_path),
        "status": status,
        "checks": {
            "metricsWriterHealthy": not metrics_writer_unhealthy,
            "persistentFailedApply": persistent_failed_apply,
            "detectedTargetsNow": has_detected_targets_now,
        },
        "metrics": {
            "enabled": enabled,
            "metricsOk": metrics_ok,
            "detectedTargets": detected_targets,
            "applyTotal": apply_total,
            "lastApplyOk": last_apply_ok,
            "lastApplyTimestampSeconds": last_apply_ts,
            "lastApplyAgeSeconds": last_apply_age_seconds,
        },
        "recent": {
            "recoveriesInWindow": recent_recoveries,
        },
        "recommendations": recommendations,
    }


def _print_human(summary: dict[str, Any]) -> None:
    print(f"Status: {summary['status'].upper()}")
    print(f"Generated: {summary['generatedAtUtc']}")
    print(f"Window: {summary['windowHours']}h")
    print("")
    print("Checks:")
    checks = summary["checks"]
    print(f"- metricsWriterHealthy: {checks['metricsWriterHealthy']}")
    print(f"- persistentFailedApply: {checks['persistentFailedApply']}")
    print(f"- detectedTargetsNow: {checks['detectedTargetsNow']}")
    print("")
    metrics = summary["metrics"]
    print("Watchdog metrics snapshot:")
    print(f"- enabled: {metrics['enabled']}")
    print(f"- metricsOk: {metrics['metricsOk']}")
    print(f"- detectedTargets: {metrics['detectedTargets']}")
    print(f"- applyTotal: {metrics['applyTotal']}")
    print(f"- lastApplyOk: {metrics['lastApplyOk']}")
    print(f"- lastApplyAgeSeconds: {metrics['lastApplyAgeSeconds']}")
    print(f"- recoveriesInWindow: {summary['recent']['recoveriesInWindow']}")
    print("")
    print("Recommendations:")
    for item in summary["recommendations"]:
        print(f"- {item}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Summarize storage watchdog rollout burn-in health from state + textfile metrics."
    )
    parser.add_argument(
        "--state-file",
        default="/srv/healtharchive/ops/watchdog/storage-hotpath-auto-recover.json",
        help="Watchdog state JSON file path.",
    )
    parser.add_argument(
        "--metrics-file",
        default=(
            "/var/lib/node_exporter/textfile_collector/"
            "healtharchive_storage_hotpath_auto_recover.prom"
        ),
        help="Watchdog Prometheus textfile path.",
    )
    parser.add_argument(
        "--window-hours",
        type=int,
        default=24 * 7,
        help="Lookback window for recovery count summaries (default: 168).",
    )
    parser.add_argument(
        "--now-epoch",
        type=int,
        default=None,
        help="Override current time for deterministic testing.",
    )
    parser.add_argument("--json", action="store_true", default=False, help="Emit JSON summary.")
    parser.add_argument(
        "--require-clean",
        action="store_true",
        default=False,
        help="Exit non-zero unless summary status is OK.",
    )
    args = parser.parse_args(argv)

    now_utc = (
        datetime.fromtimestamp(int(args.now_epoch), tz=timezone.utc)
        if args.now_epoch is not None
        else _utc_now()
    )

    summary = _evaluate(
        state_path=Path(str(args.state_file)),
        metrics_path=Path(str(args.metrics_file)),
        window_hours=max(1, int(args.window_hours)),
        now_utc=now_utc,
    )

    if args.json:
        print(json.dumps(summary, indent=2, sort_keys=True))
    else:
        _print_human(summary)

    if args.require_clean and summary["status"] != "ok":
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
