#!/usr/bin/env python3
from __future__ import annotations

import argparse
import fcntl
import json
import os
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

DEFAULT_DEPLOY_LOCK_FILE = "/tmp/healtharchive-backend-deploy.lock"


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _dt_to_epoch_seconds(dt: datetime) -> int:
    return int(dt.astimezone(timezone.utc).timestamp())


def _file_age_seconds(path: Path, *, now_utc: datetime) -> float | None:
    try:
        st = path.stat()
    except FileNotFoundError:
        return None
    except OSError:
        return 0.0
    try:
        mtime = datetime.fromtimestamp(st.st_mtime, tz=timezone.utc)
    except (OverflowError, OSError, ValueError):
        return 0.0
    return max(0.0, (now_utc - mtime).total_seconds())


def _deploy_lock_is_active(
    deploy_lock_file: Path,
    *,
    now_utc: datetime,
    deploy_lock_max_age_seconds: float,
) -> tuple[int, float | None]:
    age_seconds = _file_age_seconds(deploy_lock_file, now_utc=now_utc)
    if age_seconds is None:
        return 0, None

    try:
        f = deploy_lock_file.open("rb")
    except OSError:
        return (
            1 if age_seconds <= float(deploy_lock_max_age_seconds) else 0,
            age_seconds,
        )
    try:
        try:
            fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return 1, age_seconds
        except OSError:
            return (
                1 if age_seconds <= float(deploy_lock_max_age_seconds) else 0,
                age_seconds,
            )
        else:
            try:
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)
            except OSError:
                pass
            return 0, age_seconds
    finally:
        try:
            f.close()
        except Exception:
            pass


def _save_state_file(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(json.dumps(data, indent=2, sort_keys=True) + "\n")
        f.flush()
        os.fsync(f.fileno())
    tmp.replace(path)


def _write_textfile_metrics(
    *,
    out_dir: Path,
    out_file: str,
    now_utc: datetime,
    enabled: int,
    deploy_lock_present: int,
    result: str,
    reason: str,
) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / out_file
    tmp = path.with_suffix(path.suffix + f".{os.getpid()}.tmp")

    def emit(line: str) -> None:
        lines.append(line.rstrip("\n"))

    lines: list[str] = []
    emit(
        "# HELP healtharchive_drift_auto_reconcile_metrics_ok 1 if the drift auto reconcile watchdog ran to completion."
    )
    emit("# TYPE healtharchive_drift_auto_reconcile_metrics_ok gauge")
    emit("healtharchive_drift_auto_reconcile_metrics_ok 1")

    emit(
        "# HELP healtharchive_drift_auto_reconcile_last_run_timestamp_seconds UNIX timestamp of the last watchdog run."
    )
    emit("# TYPE healtharchive_drift_auto_reconcile_last_run_timestamp_seconds gauge")
    emit(
        f"healtharchive_drift_auto_reconcile_last_run_timestamp_seconds {_dt_to_epoch_seconds(now_utc)}"
    )

    emit(
        "# HELP healtharchive_drift_auto_reconcile_enabled 1 if the sentinel file exists (automation enabled)."
    )
    emit("# TYPE healtharchive_drift_auto_reconcile_enabled gauge")
    emit(f"healtharchive_drift_auto_reconcile_enabled {int(enabled)}")

    emit(
        "# HELP healtharchive_drift_auto_reconcile_deploy_lock_present 1 if deploy lock appears active (held by another process)."
    )
    emit("# TYPE healtharchive_drift_auto_reconcile_deploy_lock_present gauge")
    emit(f"healtharchive_drift_auto_reconcile_deploy_lock_present {int(deploy_lock_present)}")

    emit(
        "# HELP healtharchive_drift_auto_reconcile_last_result 1 for the most recent watchdog outcome (labels: result, reason)."
    )
    emit("# TYPE healtharchive_drift_auto_reconcile_last_result gauge")
    emit(f'healtharchive_drift_auto_reconcile_last_result{{result="{result}",reason="{reason}"}} 1')

    tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
    tmp.chmod(0o644)
    tmp.replace(path)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description=(
            "HealthArchive VPS helper: watchdog that runs vps-deploy.sh if the latest baseline script detects missing dependencies."
        )
    )
    p.add_argument(
        "--apply",
        action="store_true",
        default=False,
        help="Actually run vps-deploy.sh if conditions are met (default: dry-run).",
    )
    p.add_argument(
        "--sentinel-file",
        default="/etc/healtharchive/drift-auto-reconcile-enabled",
        help="Sentinel file that indicates automation is enabled (written by operator).",
    )
    p.add_argument(
        "--drift-report",
        default="/srv/healtharchive/ops/baseline/drift-report-latest.txt",
        help="Path to the latest baseline drift report.",
    )
    p.add_argument(
        "--state-file",
        default="/srv/healtharchive/ops/watchdog/drift-auto-reconcile.json",
        help="Where to store watchdog state/history.",
    )
    p.add_argument(
        "--lock-file",
        default="/srv/healtharchive/ops/watchdog/drift-auto-reconcile.lock",
        help="Lock file to prevent concurrent runs.",
    )
    p.add_argument(
        "--deploy-script",
        default="/opt/healtharchive-backend/scripts/vps-deploy.sh",
        help="Path to the deployment script.",
    )
    p.add_argument(
        "--deploy-lock-file",
        default=DEFAULT_DEPLOY_LOCK_FILE,
        help="If this file exists (and is not stale), skip the run to avoid flapping during deploys.",
    )
    p.add_argument(
        "--deploy-lock-max-age-seconds",
        type=float,
        default=2 * 60 * 60,
        help="Treat --deploy-lock-file as stale if older than this; proceed if stale.",
    )
    p.add_argument(
        "--cooldown-minutes",
        type=float,
        default=15.0,
        help="Do not trigger auto-recovery again if a previous run happened within this window.",
    )
    p.add_argument(
        "--textfile-out-dir",
        default="/var/lib/node_exporter/textfile_collector",
        help="node_exporter textfile collector directory.",
    )
    p.add_argument(
        "--textfile-out-file",
        default="healtharchive_drift_auto_reconcile.prom",
        help="Output filename under --textfile-out-dir.",
    )
    args = p.parse_args(argv)

    now = _utc_now()
    sentinel_file = Path(args.sentinel_file)
    enabled = 1 if sentinel_file.is_file() else 0

    if enabled != 1:
        try:
            _write_textfile_metrics(
                out_dir=Path(str(args.textfile_out_dir)),
                out_file=str(args.textfile_out_file),
                now_utc=now,
                enabled=enabled,
                deploy_lock_present=0,
                result="skip",
                reason="disabled",
            )
        except Exception:
            pass
        return 0

    lock_path = Path(args.lock_file)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_f = lock_path.open("a", encoding="utf-8")
    try:
        fcntl.flock(lock_f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return 0

    state_path = Path(args.state_file)
    state_path.parent.mkdir(parents=True, exist_ok=True)

    result = "skip"
    reason = "no_action"

    deploy_lock_file = Path(str(args.deploy_lock_file))
    deploy_lock_present, _ = _deploy_lock_is_active(
        deploy_lock_file,
        now_utc=now,
        deploy_lock_max_age_seconds=float(args.deploy_lock_max_age_seconds),
    )
    if deploy_lock_present == 1:
        reason = "deploy_lock_held"
        try:
            _write_textfile_metrics(
                out_dir=Path(str(args.textfile_out_dir)),
                out_file=str(args.textfile_out_file),
                now_utc=now,
                enabled=enabled,
                deploy_lock_present=deploy_lock_present,
                result="skip",
                reason=reason,
            )
        except Exception:
            pass
        return 0

    # Read state file to enforce cooldown
    if state_path.exists():
        try:
            state_data = json.loads(state_path.read_text(encoding="utf-8"))
            last_run_utc = datetime.fromisoformat(
                state_data.get("last_run_utc", "2000-01-01T00:00:00+00:00")
            )
            if state_data.get("result") == "fail" or state_data.get("result") == "ok":
                # Ensure we only check cooldowns for actual recovery attempts (fail/ok)
                if now - last_run_utc < timedelta(minutes=args.cooldown_minutes):
                    reason = "cooldown"
                    try:
                        _write_textfile_metrics(
                            out_dir=Path(str(args.textfile_out_dir)),
                            out_file=str(args.textfile_out_file),
                            now_utc=now,
                            enabled=enabled,
                            deploy_lock_present=deploy_lock_present,
                            result="skip",
                            reason=reason,
                        )
                    except Exception:
                        pass
                    return 0
        except Exception:
            pass

    drift_report = Path(args.drift_report)
    need_recovery = False

    if drift_report.exists():
        report_text = drift_report.read_text(encoding="utf-8")
        if "FAILURES (must fix)" in report_text and "dependencies:" in report_text:
            need_recovery = True

    if not need_recovery:
        result = "skip"
        reason = "no_dependency_drift"
    else:
        if not args.apply:
            result = "skip"
            reason = "dry_run_would_reconcile"
        else:
            reason = "reconcile_triggered"
            deploy_script = str(args.deploy_script)
            cp = subprocess.run(
                [deploy_script, "--apply", "--skip-worker-restart"],
                check=False,
                capture_output=True,
                text=True,
            )
            if cp.returncode == 0:
                result = "ok"
                reason = "reconciled_successfully"
            else:
                result = "fail"
                reason = "reconcile_script_failed"

            _save_state_file(
                state_path,
                {
                    "last_run_utc": now.replace(microsecond=0).isoformat(),
                    "result": result,
                    "reason": reason,
                    "rc": int(cp.returncode),
                    "stdout": (cp.stdout or "")[:1000],
                    "stderr": (cp.stderr or "")[:1000],
                },
            )

    if result == "skip" and reason != "dry_run_would_reconcile":
        try:
            _save_state_file(
                state_path,
                {
                    "last_run_utc": now.replace(microsecond=0).isoformat(),
                    "result": result,
                    "reason": reason,
                },
            )
        except Exception:
            pass

    try:
        _write_textfile_metrics(
            out_dir=Path(str(args.textfile_out_dir)),
            out_file=str(args.textfile_out_file),
            now_utc=now,
            enabled=enabled,
            deploy_lock_present=deploy_lock_present,
            result=result,
            reason=reason,
        )
    except Exception:
        pass

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
