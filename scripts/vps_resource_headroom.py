#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

GiB = 1024**3
MiB = 1024**2


def _human_bytes(n: int) -> str:
    if n < 0:
        return f"{n} B"
    units = ["B", "KiB", "MiB", "GiB", "TiB"]
    value = float(n)
    for unit in units:
        if value < 1024.0 or unit == units[-1]:
            if unit == "B":
                return f"{int(value)} {unit}"
            return f"{value:.2f} {unit}"
        value /= 1024.0
    return f"{int(n)} B"


def _read_kv_kib(path: Path) -> dict[str, int]:
    out: dict[str, int] = {}
    try:
        txt = path.read_text(encoding="utf-8")
    except Exception:
        return out
    for line in txt.splitlines():
        if ":" not in line:
            continue
        key, rest = line.split(":", 1)
        parts = rest.strip().split()
        if not parts:
            continue
        try:
            out[key.strip()] = int(parts[0])
        except ValueError:
            continue
    return out


def _read_loadavg() -> tuple[float, float, float] | None:
    try:
        txt = Path("/proc/loadavg").read_text(encoding="utf-8").strip()
    except Exception:
        return None
    parts = txt.split()
    if len(parts) < 3:
        return None
    try:
        return float(parts[0]), float(parts[1]), float(parts[2])
    except ValueError:
        return None


def _read_psi(kind: str) -> dict[str, dict[str, float]] | None:
    path = Path(f"/proc/pressure/{kind}")
    if not path.exists():
        return None
    try:
        txt = path.read_text(encoding="utf-8").strip()
    except Exception:
        return None

    result: dict[str, dict[str, float]] = {}
    for line in txt.splitlines():
        parts = line.strip().split()
        if not parts:
            continue
        bucket = parts[0]
        data: dict[str, float] = {}
        for token in parts[1:]:
            if "=" not in token:
                continue
            k, v = token.split("=", 1)
            if k in {"avg10", "avg60", "avg300"}:
                try:
                    data[k] = float(v)
                except ValueError:
                    continue
        if data:
            result[bucket] = data
    return result or None


@dataclass(frozen=True)
class Thresholds:
    mem_warn_b: int
    mem_fail_b: int
    swap_warn_b: int
    swap_fail_b: int
    load_warn: float
    load_fail: float
    cpu_psi_some_warn: float
    cpu_psi_some_fail: float
    mem_psi_some_warn: float
    mem_psi_some_fail: float
    mem_psi_full_warn: float
    mem_psi_full_fail: float
    io_psi_some_warn: float
    io_psi_some_fail: float


def _default_thresholds(*, mem_total_b: int, cpu_count: int, swap_total_b: int) -> Thresholds:
    mem_warn_b = max(int(0.30 * mem_total_b), int(2.5 * GiB))
    mem_fail_b = max(int(0.20 * mem_total_b), int(1.5 * GiB))

    swap_used_warn_b = 1  # warn if swap used at all
    swap_used_fail_b = 0
    if swap_total_b > 0:
        swap_used_fail_b = max(int(0.10 * swap_total_b), int(512 * MiB))

    load_warn = float(cpu_count) * 0.80
    load_fail = float(cpu_count) * 1.20

    return Thresholds(
        mem_warn_b=mem_warn_b,
        mem_fail_b=mem_fail_b,
        swap_warn_b=swap_used_warn_b,
        swap_fail_b=swap_used_fail_b,
        load_warn=load_warn,
        load_fail=load_fail,
        cpu_psi_some_warn=0.20,
        cpu_psi_some_fail=0.40,
        mem_psi_some_warn=0.10,
        mem_psi_some_fail=0.20,
        mem_psi_full_warn=0.02,
        mem_psi_full_fail=0.05,
        io_psi_some_warn=0.20,
        io_psi_some_fail=0.40,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "HealthArchive VPS helper: check CPU/RAM headroom before crawl workloads "
            "(fails if the host is already under sustained memory pressure or high load)."
        )
    )
    parser.add_argument(
        "--json", action="store_true", default=False, help="Emit machine-readable JSON output."
    )
    args = parser.parse_args(argv)

    meminfo = _read_kv_kib(Path("/proc/meminfo"))
    mem_total_b = int(meminfo.get("MemTotal", 0)) * 1024
    mem_avail_b = int(meminfo.get("MemAvailable", 0)) * 1024
    swap_total_b = int(meminfo.get("SwapTotal", 0)) * 1024
    swap_free_b = int(meminfo.get("SwapFree", 0)) * 1024
    swap_used_b = max(0, swap_total_b - swap_free_b)

    cpu_count = int(os.cpu_count() or 0) or 1
    load = _read_loadavg()
    psi_cpu = _read_psi("cpu")
    psi_mem = _read_psi("memory")
    psi_io = _read_psi("io")

    thresholds = _default_thresholds(
        mem_total_b=mem_total_b, cpu_count=cpu_count, swap_total_b=swap_total_b
    )

    findings: list[dict[str, Any]] = []

    def warn(key: str, msg: str) -> None:
        findings.append({"level": "WARN", "key": key, "message": msg})

    def fail(key: str, msg: str) -> None:
        findings.append({"level": "FAIL", "key": key, "message": msg})

    if mem_total_b <= 0:
        warn("meminfo", "Could not read MemTotal from /proc/meminfo; skipping memory thresholds.")
    else:
        if mem_avail_b <= 0:
            warn(
                "meminfo",
                "Could not read MemAvailable from /proc/meminfo; skipping memory thresholds.",
            )
        elif mem_avail_b < thresholds.mem_fail_b:
            fail(
                "mem_available",
                f"MemAvailable is low ({_human_bytes(mem_avail_b)}); "
                f"need at least {_human_bytes(thresholds.mem_fail_b)} for crawl safety.",
            )
        elif mem_avail_b < thresholds.mem_warn_b:
            warn(
                "mem_available",
                f"MemAvailable is getting low ({_human_bytes(mem_avail_b)}); "
                f"target at least {_human_bytes(thresholds.mem_warn_b)} before starting crawls.",
            )

    if swap_total_b > 0:
        if swap_used_b >= thresholds.swap_fail_b and thresholds.swap_fail_b > 0:
            fail(
                "swap_used",
                f"Swap is in use ({_human_bytes(swap_used_b)}); "
                f"expected near-zero swap before crawl. Threshold {_human_bytes(thresholds.swap_fail_b)}.",
            )
        elif swap_used_b >= thresholds.swap_warn_b:
            warn(
                "swap_used",
                f"Swap is in use ({_human_bytes(swap_used_b)}); prefer 0 swap used before crawl.",
            )

    if load is None:
        warn("loadavg", "Could not read /proc/loadavg; skipping load thresholds.")
    else:
        _l1, _l5, l15 = load
        if l15 >= thresholds.load_fail:
            fail(
                "loadavg_15m",
                f"15m loadavg is high ({l15:.2f} on {cpu_count} CPU); expected < {thresholds.load_fail:.2f}.",
            )
        elif l15 >= thresholds.load_warn:
            warn(
                "loadavg_15m",
                f"15m loadavg is elevated ({l15:.2f} on {cpu_count} CPU); target < {thresholds.load_warn:.2f}.",
            )

    def psi_check(
        psi: dict[str, dict[str, float]] | None,
        *,
        kind: str,
        bucket: str,
        warn_th: float,
        fail_th: float,
    ) -> None:
        if psi is None:
            return
        v = psi.get(bucket, {}).get("avg10")
        if v is None:
            return
        key = f"psi_{kind}_{bucket}_avg10"
        if v >= fail_th:
            fail(key, f"{kind} PSI {bucket} avg10 is high ({v:.2f}); expected < {fail_th:.2f}.")
        elif v >= warn_th:
            warn(key, f"{kind} PSI {bucket} avg10 is elevated ({v:.2f}); target < {warn_th:.2f}.")

    psi_check(
        psi_cpu,
        kind="cpu",
        bucket="some",
        warn_th=thresholds.cpu_psi_some_warn,
        fail_th=thresholds.cpu_psi_some_fail,
    )
    psi_check(
        psi_mem,
        kind="memory",
        bucket="some",
        warn_th=thresholds.mem_psi_some_warn,
        fail_th=thresholds.mem_psi_some_fail,
    )
    psi_check(
        psi_mem,
        kind="memory",
        bucket="full",
        warn_th=thresholds.mem_psi_full_warn,
        fail_th=thresholds.mem_psi_full_fail,
    )
    psi_check(
        psi_io,
        kind="io",
        bucket="some",
        warn_th=thresholds.io_psi_some_warn,
        fail_th=thresholds.io_psi_some_fail,
    )

    result = {
        "cpuCount": cpu_count,
        "loadavg": {"1m": load[0], "5m": load[1], "15m": load[2]} if load else None,
        "memory": {
            "memTotalBytes": mem_total_b,
            "memAvailableBytes": mem_avail_b,
            "swapTotalBytes": swap_total_b,
            "swapUsedBytes": swap_used_b,
        },
        "psi": {"cpu": psi_cpu, "memory": psi_mem, "io": psi_io},
        "thresholds": {
            "memWarnBytes": thresholds.mem_warn_b,
            "memFailBytes": thresholds.mem_fail_b,
            "swapWarnBytes": thresholds.swap_warn_b,
            "swapFailBytes": thresholds.swap_fail_b,
            "loadWarn": thresholds.load_warn,
            "loadFail": thresholds.load_fail,
            "cpuPsiSomeWarn": thresholds.cpu_psi_some_warn,
            "cpuPsiSomeFail": thresholds.cpu_psi_some_fail,
            "memPsiSomeWarn": thresholds.mem_psi_some_warn,
            "memPsiSomeFail": thresholds.mem_psi_some_fail,
            "memPsiFullWarn": thresholds.mem_psi_full_warn,
            "memPsiFullFail": thresholds.mem_psi_full_fail,
            "ioPsiSomeWarn": thresholds.io_psi_some_warn,
            "ioPsiSomeFail": thresholds.io_psi_some_fail,
        },
        "findings": findings,
    }

    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0 if not any(f["level"] == "FAIL" for f in findings) else 1

    print("CPU/RAM headroom preflight")
    print("--------------------------")
    if load is not None:
        print(
            f"CPU: {cpu_count} core(s), loadavg 1/5/15m = {load[0]:.2f} {load[1]:.2f} {load[2]:.2f}"
        )
    else:
        print(f"CPU: {cpu_count} core(s), loadavg = (unavailable)")
    print(
        f"RAM: MemAvailable={_human_bytes(mem_avail_b)} / MemTotal={_human_bytes(mem_total_b)} "
        f"(swap used={_human_bytes(swap_used_b)} / swap total={_human_bytes(swap_total_b)})"
    )
    if psi_cpu or psi_mem or psi_io:
        print("PSI: available (cpu/memory/io)")
    else:
        print("PSI: not available (kernel may not expose /proc/pressure/*)")

    for f in findings:
        print(f"{f['level']}: {f['key']}: {f['message']}")

    if any(f["level"] == "FAIL" for f in findings):
        print("")
        print("FAIL: host resource headroom is not sufficient for safe crawl workloads right now.")
        return 1

    print("")
    print("OK: CPU/RAM headroom looks sufficient for crawl workloads.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
