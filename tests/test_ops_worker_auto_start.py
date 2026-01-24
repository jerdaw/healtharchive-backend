from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path
from types import ModuleType

from ha_backend import db as db_module
from ha_backend.db import Base, get_engine, get_session
from ha_backend.models import ArchiveJob


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


def _init_test_db(tmp_path: Path, monkeypatch, name: str) -> None:
    db_path = tmp_path / name
    monkeypatch.setenv("HEALTHARCHIVE_DATABASE_URL", f"sqlite:///{db_path}")

    db_module._engine = None
    db_module._SessionLocal = None

    engine = get_engine()
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)


def test_worker_auto_start_disabled_without_sentinel(tmp_path, monkeypatch) -> None:
    mod = _load_script_module(
        "vps-worker-auto-start.py",
        module_name="ha_test_vps_worker_auto_start_disabled",
    )
    _init_test_db(tmp_path, monkeypatch, "worker_disabled.db")

    out_dir = tmp_path / "out"
    rc = mod.main(
        [
            "--sentinel-file",
            str(tmp_path / "missing-sentinel"),
            "--state-file",
            str(tmp_path / "state.json"),
            "--lock-file",
            str(tmp_path / "lock"),
            "--textfile-out-dir",
            str(out_dir),
            "--textfile-out-file",
            "worker.prom",
        ]
    )
    assert rc == 0

    prom = (out_dir / "worker.prom").read_text(encoding="utf-8")
    assert "healtharchive_worker_auto_start_enabled 0" in prom
    assert 'healtharchive_worker_auto_start_last_result{result="skip",reason="disabled"} 1' in prom


def test_worker_auto_start_refuses_during_deploy_lock(tmp_path, monkeypatch) -> None:
    mod = _load_script_module(
        "vps-worker-auto-start.py",
        module_name="ha_test_vps_worker_auto_start_deploy_lock",
    )
    _init_test_db(tmp_path, monkeypatch, "worker_deploy_lock.db")

    sentinel = tmp_path / "sentinel"
    sentinel.write_text("1", encoding="utf-8")

    monkeypatch.setattr(mod, "_systemctl_is_active", lambda _u: False)
    monkeypatch.setattr(mod, "_probe_readable_dir", lambda _p: (1, -1))

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
            "--deploy-lock-max-age-seconds",
            "9999",
            "--state-file",
            str(tmp_path / "state.json"),
            "--lock-file",
            str(tmp_path / "lock"),
            "--textfile-out-dir",
            str(out_dir),
            "--textfile-out-file",
            "worker.prom",
        ]
    )
    assert rc == 0

    prom = (out_dir / "worker.prom").read_text(encoding="utf-8")
    assert (
        'healtharchive_worker_auto_start_last_result{result="skip",reason="deploy_lock"} 1' in prom
    )


def test_worker_auto_start_refuses_when_storagebox_unreadable(tmp_path, monkeypatch) -> None:
    mod = _load_script_module(
        "vps-worker-auto-start.py",
        module_name="ha_test_vps_worker_auto_start_storagebox_unreadable",
    )
    _init_test_db(tmp_path, monkeypatch, "worker_storagebox.db")

    sentinel = tmp_path / "sentinel"
    sentinel.write_text("1", encoding="utf-8")

    monkeypatch.setattr(mod, "_systemctl_is_active", lambda _u: False)
    monkeypatch.setattr(mod, "_file_age_seconds", lambda _p, now_utc: None)
    monkeypatch.setattr(mod, "_probe_readable_dir", lambda _p: (0, 107))

    out_dir = tmp_path / "out"
    rc = mod.main(
        [
            "--apply",
            "--sentinel-file",
            str(sentinel),
            "--state-file",
            str(tmp_path / "state.json"),
            "--lock-file",
            str(tmp_path / "lock"),
            "--textfile-out-dir",
            str(out_dir),
            "--textfile-out-file",
            "worker.prom",
        ]
    )
    assert rc == 0

    prom = (out_dir / "worker.prom").read_text(encoding="utf-8")
    assert (
        'healtharchive_worker_auto_start_last_result{result="skip",reason="storagebox_unreadable_errno_107"} 1'
        in prom
    )


def test_worker_auto_start_skips_when_worker_active(tmp_path, monkeypatch) -> None:
    mod = _load_script_module(
        "vps-worker-auto-start.py",
        module_name="ha_test_vps_worker_auto_start_worker_active",
    )
    _init_test_db(tmp_path, monkeypatch, "worker_active.db")

    sentinel = tmp_path / "sentinel"
    sentinel.write_text("1", encoding="utf-8")

    monkeypatch.setattr(mod, "_systemctl_is_active", lambda _u: True)
    monkeypatch.setattr(mod, "_file_age_seconds", lambda _p, now_utc: None)
    monkeypatch.setattr(mod, "_probe_readable_dir", lambda _p: (1, -1))

    out_dir = tmp_path / "out"
    rc = mod.main(
        [
            "--apply",
            "--sentinel-file",
            str(sentinel),
            "--state-file",
            str(tmp_path / "state.json"),
            "--lock-file",
            str(tmp_path / "lock"),
            "--textfile-out-dir",
            str(out_dir),
            "--textfile-out-file",
            "worker.prom",
        ]
    )
    assert rc == 0

    prom = (out_dir / "worker.prom").read_text(encoding="utf-8")
    assert (
        'healtharchive_worker_auto_start_last_result{result="skip",reason="worker_active"} 1'
        in prom
    )


def test_worker_auto_start_refuses_when_db_has_running_jobs_but_worker_down(
    tmp_path, monkeypatch
) -> None:
    mod = _load_script_module(
        "vps-worker-auto-start.py",
        module_name="ha_test_vps_worker_auto_start_running_jobs_present",
    )
    _init_test_db(tmp_path, monkeypatch, "worker_running_jobs_present.db")

    sentinel = tmp_path / "sentinel"
    sentinel.write_text("1", encoding="utf-8")

    monkeypatch.setattr(mod, "_systemctl_is_active", lambda _u: False)
    monkeypatch.setattr(mod, "_file_age_seconds", lambda _p, now_utc: None)
    monkeypatch.setattr(mod, "_probe_readable_dir", lambda _p: (1, -1))

    with get_session() as session:
        session.add(
            ArchiveJob(
                name="running",
                output_dir=str(tmp_path / "jobs" / "hc" / "out"),
                status="running",
                config={},
            )
        )
        session.flush()

    out_dir = tmp_path / "out"
    rc = mod.main(
        [
            "--apply",
            "--sentinel-file",
            str(sentinel),
            "--state-file",
            str(tmp_path / "state.json"),
            "--lock-file",
            str(tmp_path / "lock"),
            "--textfile-out-dir",
            str(out_dir),
            "--textfile-out-file",
            "worker.prom",
        ]
    )
    assert rc == 0

    prom = (out_dir / "worker.prom").read_text(encoding="utf-8")
    assert (
        'healtharchive_worker_auto_start_last_result{result="skip",reason="running_jobs_present_worker_inactive"} 1'
        in prom
    )


def test_worker_auto_start_starts_worker_when_pending_jobs_and_safe(tmp_path, monkeypatch) -> None:
    mod = _load_script_module(
        "vps-worker-auto-start.py",
        module_name="ha_test_vps_worker_auto_start_start",
    )
    _init_test_db(tmp_path, monkeypatch, "worker_start.db")

    sentinel = tmp_path / "sentinel"
    sentinel.write_text("1", encoding="utf-8")

    monkeypatch.setattr(mod, "_systemctl_is_active", lambda _u: False)
    monkeypatch.setattr(mod, "_file_age_seconds", lambda _p, now_utc: None)
    monkeypatch.setattr(mod, "_probe_readable_dir", lambda _p: (1, -1))

    with get_session() as session:
        session.add(
            ArchiveJob(
                name="pending",
                output_dir=str(tmp_path / "jobs" / "hc" / "out"),
                status="queued",
                config={},
            )
        )
        session.flush()

    calls: list[list[str]] = []

    def fake_run(cmd: list[str], **kwargs):
        del kwargs
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(mod.subprocess, "run", fake_run)

    out_dir = tmp_path / "out"
    rc = mod.main(
        [
            "--apply",
            "--sentinel-file",
            str(sentinel),
            "--state-file",
            str(tmp_path / "state.json"),
            "--lock-file",
            str(tmp_path / "lock"),
            "--textfile-out-dir",
            str(out_dir),
            "--textfile-out-file",
            "worker.prom",
        ]
    )
    assert rc == 0
    assert any(cmd[:2] == ["systemctl", "start"] for cmd in calls)

    prom = (out_dir / "worker.prom").read_text(encoding="utf-8")
    assert (
        'healtharchive_worker_auto_start_last_result{result="ok",reason="started_worker"} 1' in prom
    )
