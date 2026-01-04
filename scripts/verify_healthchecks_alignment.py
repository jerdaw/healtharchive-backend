from __future__ import annotations

import argparse
import re
import subprocess
import sys
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class EnvVar:
    name: str
    is_set: bool


PING_VAR_RE = re.compile(r"--ping-var\s+([A-Z0-9_]+)")
ENV_LINE_RE = re.compile(r"^\s*(?:export\s+)?([A-Z0-9_]+)\s*=\s*(.*)\s*$")

LEGACY_VARS = ("HC_DB_BACKUP_URL", "HC_DISK_URL", "HC_DISK_THRESHOLD")


def _run(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, check=False, text=True, capture_output=True)


def _read_env_vars(env_file: Path) -> dict[str, EnvVar]:
    if not env_file.exists():
        return {}
    env_vars: dict[str, EnvVar] = {}
    for raw in env_file.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        match = ENV_LINE_RE.match(raw)
        if not match:
            continue
        name = match.group(1)
        value_raw = match.group(2).strip()
        if "#" in value_raw:
            value_raw = value_raw.split("#", 1)[0].strip()
        if (value_raw.startswith('"') and value_raw.endswith('"')) or (
            value_raw.startswith("'") and value_raw.endswith("'")
        ):
            value_raw = value_raw[1:-1].strip()
        env_vars[name] = EnvVar(name=name, is_set=bool(value_raw))
    return env_vars


def _list_unit_files() -> list[str]:
    proc = _run(["systemctl", "list-unit-files", "--no-legend", "--no-pager"])
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or "systemctl list-unit-files failed")
    units: list[str] = []
    for line in proc.stdout.splitlines():
        parts = line.split()
        if not parts:
            continue
        units.append(parts[0])
    return units


def _list_timers() -> list[str]:
    proc = _run(["systemctl", "list-timers", "--all", "--no-legend", "--no-pager"])
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or "systemctl list-timers failed")
    return [line.rstrip() for line in proc.stdout.splitlines() if line.strip()]


def _systemctl_cat(unit: str) -> str | None:
    proc = _run(["systemctl", "cat", unit])
    if proc.returncode != 0:
        return None
    return proc.stdout


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Audit Healthchecks alignment: env vars ↔ systemd units ↔ timers."
    )
    parser.add_argument(
        "--env-file",
        default="/etc/healtharchive/healthchecks.env",
        help="Path to root-owned env file holding Healthchecks ping URLs.",
    )
    parser.add_argument(
        "--unit-prefix",
        default="healtharchive-",
        help="Only scan systemd unit names with this prefix.",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Exit non-zero if there are referenced-but-unset vars or set-but-unused vars.",
    )
    args = parser.parse_args()

    env_file = Path(args.env_file)
    try:
        configured = _read_env_vars(env_file)
    except Exception as exc:
        print(f"ERROR: Failed to read env file {env_file}: {exc}", file=sys.stderr)
        return 2

    try:
        all_units = _list_unit_files()
    except Exception as exc:
        print(f"ERROR: systemctl not available or failed: {exc}", file=sys.stderr)
        return 2

    services = sorted(
        u
        for u in all_units
        if u.startswith(args.unit_prefix) and u.endswith(".service") and "@" not in u
    )
    timers = sorted(
        u
        for u in all_units
        if u.startswith(args.unit_prefix) and u.endswith(".timer") and "@" not in u
    )

    referenced_by_var: dict[str, set[str]] = defaultdict(set)
    referenced_legacy: dict[str, set[str]] = defaultdict(set)
    unreadable_units: list[str] = []

    for svc in services:
        text = _systemctl_cat(svc)
        if text is None:
            unreadable_units.append(svc)
            continue
        for var in PING_VAR_RE.findall(text):
            referenced_by_var[var].add(svc)
        for legacy in LEGACY_VARS:
            if legacy in text:
                referenced_legacy[legacy].add(svc)

    referenced_vars = set(referenced_by_var.keys())
    configured_vars = set(configured.keys())

    referenced_but_unset = sorted(
        v for v in referenced_vars if (v not in configured or not configured[v].is_set)
    )
    set_but_unused = sorted(
        v
        for v in configured_vars
        if configured[v].is_set and v not in referenced_vars and v not in LEGACY_VARS
    )

    print("Healthchecks alignment audit")
    print("")
    print(f"- Env file: {env_file} ({'present' if env_file.exists() else 'missing'})")
    print(f"- Scanned services: {len(services)} (prefix={args.unit_prefix!r})")
    print(f"- Known timers (unit files): {len(timers)}")
    print("")

    if configured:
        print("Configured env vars (names only; values not printed):")
        for name in sorted(configured.keys()):
            suffix = "set" if configured[name].is_set else "empty"
            print(f"- {name} ({suffix})")
        print("")
    else:
        print("Configured env vars: none found (file missing or empty)")
        print("")

    if referenced_by_var:
        print("systemd ping vars referenced by units:")
        for var in sorted(referenced_by_var.keys()):
            units = ", ".join(sorted(referenced_by_var[var]))
            status = "set" if configured.get(var, EnvVar(var, False)).is_set else "unset"
            print(f"- {var} ({status}) -> {units}")
        print("")
    else:
        print("systemd ping vars referenced by units: none detected")
        print("")

    if referenced_legacy:
        print("Legacy env vars referenced by units:")
        for var in LEGACY_VARS:
            if var in referenced_legacy:
                units = ", ".join(sorted(referenced_legacy[var]))
                status = "set" if configured.get(var, EnvVar(var, False)).is_set else "unset"
                print(f"- {var} ({status}) -> {units}")
        print("")
    else:
        print("Legacy env vars referenced by units: none detected")
        print("")

    if referenced_but_unset:
        print("Referenced by units but NOT set in env (pings currently disabled):")
        for var in referenced_but_unset:
            units = ", ".join(sorted(referenced_by_var.get(var, set())))
            print(f"- {var} -> {units}")
        print("")

    if set_but_unused:
        print("Set in env but NOT referenced by any scanned unit (likely stale):")
        for var in set_but_unused:
            print(f"- {var}")
        print("")

    if unreadable_units:
        print("Units that could not be read via `systemctl cat` (run with sudo if needed):")
        for unit in unreadable_units:
            print(f"- {unit}")
        print("")

    try:
        timer_lines = _list_timers()
    except Exception as exc:
        print(f"WARNING: could not list timers: {exc}", file=sys.stderr)
        timer_lines = []

    if timer_lines:
        print("systemd timers (for manual cross-check with Healthchecks 'last ping'):")
        for line in timer_lines:
            if args.unit_prefix in line:
                print(f"- {line}")
        print("")

    has_mismatch = bool(referenced_but_unset or set_but_unused)
    if args.strict and has_mismatch:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
