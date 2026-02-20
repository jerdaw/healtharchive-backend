from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
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


def test_drift_auto_reconcile_disabled_without_sentinel(tmp_path) -> None:
    mod = _load_script_module(
        "vps-drift-auto-reconcile.py",
        module_name="ha_test_vps_drift_auto_reconcile_disabled",
    )

    out_dir = tmp_path / "out"
    rc = mod.main(
        [
            "--sentinel-file",
            str(tmp_path / "missing-sentinel"),
            "--drift-report",
            str(tmp_path / "missing-report"),
            "--state-file",
            str(tmp_path / "state.json"),
            "--lock-file",
            str(tmp_path / "lock"),
            "--textfile-out-dir",
            str(out_dir),
            "--textfile-out-file",
            "drift.prom",
        ]
    )
    assert rc == 0

    prom = (out_dir / "drift.prom").read_text(encoding="utf-8")
    assert "healtharchive_drift_auto_reconcile_enabled 0" in prom
    assert (
        'healtharchive_drift_auto_reconcile_last_result{result="skip",reason="disabled"} 1' in prom
    )


def test_drift_auto_reconcile_refuses_during_deploy_lock(tmp_path, monkeypatch) -> None:
    mod = _load_script_module(
        "vps-drift-auto-reconcile.py",
        module_name="ha_test_vps_drift_auto_reconcile_deploy_lock",
    )

    sentinel = tmp_path / "sentinel"
    sentinel.write_text("1", encoding="utf-8")

    def fake_deploy_lock_is_active(_p: Path, *, now_utc, deploy_lock_max_age_seconds: float):
        del now_utc, deploy_lock_max_age_seconds
        return 1, 10.0

    monkeypatch.setattr(mod, "_deploy_lock_is_active", fake_deploy_lock_is_active)

    out_dir = tmp_path / "out"
    rc = mod.main(
        [
            "--apply",
            "--sentinel-file",
            str(sentinel),
            "--deploy-lock-file",
            str(tmp_path / "deploy.lock"),
            "--drift-report",
            str(tmp_path / "missing-report"),
            "--state-file",
            str(tmp_path / "state.json"),
            "--lock-file",
            str(tmp_path / "lock"),
            "--textfile-out-dir",
            str(out_dir),
            "--textfile-out-file",
            "drift.prom",
        ]
    )
    assert rc == 0

    prom = (out_dir / "drift.prom").read_text(encoding="utf-8")
    assert (
        'healtharchive_drift_auto_reconcile_last_result{result="skip",reason="deploy_lock_held"} 1'
        in prom
    )


def test_drift_auto_reconcile_cooldown(tmp_path, monkeypatch) -> None:
    mod = _load_script_module(
        "vps-drift-auto-reconcile.py",
        module_name="ha_test_vps_drift_auto_reconcile_cooldown",
    )

    sentinel = tmp_path / "sentinel"
    sentinel.write_text("1", encoding="utf-8")

    monkeypatch.setattr(
        mod,
        "_deploy_lock_is_active",
        lambda _p, now_utc=None, deploy_lock_max_age_seconds=None: (0, 10.0),
    )

    # set up recent state
    now = datetime.now(timezone.utc)
    recent = now - timedelta(minutes=5)

    state_file = tmp_path / "state.json"
    state_file.write_text(
        json.dumps(
            {
                "last_run_utc": recent.isoformat(),
                "result": "ok",
                "reason": "reconciled_successfully",
            }
        ),
        encoding="utf-8",
    )

    out_dir = tmp_path / "out"
    rc = mod.main(
        [
            "--apply",
            "--sentinel-file",
            str(sentinel),
            "--drift-report",
            str(tmp_path / "missing-report"),
            "--state-file",
            str(state_file),
            "--lock-file",
            str(tmp_path / "lock"),
            "--textfile-out-dir",
            str(out_dir),
            "--textfile-out-file",
            "drift.prom",
            "--cooldown-minutes",
            "15.0",
        ]
    )
    assert rc == 0

    prom = (out_dir / "drift.prom").read_text(encoding="utf-8")
    assert (
        'healtharchive_drift_auto_reconcile_last_result{result="skip",reason="cooldown"} 1' in prom
    )


def test_drift_auto_reconcile_skips_when_no_drift(tmp_path, monkeypatch) -> None:
    mod = _load_script_module(
        "vps-drift-auto-reconcile.py",
        module_name="ha_test_vps_drift_auto_reconcile_skips",
    )

    sentinel = tmp_path / "sentinel"
    sentinel.write_text("1", encoding="utf-8")

    monkeypatch.setattr(
        mod,
        "_deploy_lock_is_active",
        lambda _p, now_utc=None, deploy_lock_max_age_seconds=None: (0, 10.0),
    )

    drift_report = tmp_path / "drift.txt"
    drift_report.write_text("FAILURES (must fix)\n- something else", encoding="utf-8")

    out_dir = tmp_path / "out"
    rc = mod.main(
        [
            "--apply",
            "--sentinel-file",
            str(sentinel),
            "--drift-report",
            str(drift_report),
            "--state-file",
            str(tmp_path / "state.json"),
            "--lock-file",
            str(tmp_path / "lock"),
            "--textfile-out-dir",
            str(out_dir),
            "--textfile-out-file",
            "drift.prom",
        ]
    )
    assert rc == 0

    prom = (out_dir / "drift.prom").read_text(encoding="utf-8")
    assert (
        'healtharchive_drift_auto_reconcile_last_result{result="skip",reason="no_dependency_drift"} 1'
        in prom
    )


def test_drift_auto_reconcile_triggers_reconcile(tmp_path, monkeypatch) -> None:
    mod = _load_script_module(
        "vps-drift-auto-reconcile.py",
        module_name="ha_test_vps_drift_auto_reconcile_triggers",
    )

    sentinel = tmp_path / "sentinel"
    sentinel.write_text("1", encoding="utf-8")

    monkeypatch.setattr(
        mod,
        "_deploy_lock_is_active",
        lambda _p, now_utc=None, deploy_lock_max_age_seconds=None: (0, 10.0),
    )

    drift_report = tmp_path / "drift.txt"
    drift_report.write_text(
        "FAILURES (must fix)\n- dependencies: missing slowapi", encoding="utf-8"
    )

    calls: list[list[str]] = []

    def fake_run(cmd: list[str], **kwargs):
        del kwargs
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(mod.subprocess, "run", fake_run)

    out_dir = tmp_path / "out"
    deploy_script = tmp_path / "deploy.sh"

    rc = mod.main(
        [
            "--apply",
            "--sentinel-file",
            str(sentinel),
            "--drift-report",
            str(drift_report),
            "--state-file",
            str(tmp_path / "state.json"),
            "--lock-file",
            str(tmp_path / "lock"),
            "--deploy-script",
            str(deploy_script),
            "--textfile-out-dir",
            str(out_dir),
            "--textfile-out-file",
            "drift.prom",
        ]
    )
    assert rc == 0
    assert any(cmd[:3] == [str(deploy_script), "--apply", "--skip-worker-restart"] for cmd in calls)

    prom = (out_dir / "drift.prom").read_text(encoding="utf-8")
    assert (
        'healtharchive_drift_auto_reconcile_last_result{result="ok",reason="reconciled_successfully"} 1'
        in prom
    )
