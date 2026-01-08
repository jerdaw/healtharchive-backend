from __future__ import annotations

import importlib.util
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import ModuleType

from ha_backend import db as db_module
from ha_backend.db import Base, get_engine, get_session
from ha_backend.models import ArchiveJob, Source
from ha_backend.seeds import seed_sources


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


def test_storage_hotpath_watchdog_requires_confirm_runs(tmp_path, monkeypatch, capsys) -> None:
    mod = _load_script_module(
        "vps-storage-hotpath-auto-recover.py",
        module_name="ha_test_vps_storage_hotpath_auto_recover_confirm",
    )
    _init_test_db(tmp_path, monkeypatch, "hotpath_confirm.db")

    jobs_root = tmp_path / "jobs"
    output_dir = jobs_root / "hc" / "jobdir"
    storagebox_mount = tmp_path / "storagebox"
    storagebox_mount.mkdir(parents=True)

    with get_session() as session:
        seed_sources(session)
        session.flush()
        hc = session.query(Source).filter_by(code="hc").one()
        job = ArchiveJob(
            source=hc,
            name="hc-20260101",
            status="running",
            started_at=datetime.now(timezone.utc),
            output_dir=str(output_dir),
            config={},
        )
        session.add(job)
        session.flush()

    # Simulate a stale output_dir (Errno 107) without touching the real FS.
    def fake_probe(path: Path) -> tuple[int, int]:
        if str(path) == str(output_dir):
            return 0, 107
        return 1, -1

    monkeypatch.setattr(mod, "_probe_readable_dir", fake_probe)
    monkeypatch.setattr(mod, "_systemctl_is_active", lambda _u: False)

    state_file = tmp_path / "state.json"
    lock_file = tmp_path / "lock"
    out_dir = tmp_path / "out"
    sentinel = tmp_path / "sentinel"

    # First run: observation recorded, but not eligible (confirm_runs=2).
    rc1 = mod.main(
        [
            "--jobs-root",
            str(jobs_root),
            "--storagebox-mount",
            str(storagebox_mount),
            "--state-file",
            str(state_file),
            "--lock-file",
            str(lock_file),
            "--sentinel-file",
            str(sentinel),
            "--textfile-out-dir",
            str(out_dir),
            "--textfile-out-file",
            "hotpath.prom",
            "--confirm-runs",
            "2",
            "--min-failure-age-seconds",
            "0",
        ]
    )
    assert rc1 == 0
    captured1 = capsys.readouterr()
    assert "eligible for recovery" not in captured1.out

    # Second run: now eligible (consecutive >= 2, min age == 0).
    rc2 = mod.main(
        [
            "--jobs-root",
            str(jobs_root),
            "--storagebox-mount",
            str(storagebox_mount),
            "--state-file",
            str(state_file),
            "--lock-file",
            str(lock_file),
            "--sentinel-file",
            str(sentinel),
            "--textfile-out-dir",
            str(out_dir),
            "--textfile-out-file",
            "hotpath.prom",
            "--confirm-runs",
            "2",
            "--min-failure-age-seconds",
            "0",
        ]
    )
    assert rc2 == 0
    captured2 = capsys.readouterr()
    assert "DRY-RUN: detected" in captured2.out
    assert "Planned actions (dry-run):" in captured2.out

    prom = (out_dir / "hotpath.prom").read_text(encoding="utf-8")
    assert "healtharchive_storage_hotpath_auto_recover_metrics_ok 1" in prom
    assert "healtharchive_storage_hotpath_auto_recover_detected_targets 1" in prom


def test_storage_hotpath_watchdog_apply_stops_and_restarts_worker_when_active(
    tmp_path, monkeypatch
) -> None:
    mod = _load_script_module(
        "vps-storage-hotpath-auto-recover.py",
        module_name="ha_test_vps_storage_hotpath_auto_recover_apply_active",
    )
    _init_test_db(tmp_path, monkeypatch, "hotpath_apply_active.db")

    jobs_root = tmp_path / "jobs"
    output_dir = jobs_root / "hc" / "jobdir"
    storagebox_mount = tmp_path / "storagebox"
    storagebox_mount.mkdir(parents=True)

    with get_session() as session:
        seed_sources(session)
        session.flush()
        hc = session.query(Source).filter_by(code="hc").one()
        job = ArchiveJob(
            source=hc,
            name="hc-20260101",
            status="running",
            started_at=datetime.now(timezone.utc),
            output_dir=str(output_dir),
            config={},
        )
        session.add(job)
        session.flush()

    calls: list[list[str]] = []
    repaired = {"ok": False}

    tiering_script = tmp_path / "tiering.sh"
    tiering_script.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    annual_script = tmp_path / "annual.py"
    annual_script.write_text("print('ok')\n", encoding="utf-8")

    def fake_run_apply(cmd: list[str], *, timeout_seconds: float | None = None):
        del timeout_seconds
        calls.append(cmd)
        if cmd and cmd[0] == str(tiering_script):
            repaired["ok"] = True
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(mod, "_run_apply", fake_run_apply)
    monkeypatch.setattr(mod, "_is_unit_present", lambda _u: True)
    monkeypatch.setattr(mod, "_systemctl_is_active", lambda _u: True)
    monkeypatch.setattr(
        mod,
        "_get_mount_info",
        lambda _p: {"source": "dummy", "target": str(output_dir), "fstype": "fuse.sshfs"},
    )

    def fake_probe(path: Path) -> tuple[int, int]:
        s = str(path)
        if s == str(output_dir):
            return (1, -1) if repaired["ok"] else (0, 107)
        if s == str(storagebox_mount):
            return 1, -1
        return 1, -1

    monkeypatch.setattr(mod, "_probe_readable_dir", fake_probe)

    state_file = tmp_path / "state.json"
    lock_file = tmp_path / "lock"
    out_dir = tmp_path / "out"
    sentinel = tmp_path / "sentinel"
    manifest = tmp_path / "warc-tiering.binds"
    manifest.write_text(f"{tmp_path / 'cold'} {tmp_path / 'hot'}\n", encoding="utf-8")

    rc = mod.main(
        [
            "--apply",
            "--jobs-root",
            str(jobs_root),
            "--storagebox-mount",
            str(storagebox_mount),
            "--manifest",
            str(manifest),
            "--state-file",
            str(state_file),
            "--lock-file",
            str(lock_file),
            "--sentinel-file",
            str(sentinel),
            "--tiering-apply-script",
            str(tiering_script),
            "--annual-output-tiering-script",
            str(annual_script),
            "--ha-backend",
            str(tmp_path / "ha-backend"),
            "--textfile-out-dir",
            str(out_dir),
            "--textfile-out-file",
            "hotpath.prom",
            "--confirm-runs",
            "1",
            "--min-failure-age-seconds",
            "0",
        ]
    )
    assert rc == 0

    flattened = [" ".join(c) for c in calls]
    assert any(c.startswith("systemctl stop healtharchive-worker.service") for c in flattened)
    assert any(c.startswith("umount ") and str(output_dir) in c for c in flattened)
    assert any(c.startswith(str(tiering_script)) for c in flattened)
    assert any("recover-stale-jobs" in c and "--source" in c and "hc" in c for c in flattened)
    assert any(c.startswith("systemctl start healtharchive-worker.service") for c in flattened)

    prom = (out_dir / "hotpath.prom").read_text(encoding="utf-8")
    assert "healtharchive_storage_hotpath_auto_recover_last_apply_ok 1" in prom


def test_storage_hotpath_watchdog_apply_does_not_start_worker_if_inactive(
    tmp_path, monkeypatch
) -> None:
    mod = _load_script_module(
        "vps-storage-hotpath-auto-recover.py",
        module_name="ha_test_vps_storage_hotpath_auto_recover_apply_inactive",
    )
    _init_test_db(tmp_path, monkeypatch, "hotpath_apply_inactive.db")

    jobs_root = tmp_path / "jobs"
    output_dir = jobs_root / "hc" / "jobdir"
    storagebox_mount = tmp_path / "storagebox"
    storagebox_mount.mkdir(parents=True)

    with get_session() as session:
        seed_sources(session)
        session.flush()
        hc = session.query(Source).filter_by(code="hc").one()
        job = ArchiveJob(
            source=hc,
            name="hc-20260101",
            status="running",
            started_at=datetime.now(timezone.utc),
            output_dir=str(output_dir),
            config={},
        )
        session.add(job)
        session.flush()

    calls: list[list[str]] = []
    repaired = {"ok": False}

    tiering_script = tmp_path / "tiering.sh"
    tiering_script.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    annual_script = tmp_path / "annual.py"
    annual_script.write_text("print('ok')\n", encoding="utf-8")

    def fake_run_apply(cmd: list[str], *, timeout_seconds: float | None = None):
        del timeout_seconds
        calls.append(cmd)
        if cmd and cmd[0] == str(tiering_script):
            repaired["ok"] = True
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(mod, "_run_apply", fake_run_apply)
    monkeypatch.setattr(mod, "_is_unit_present", lambda _u: True)
    monkeypatch.setattr(mod, "_systemctl_is_active", lambda _u: False)
    monkeypatch.setattr(
        mod,
        "_get_mount_info",
        lambda _p: {"source": "dummy", "target": str(output_dir), "fstype": "fuse.sshfs"},
    )

    def fake_probe(path: Path) -> tuple[int, int]:
        s = str(path)
        if s == str(output_dir):
            return (1, -1) if repaired["ok"] else (0, 107)
        if s == str(storagebox_mount):
            return 1, -1
        return 1, -1

    monkeypatch.setattr(mod, "_probe_readable_dir", fake_probe)

    state_file = tmp_path / "state.json"
    lock_file = tmp_path / "lock"
    out_dir = tmp_path / "out"
    sentinel = tmp_path / "sentinel"
    manifest = tmp_path / "warc-tiering.binds"
    manifest.write_text(f"{tmp_path / 'cold'} {tmp_path / 'hot'}\n", encoding="utf-8")

    rc = mod.main(
        [
            "--apply",
            "--jobs-root",
            str(jobs_root),
            "--storagebox-mount",
            str(storagebox_mount),
            "--manifest",
            str(manifest),
            "--state-file",
            str(state_file),
            "--lock-file",
            str(lock_file),
            "--sentinel-file",
            str(sentinel),
            "--tiering-apply-script",
            str(tiering_script),
            "--annual-output-tiering-script",
            str(annual_script),
            "--ha-backend",
            str(tmp_path / "ha-backend"),
            "--textfile-out-dir",
            str(out_dir),
            "--textfile-out-file",
            "hotpath.prom",
            "--confirm-runs",
            "1",
            "--min-failure-age-seconds",
            "0",
        ]
    )
    assert rc == 0

    flattened = [" ".join(c) for c in calls]
    assert not any(c.startswith("systemctl stop healtharchive-worker.service") for c in flattened)
    assert not any(c.startswith("systemctl start healtharchive-worker.service") for c in flattened)
