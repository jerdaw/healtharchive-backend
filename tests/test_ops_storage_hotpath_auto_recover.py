from __future__ import annotations

import fcntl
import importlib.util
import json
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


def _assert_prom_contains_last_healthy(prom: str) -> None:
    assert "healtharchive_storage_hotpath_auto_recover_last_healthy_timestamp_seconds" in prom


def test_deploy_lock_probe_works_when_lock_file_is_readonly(tmp_path, monkeypatch) -> None:
    mod = _load_script_module(
        "vps-storage-hotpath-auto-recover.py",
        module_name="ha_test_vps_storage_hotpath_auto_recover_lock_probe_readonly",
    )
    _init_test_db(tmp_path, monkeypatch, "hotpath_lock_probe_readonly.db")

    lock_file = tmp_path / "deploy.lock"
    lock_file.write_text("pid=123\nstarted_at_utc=20260101T000000Z\n", encoding="utf-8")
    lock_file.chmod(0o444)

    active, age_seconds = mod._deploy_lock_is_active(
        lock_file,
        now_utc=datetime.now(timezone.utc),
        deploy_lock_max_age_seconds=9999,
    )
    assert active == 0
    assert age_seconds is not None


def test_storage_hotpath_watchdog_with_active_deploy_lock_records_detection_but_skips_actions(
    tmp_path, monkeypatch, capsys
) -> None:
    mod = _load_script_module(
        "vps-storage-hotpath-auto-recover.py",
        module_name="ha_test_vps_storage_hotpath_auto_recover_deploy_lock_active",
    )
    _init_test_db(tmp_path, monkeypatch, "hotpath_deploy_lock_active.db")

    jobs_root = tmp_path / "jobs"
    output_dir = jobs_root / "hc" / "jobdir"
    storagebox_mount = tmp_path / "storagebox"
    storagebox_mount.mkdir(parents=True)

    with get_session() as session:
        seed_sources(session)
        session.flush()
        hc = session.query(Source).filter_by(code="hc").one()
        session.add(
            ArchiveJob(
                source=hc,
                name="hc-20260101",
                status="running",
                started_at=datetime.now(timezone.utc),
                output_dir=str(output_dir),
                config={},
            )
        )
        session.flush()

    def fake_probe(path: Path) -> tuple[int, int]:
        if str(path) == str(output_dir):
            return 0, 107
        if str(path) == str(storagebox_mount):
            return 1, -1
        return 1, -1

    monkeypatch.setattr(mod, "_probe_readable_dir", fake_probe)
    monkeypatch.setattr(mod, "_systemctl_is_active", lambda _u: False)

    # Create and hold an exclusive lock, simulating an active deploy.
    deploy_lock = tmp_path / "deploy.lock"
    deploy_lock.write_text("pid=123\n", encoding="utf-8")
    with deploy_lock.open("a", encoding="utf-8") as f:
        fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

        state_file = tmp_path / "state.json"
        lock_file = tmp_path / "lock"
        out_dir = tmp_path / "out"
        sentinel = tmp_path / "sentinel"

        rc = mod.main(
            [
                "--apply",
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
                "--deploy-lock-file",
                str(deploy_lock),
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

    captured = capsys.readouterr()
    assert "DRY-RUN (deploy lock active)" in captured.out
    assert "Planned actions (dry-run):" in captured.out

    prom = (out_dir / "hotpath.prom").read_text(encoding="utf-8")
    assert "healtharchive_storage_hotpath_auto_recover_detected_targets 1" in prom
    assert "healtharchive_storage_hotpath_auto_recover_deploy_lock_active 1" in prom


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
    assert any(
        c.startswith(f"/opt/healtharchive-backend/.venv/bin/python3 {annual_script}")
        and "--repair-stale-mounts" in c
        and "--allow-repair-running-jobs" in c
        for c in flattened
    )
    assert any("recover-stale-jobs" in c and "--source" in c and "hc" in c for c in flattened)
    assert any(c.startswith("systemctl start healtharchive-worker.service") for c in flattened)

    prom = (out_dir / "hotpath.prom").read_text(encoding="utf-8")
    assert "healtharchive_storage_hotpath_auto_recover_last_apply_ok 1" in prom
    _assert_prom_contains_last_healthy(prom)


def test_storage_hotpath_watchdog_starts_worker_even_if_annual_tiering_fails_when_mounts_ok(
    tmp_path, monkeypatch
) -> None:
    mod = _load_script_module(
        "vps-storage-hotpath-auto-recover.py",
        module_name="ha_test_vps_storage_hotpath_auto_recover_annual_fail_ok",
    )
    _init_test_db(tmp_path, monkeypatch, "hotpath_annual_fail_ok.db")

    jobs_root = tmp_path / "jobs"
    output_dir = jobs_root / "hc" / "jobdir"
    storagebox_mount = tmp_path / "storagebox"
    storagebox_mount.mkdir(parents=True)

    with get_session() as session:
        seed_sources(session)
        session.flush()
        hc = session.query(Source).filter_by(code="hc").one()
        session.add(
            ArchiveJob(
                source=hc,
                name="hc-20260101",
                status="running",
                started_at=datetime.now(timezone.utc),
                output_dir=str(output_dir),
                config={},
            )
        )
        session.flush()

    calls: list[list[str]] = []
    repaired = {"ok": False}

    tiering_script = tmp_path / "tiering.sh"
    tiering_script.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    annual_script = tmp_path / "annual.py"
    annual_script.write_text("raise SystemExit(1)\n", encoding="utf-8")

    def fake_run_apply(cmd: list[str], *, timeout_seconds: float | None = None):
        del timeout_seconds
        calls.append(cmd)
        if cmd and cmd[0] == str(tiering_script):
            repaired["ok"] = True
        if cmd[:2] == ["/opt/healtharchive-backend/.venv/bin/python3", str(annual_script)]:
            return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="boom")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(mod, "_run_apply", fake_run_apply)
    monkeypatch.setattr(mod, "_is_unit_present", lambda _u: False)
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


def test_storage_hotpath_watchdog_repairs_next_job_output_dir_without_stopping_worker_when_crawl_healthy(
    tmp_path, monkeypatch
) -> None:
    mod = _load_script_module(
        "vps-storage-hotpath-auto-recover.py",
        module_name="ha_test_vps_storage_hotpath_auto_recover_next_jobs",
    )
    _init_test_db(tmp_path, monkeypatch, "hotpath_next_jobs.db")

    jobs_root = tmp_path / "jobs"
    storagebox_mount = tmp_path / "storagebox"
    storagebox_mount.mkdir(parents=True)

    hc_out = jobs_root / "hc" / "hc-jobdir"
    phac_out = jobs_root / "phac" / "phac-jobdir"

    with get_session() as session:
        seed_sources(session)
        session.flush()
        hc = session.query(Source).filter_by(code="hc").one()
        phac = session.query(Source).filter_by(code="phac").one()

        # Running crawl is healthy (output dir readable).
        session.add(
            ArchiveJob(
                source=hc,
                name="hc-20260101",
                status="running",
                started_at=datetime.now(timezone.utc),
                output_dir=str(hc_out),
                config={},
            )
        )
        # Next job is retryable but its output dir is stale (Errno 107).
        session.add(
            ArchiveJob(
                source=phac,
                name="phac-20260101",
                status="retryable",
                queued_at=datetime.now(timezone.utc),
                output_dir=str(phac_out),
                config={},
            )
        )
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
        if cmd[:2] == ["umount", str(phac_out)]:
            repaired["ok"] = True
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(mod, "_run_apply", fake_run_apply)
    monkeypatch.setattr(mod, "_is_unit_present", lambda _u: False)
    monkeypatch.setattr(mod, "_systemctl_is_active", lambda _u: True)
    monkeypatch.setattr(
        mod,
        "_get_mount_info",
        lambda p: {"source": "dummy", "target": str(p), "fstype": "fuse.sshfs"},
    )

    def fake_probe(path: Path) -> tuple[int, int]:
        s = str(path)
        if s == str(hc_out):
            return 1, -1
        if s == str(phac_out):
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
            "--next-jobs-limit",
            "10",
        ]
    )
    assert rc == 0

    flattened = [" ".join(c) for c in calls]
    assert not any(c.startswith("systemctl stop healtharchive-worker.service") for c in flattened)
    assert any(c.startswith("umount ") and str(phac_out) in c for c in flattened)
    assert not any("recover-stale-jobs" in c for c in flattened)

    # Safety: allow annual output tiering repairs, but do not allow repairing "running" jobs unless
    # the worker has been quiesced.
    annual_calls = [
        c
        for c in flattened
        if c.startswith(f"/opt/healtharchive-backend/.venv/bin/python3 {annual_script}")
    ]
    assert annual_calls
    assert any("--repair-stale-mounts" in c for c in annual_calls)
    assert not any("--allow-repair-running-jobs" in c for c in annual_calls)

    prom = (out_dir / "hotpath.prom").read_text(encoding="utf-8")
    assert "healtharchive_storage_hotpath_auto_recover_last_apply_ok 1" in prom


def test_storage_hotpath_watchdog_simulate_broken_path_dry_run_drill(
    tmp_path, monkeypatch, capsys
) -> None:
    mod = _load_script_module(
        "vps-storage-hotpath-auto-recover.py",
        module_name="ha_test_vps_storage_hotpath_auto_recover_simulate",
    )
    _init_test_db(tmp_path, monkeypatch, "hotpath_simulate.db")

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

    # No real filesystem failure: only the simulation flag should make the script "detect" a problem.
    monkeypatch.setattr(mod, "_probe_readable_dir", lambda _p: (1, -1))
    monkeypatch.setattr(mod, "_systemctl_is_active", lambda _u: True)
    monkeypatch.setattr(
        mod,
        "_get_mount_info",
        lambda _p: {"source": "dummy", "target": str(output_dir), "fstype": "fuse.sshfs"},
    )

    state_file = tmp_path / "state.json"
    lock_file = tmp_path / "lock"
    out_dir = tmp_path / "out"
    sentinel = tmp_path / "sentinel"
    manifest = tmp_path / "warc-tiering.binds"
    manifest.write_text(f"{tmp_path / 'cold'} {tmp_path / 'hot'}\n", encoding="utf-8")

    rc = mod.main(
        [
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
            "--textfile-out-dir",
            str(out_dir),
            "--textfile-out-file",
            "hotpath.prom",
            "--simulate-broken-path",
            str(output_dir),
            # Make this a fast, self-contained drill (no need to run twice).
            "--confirm-runs",
            "1",
            "--min-failure-age-seconds",
            "0",
        ]
    )
    assert rc == 0

    captured = capsys.readouterr()
    assert "DRILL: simulate-broken-path active" in captured.out
    assert "DRY-RUN: detected" in captured.out
    assert "Planned actions (dry-run):" in captured.out

    prom = (out_dir / "hotpath.prom").read_text(encoding="utf-8")
    assert "healtharchive_storage_hotpath_auto_recover_detected_targets 1" in prom
    _assert_prom_contains_last_healthy(prom)

    # Safety: simulation should never be allowed in apply mode.
    rc_apply = mod.main(
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
            "--textfile-out-dir",
            str(out_dir),
            "--textfile-out-file",
            "hotpath.prom",
            "--simulate-broken-path",
            str(output_dir),
        ]
    )
    assert rc_apply == 2


def test_storage_hotpath_watchdog_running_job_errno107_with_missing_mount_info_is_recoverable(
    tmp_path, monkeypatch
) -> None:
    mod = _load_script_module(
        "vps-storage-hotpath-auto-recover.py",
        module_name="ha_test_vps_storage_hotpath_auto_recover_missing_mount_running",
    )
    _init_test_db(tmp_path, monkeypatch, "hotpath_missing_mount_running.db")

    jobs_root = tmp_path / "jobs"
    output_dir = jobs_root / "hc" / "jobdir"
    storagebox_mount = tmp_path / "storagebox"
    storagebox_mount.mkdir(parents=True)

    with get_session() as session:
        seed_sources(session)
        session.flush()
        hc = session.query(Source).filter_by(code="hc").one()
        session.add(
            ArchiveJob(
                source=hc,
                name="hc-20260101",
                status="running",
                started_at=datetime.now(timezone.utc),
                output_dir=str(output_dir),
                config={},
            )
        )
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
        if cmd[:2] in (["umount", str(output_dir)], ["umount", "-l"]) and str(output_dir) in cmd:
            repaired["ok"] = True
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(mod, "_run_apply", fake_run_apply)
    monkeypatch.setattr(mod, "_is_unit_present", lambda _u: False)
    monkeypatch.setattr(mod, "_systemctl_is_active", lambda _u: True)
    monkeypatch.setattr(mod, "_get_mount_info", lambda _p: None)

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
    assert any(c.startswith("umount ") and str(output_dir) in c for c in flattened)

    prom = (out_dir / "hotpath.prom").read_text(encoding="utf-8")
    assert "healtharchive_storage_hotpath_auto_recover_last_apply_ok 1" in prom


def test_storage_hotpath_watchdog_next_job_errno107_with_missing_mount_info_is_recoverable(
    tmp_path, monkeypatch
) -> None:
    mod = _load_script_module(
        "vps-storage-hotpath-auto-recover.py",
        module_name="ha_test_vps_storage_hotpath_auto_recover_missing_mount_next_job",
    )
    _init_test_db(tmp_path, monkeypatch, "hotpath_missing_mount_next.db")

    jobs_root = tmp_path / "jobs"
    storagebox_mount = tmp_path / "storagebox"
    storagebox_mount.mkdir(parents=True)

    hc_out = jobs_root / "hc" / "hc-jobdir"
    phac_out = jobs_root / "phac" / "phac-jobdir"

    with get_session() as session:
        seed_sources(session)
        session.flush()
        hc = session.query(Source).filter_by(code="hc").one()
        phac = session.query(Source).filter_by(code="phac").one()
        session.add(
            ArchiveJob(
                source=hc,
                name="hc-20260101",
                status="running",
                started_at=datetime.now(timezone.utc),
                output_dir=str(hc_out),
                config={},
            )
        )
        session.add(
            ArchiveJob(
                source=phac,
                name="phac-20260101",
                status="retryable",
                queued_at=datetime.now(timezone.utc),
                output_dir=str(phac_out),
                config={},
            )
        )
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
        if cmd[:2] in (["umount", str(phac_out)], ["umount", "-l"]) and str(phac_out) in cmd:
            repaired["ok"] = True
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(mod, "_run_apply", fake_run_apply)
    monkeypatch.setattr(mod, "_is_unit_present", lambda _u: False)
    monkeypatch.setattr(mod, "_systemctl_is_active", lambda _u: True)
    monkeypatch.setattr(mod, "_get_mount_info", lambda _p: None)

    def fake_probe(path: Path) -> tuple[int, int]:
        s = str(path)
        if s == str(hc_out):
            return 1, -1
        if s == str(phac_out):
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
            "--next-jobs-limit",
            "10",
        ]
    )
    assert rc == 0

    flattened = [" ".join(c) for c in calls]
    assert any(c.startswith("umount ") and str(phac_out) in c for c in flattened)
    assert not any(c.startswith("systemctl stop healtharchive-worker.service") for c in flattened)
    assert not any(c.startswith("systemctl start healtharchive-worker.service") for c in flattened)


def test_storage_hotpath_watchdog_dry_run_apply_parity_for_same_stale_target(
    tmp_path, monkeypatch, capsys
) -> None:
    mod = _load_script_module(
        "vps-storage-hotpath-auto-recover.py",
        module_name="ha_test_vps_storage_hotpath_auto_recover_dry_run_apply_parity",
    )
    _init_test_db(tmp_path, monkeypatch, "hotpath_dry_run_apply_parity.db")

    jobs_root = tmp_path / "jobs"
    output_dir = jobs_root / "hc" / "jobdir"
    storagebox_mount = tmp_path / "storagebox"
    storagebox_mount.mkdir(parents=True)

    with get_session() as session:
        seed_sources(session)
        session.flush()
        hc = session.query(Source).filter_by(code="hc").one()
        session.add(
            ArchiveJob(
                source=hc,
                name="hc-20260101",
                status="running",
                started_at=datetime.now(timezone.utc),
                output_dir=str(output_dir),
                config={},
            )
        )
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
        if cmd[:2] in (["umount", str(output_dir)], ["umount", "-l"]) and str(output_dir) in cmd:
            repaired["ok"] = True
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(mod, "_run_apply", fake_run_apply)
    monkeypatch.setattr(mod, "_is_unit_present", lambda _u: False)
    monkeypatch.setattr(mod, "_systemctl_is_active", lambda _u: True)
    monkeypatch.setattr(mod, "_get_mount_info", lambda _p: None)

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

    rc_dry = mod.main(
        [
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
    assert rc_dry == 0
    captured = capsys.readouterr()
    assert "Planned actions (dry-run):" in captured.out
    assert f"- {output_dir} (errno=107, mount info unavailable)" in captured.out

    rc_apply = mod.main(
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
    assert rc_apply == 0
    assert any(c[:2] == ["umount", str(output_dir)] for c in calls)


def test_storage_hotpath_watchdog_apply_sets_last_apply_ok_zero_when_mount_stays_unreadable(
    tmp_path, monkeypatch
) -> None:
    mod = _load_script_module(
        "vps-storage-hotpath-auto-recover.py",
        module_name="ha_test_vps_storage_hotpath_auto_recover_apply_postcheck_failure",
    )
    _init_test_db(tmp_path, monkeypatch, "hotpath_apply_postcheck_failure.db")

    jobs_root = tmp_path / "jobs"
    output_dir = jobs_root / "hc" / "jobdir"
    storagebox_mount = tmp_path / "storagebox"
    storagebox_mount.mkdir(parents=True)

    with get_session() as session:
        seed_sources(session)
        session.flush()
        hc = session.query(Source).filter_by(code="hc").one()
        session.add(
            ArchiveJob(
                source=hc,
                name="hc-20260101",
                status="running",
                started_at=datetime.now(timezone.utc),
                output_dir=str(output_dir),
                config={},
            )
        )
        session.flush()

    calls: list[list[str]] = []

    tiering_script = tmp_path / "tiering.sh"
    tiering_script.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    annual_script = tmp_path / "annual.py"
    annual_script.write_text("print('ok')\n", encoding="utf-8")

    def fake_run_apply(cmd: list[str], *, timeout_seconds: float | None = None):
        del timeout_seconds
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(mod, "_run_apply", fake_run_apply)
    monkeypatch.setattr(mod, "_is_unit_present", lambda _u: False)
    monkeypatch.setattr(mod, "_systemctl_is_active", lambda _u: True)
    monkeypatch.setattr(mod, "_get_mount_info", lambda _p: None)

    def fake_probe(path: Path) -> tuple[int, int]:
        s = str(path)
        if s == str(output_dir):
            # Remains stale even after attempted recovery.
            return 0, 107
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
    assert rc == 1
    assert any(c[:2] == ["umount", str(output_dir)] for c in calls)

    state_payload = json.loads(state_file.read_text(encoding="utf-8"))
    assert int(state_payload.get("last_apply_ok") or 0) == 0
    errors = state_payload.get("last_apply_errors") or []
    assert any("mountpoint not restored" in str(e) for e in errors)

    prom = (out_dir / "hotpath.prom").read_text(encoding="utf-8")
    assert "healtharchive_storage_hotpath_auto_recover_last_apply_ok 0" in prom


def test_storage_hotpath_watchdog_reconciles_failed_tiering_unit_when_no_stale_targets(
    tmp_path, monkeypatch
) -> None:
    mod = _load_script_module(
        "vps-storage-hotpath-auto-recover.py",
        module_name="ha_test_vps_storage_hotpath_auto_recover_reconcile_tiering_unit",
    )
    _init_test_db(tmp_path, monkeypatch, "hotpath_reconcile_tiering_unit.db")

    jobs_root = tmp_path / "jobs"
    storagebox_mount = tmp_path / "storagebox"
    storagebox_mount.mkdir(parents=True)

    calls: list[list[str]] = []

    def fake_run_apply(cmd: list[str], *, timeout_seconds: float | None = None):
        del timeout_seconds
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(mod, "_run_apply", fake_run_apply)
    monkeypatch.setattr(mod, "_is_unit_present", lambda _u: True)
    monkeypatch.setattr(mod, "_systemctl_is_failed", lambda _u: True)
    monkeypatch.setattr(mod, "_probe_readable_dir", lambda _p: (1, -1))

    state_file = tmp_path / "state.json"
    lock_file = tmp_path / "lock"
    out_dir = tmp_path / "out"
    sentinel = tmp_path / "sentinel"

    rc = mod.main(
        [
            "--apply",
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
        ]
    )
    assert rc == 0

    flattened = [" ".join(c) for c in calls]
    assert any(c == "systemctl reset-failed healtharchive-warc-tiering.service" for c in flattened)
    assert any(c == "systemctl start healtharchive-warc-tiering.service" for c in flattened)

    state_payload = json.loads(state_file.read_text(encoding="utf-8"))
    assert int(state_payload.get("last_tiering_unit_reconcile_ok") or 0) == 1


def test_storage_hotpath_watchdog_does_not_reconcile_failed_tiering_unit_when_storage_unreadable(
    tmp_path, monkeypatch
) -> None:
    mod = _load_script_module(
        "vps-storage-hotpath-auto-recover.py",
        module_name="ha_test_vps_storage_hotpath_auto_recover_reconcile_tiering_unit_skip_storage",
    )
    _init_test_db(tmp_path, monkeypatch, "hotpath_reconcile_tiering_unit_skip_storage.db")

    jobs_root = tmp_path / "jobs"
    storagebox_mount = tmp_path / "storagebox"
    storagebox_mount.mkdir(parents=True)

    calls: list[list[str]] = []

    def fake_run_apply(cmd: list[str], *, timeout_seconds: float | None = None):
        del timeout_seconds
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    def fake_probe(path: Path) -> tuple[int, int]:
        if str(path) == str(storagebox_mount):
            return 0, 107
        return 1, -1

    monkeypatch.setattr(mod, "_run_apply", fake_run_apply)
    monkeypatch.setattr(mod, "_is_unit_present", lambda _u: True)
    monkeypatch.setattr(mod, "_systemctl_is_failed", lambda _u: True)
    monkeypatch.setattr(mod, "_probe_readable_dir", fake_probe)

    state_file = tmp_path / "state.json"
    lock_file = tmp_path / "lock"
    out_dir = tmp_path / "out"
    sentinel = tmp_path / "sentinel"

    rc = mod.main(
        [
            "--apply",
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
        ]
    )
    assert rc == 0
    assert calls == []
