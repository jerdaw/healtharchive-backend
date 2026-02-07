from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType


def _load_script_module(script_filename: str, module_name: str) -> ModuleType:
    repo_root = Path(__file__).resolve().parents[1]
    script_path = repo_root / "scripts" / script_filename
    spec = importlib.util.spec_from_file_location(module_name, script_path)
    assert spec is not None
    assert spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = mod
    spec.loader.exec_module(mod)
    return mod


def test_burnin_report_flags_persistent_failed_apply(tmp_path: Path, capsys) -> None:
    mod = _load_script_module(
        "vps-storage-watchdog-burnin-report.py",
        module_name="ha_test_vps_storage_watchdog_burnin_report_fail",
    )

    state_file = tmp_path / "state.json"
    state_file.write_text(
        json.dumps(
            {
                "recoveries": {
                    "global": [
                        "2026-02-01T00:00:00+00:00",
                        "2026-02-03T00:00:00+00:00",
                    ]
                },
                "last_apply_utc": "2026-02-03T00:00:00+00:00",
                "last_apply_ok": 0,
            }
        ),
        encoding="utf-8",
    )

    metrics_file = tmp_path / "watchdog.prom"
    metrics_file.write_text(
        "\n".join(
            [
                "healtharchive_storage_hotpath_auto_recover_enabled 1",
                "healtharchive_storage_hotpath_auto_recover_metrics_ok 1",
                "healtharchive_storage_hotpath_auto_recover_detected_targets 0",
                "healtharchive_storage_hotpath_auto_recover_apply_total 2",
                "healtharchive_storage_hotpath_auto_recover_last_apply_ok 0",
                "healtharchive_storage_hotpath_auto_recover_last_apply_timestamp_seconds 1738540800",
                "",
            ]
        ),
        encoding="utf-8",
    )

    # now=2026-02-06T00:00:00Z
    rc = mod.main(
        [
            "--state-file",
            str(state_file),
            "--metrics-file",
            str(metrics_file),
            "--window-hours",
            "168",
            "--now-epoch",
            "1738800000",
            "--json",
            "--require-clean",
        ]
    )
    assert rc == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "fail"
    assert payload["checks"]["persistentFailedApply"] is True


def test_burnin_report_is_ok_without_apply_attempts(tmp_path: Path, capsys) -> None:
    mod = _load_script_module(
        "vps-storage-watchdog-burnin-report.py",
        module_name="ha_test_vps_storage_watchdog_burnin_report_ok",
    )

    state_file = tmp_path / "state.json"
    state_file.write_text(json.dumps({"recoveries": {"global": []}}), encoding="utf-8")

    metrics_file = tmp_path / "watchdog.prom"
    metrics_file.write_text(
        "\n".join(
            [
                "healtharchive_storage_hotpath_auto_recover_enabled 1",
                "healtharchive_storage_hotpath_auto_recover_metrics_ok 1",
                "healtharchive_storage_hotpath_auto_recover_detected_targets 0",
                "healtharchive_storage_hotpath_auto_recover_apply_total 0",
                "healtharchive_storage_hotpath_auto_recover_last_apply_ok 0",
                "healtharchive_storage_hotpath_auto_recover_last_apply_timestamp_seconds 0",
                "",
            ]
        ),
        encoding="utf-8",
    )

    rc = mod.main(
        [
            "--state-file",
            str(state_file),
            "--metrics-file",
            str(metrics_file),
            "--json",
            "--require-clean",
        ]
    )
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "ok"
    assert payload["checks"]["persistentFailedApply"] is False


def test_burnin_report_warns_when_detected_targets_nonzero(tmp_path: Path, capsys) -> None:
    mod = _load_script_module(
        "vps-storage-watchdog-burnin-report.py",
        module_name="ha_test_vps_storage_watchdog_burnin_report_warn",
    )

    state_file = tmp_path / "state.json"
    state_file.write_text(json.dumps({"recoveries": {"global": []}}), encoding="utf-8")

    metrics_file = tmp_path / "watchdog.prom"
    metrics_file.write_text(
        "\n".join(
            [
                "healtharchive_storage_hotpath_auto_recover_enabled 1",
                "healtharchive_storage_hotpath_auto_recover_metrics_ok 1",
                "healtharchive_storage_hotpath_auto_recover_detected_targets 2",
                "healtharchive_storage_hotpath_auto_recover_apply_total 1",
                "healtharchive_storage_hotpath_auto_recover_last_apply_ok 1",
                "healtharchive_storage_hotpath_auto_recover_last_apply_timestamp_seconds 1738799000",
                "",
            ]
        ),
        encoding="utf-8",
    )

    rc = mod.main(
        [
            "--state-file",
            str(state_file),
            "--metrics-file",
            str(metrics_file),
            "--now-epoch",
            "1738800000",
            "--json",
        ]
    )
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "warn"
    assert payload["checks"]["detectedTargetsNow"] is True


def test_burnin_report_is_ok_when_recoveries_in_window_only(tmp_path: Path, capsys) -> None:
    mod = _load_script_module(
        "vps-storage-watchdog-burnin-report.py",
        module_name="ha_test_vps_storage_watchdog_burnin_report_recoveries_ok",
    )

    state_file = tmp_path / "state.json"
    state_file.write_text(
        json.dumps({"recoveries": {"global": ["2026-02-05T00:00:00+00:00"]}}),
        encoding="utf-8",
    )

    metrics_file = tmp_path / "watchdog.prom"
    metrics_file.write_text(
        "\n".join(
            [
                "healtharchive_storage_hotpath_auto_recover_enabled 1",
                "healtharchive_storage_hotpath_auto_recover_metrics_ok 1",
                "healtharchive_storage_hotpath_auto_recover_detected_targets 0",
                "healtharchive_storage_hotpath_auto_recover_apply_total 2",
                "healtharchive_storage_hotpath_auto_recover_last_apply_ok 1",
                "healtharchive_storage_hotpath_auto_recover_last_apply_timestamp_seconds 1738540800",
                "",
            ]
        ),
        encoding="utf-8",
    )

    rc = mod.main(
        [
            "--state-file",
            str(state_file),
            "--metrics-file",
            str(metrics_file),
            "--window-hours",
            "168",
            "--now-epoch",
            "1738800000",
            "--json",
            "--require-clean",
        ]
    )
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "ok"
