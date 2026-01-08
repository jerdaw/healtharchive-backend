#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import stat
import subprocess
from datetime import datetime, timezone
from pathlib import Path


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _dt_to_epoch_seconds(dt: datetime) -> int:
    return int(dt.astimezone(timezone.utc).timestamp())


def _is_mountpoint(path: Path) -> bool:
    if not path.exists():
        return False
    try:
        r = subprocess.run(["mountpoint", "-q", str(path)], check=False)
        return r.returncode == 0
    except FileNotFoundError:
        pass
    out = subprocess.run(["mount"], check=False, capture_output=True, text=True).stdout
    return f" on {path} " in out


def _probe_readable_dir(path: Path) -> tuple[int, int]:
    try:
        st = path.stat()
    except OSError as exc:
        return 0, int(exc.errno or -1)
    if not stat.S_ISDIR(st.st_mode):
        return 0, 0
    try:
        os.listdir(path)
    except OSError as exc:
        return 0, int(exc.errno or -1)
    return 1, -1


def _unit_exists(unit: str) -> bool:
    r = subprocess.run(["systemctl", "cat", unit], check=False, capture_output=True, text=True)
    return r.returncode == 0


def _unit_ok(unit: str) -> int:
    """
    1 if the unit exists and is not failed (and active when applicable).

    Note: for oneshot services without RemainAfterExit, is-active may be "inactive"
    even when the last run succeeded; we intentionally do not use unit_ok for those.
    """
    if not _unit_exists(unit):
        return 0
    active = subprocess.run(
        ["systemctl", "is-active", unit], check=False, capture_output=True, text=True
    ).stdout.strip()
    failed = subprocess.run(
        ["systemctl", "is-failed", unit], check=False, capture_output=True, text=True
    ).stdout.strip()
    return 1 if active == "active" and failed != "failed" else 0


def _unit_failed(unit: str) -> int:
    if not _unit_exists(unit):
        return 0
    failed = subprocess.run(
        ["systemctl", "is-failed", unit], check=False, capture_output=True, text=True
    ).stdout.strip()
    return 1 if failed == "failed" else 0


def _read_manifest_hot_paths(manifest_path: Path) -> tuple[int, list[Path]]:
    """
    Return (manifest_ok, hot_paths).

    manifest_ok=1 means the file exists and was parsed without error.
    """
    if not manifest_path.is_file():
        return 0, []
    hot_paths: list[Path] = []
    try:
        for raw in manifest_path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split()
            if len(parts) < 2:
                continue
            hot_paths.append(Path(parts[1]))
    except Exception:
        return 0, []
    return 1, hot_paths


def _emit(lines: list[str], line: str) -> None:
    lines.append(line.rstrip("\n"))


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description=(
            "HealthArchive VPS helper: emit tiering/storage health metrics via node_exporter textfile collector."
        )
    )
    p.add_argument(
        "--out-dir",
        default="/var/lib/node_exporter/textfile_collector",
        help="node_exporter textfile collector directory.",
    )
    p.add_argument(
        "--out-file",
        default="healtharchive_tiering.prom",
        help="Output filename under --out-dir.",
    )
    p.add_argument(
        "--storagebox-mount",
        default="/srv/healtharchive/storagebox",
        help="Storage Box mountpoint.",
    )
    p.add_argument(
        "--manifest",
        default="/etc/healtharchive/warc-tiering.binds",
        help="WARC tiering bind-mount manifest (for hot-path checks).",
    )
    args = p.parse_args(argv)

    now = _utc_now()
    out_dir = Path(str(args.out_dir))
    out_file = out_dir / str(args.out_file)

    storagebox_mount = Path(str(args.storagebox_mount))
    manifest_path = Path(str(args.manifest))

    storagebox_ok = 0
    if _is_mountpoint(storagebox_mount):
        ok, _errno = _probe_readable_dir(storagebox_mount)
        storagebox_ok = ok

    storagebox_service_ok = _unit_ok("healtharchive-storagebox-sshfs.service")
    tiering_service_ok = _unit_ok("healtharchive-warc-tiering.service")
    tiering_service_failed = _unit_failed("healtharchive-warc-tiering.service")

    manifest_ok, hot_paths = _read_manifest_hot_paths(manifest_path)

    lines: list[str] = []

    _emit(
        lines,
        "# HELP healtharchive_storagebox_mount_ok 1 if Storage Box mount is present and readable.",
    )
    _emit(lines, "# TYPE healtharchive_storagebox_mount_ok gauge")
    _emit(lines, f"healtharchive_storagebox_mount_ok {storagebox_ok}")

    _emit(
        lines,
        "# HELP healtharchive_systemd_unit_ok 1 if the unit exists and is not failed (and active when applicable).",
    )
    _emit(lines, "# TYPE healtharchive_systemd_unit_ok gauge")
    _emit(
        lines,
        f'healtharchive_systemd_unit_ok{{unit="healtharchive-storagebox-sshfs.service"}} {storagebox_service_ok}',
    )
    _emit(
        lines,
        f'healtharchive_systemd_unit_ok{{unit="healtharchive-warc-tiering.service"}} {tiering_service_ok}',
    )

    _emit(
        lines, "# HELP healtharchive_systemd_unit_failed 1 if systemd reports the unit is failed."
    )
    _emit(lines, "# TYPE healtharchive_systemd_unit_failed gauge")
    _emit(
        lines,
        f'healtharchive_systemd_unit_failed{{unit="healtharchive-warc-tiering.service"}} {tiering_service_failed}',
    )

    _emit(
        lines,
        "# HELP healtharchive_tiering_manifest_ok 1 if the tiering manifest exists and was parsed successfully.",
    )
    _emit(lines, "# TYPE healtharchive_tiering_manifest_ok gauge")
    _emit(lines, f"healtharchive_tiering_manifest_ok {manifest_ok}")

    _emit(
        lines,
        "# HELP healtharchive_tiering_hot_path_ok 1 if the tiering hot path is readable (from manifest).",
    )
    _emit(lines, "# TYPE healtharchive_tiering_hot_path_ok gauge")
    _emit(
        lines,
        "# HELP healtharchive_tiering_hot_path_errno Errno observed when probing the hot path, or -1 when OK.",
    )
    _emit(lines, "# TYPE healtharchive_tiering_hot_path_errno gauge")

    for hot in hot_paths:
        ok, errno = _probe_readable_dir(hot)
        labels = f'hot="{hot}"'
        _emit(lines, f"healtharchive_tiering_hot_path_ok{{{labels}}} {ok}")
        _emit(lines, f"healtharchive_tiering_hot_path_errno{{{labels}}} {errno}")

    _emit(
        lines,
        "# HELP healtharchive_tiering_metrics_timestamp_seconds UNIX timestamp when these metrics were generated.",
    )
    _emit(lines, "# TYPE healtharchive_tiering_metrics_timestamp_seconds gauge")
    _emit(lines, f"healtharchive_tiering_metrics_timestamp_seconds {_dt_to_epoch_seconds(now)}")

    out_dir.mkdir(parents=True, exist_ok=True)
    tmp = out_file.with_suffix(out_file.suffix + f".{os.getpid()}.tmp")
    tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
    tmp.chmod(0o644)
    tmp.replace(out_file)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
