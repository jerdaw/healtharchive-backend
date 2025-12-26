#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

GiB = 1024**3


def _parse_bool(s: str | None) -> bool:
    return str(s or "").strip().lower() in {"1", "true", "yes", "on"}


def _human_bytes(n: int | None) -> str:
    if n is None:
        return "(unknown)"
    units = ["B", "KiB", "MiB", "GiB", "TiB"]
    value = float(n)
    for unit in units:
        if value < 1024.0 or unit == units[-1]:
            if unit == "B":
                return f"{int(value)} {unit}"
            return f"{value:.2f} {unit}"
        value /= 1024.0
    return f"{int(n)} B"


def _parse_meta(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    if not path.is_file():
        return out
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if "=" not in line:
            continue
        k, v = line.split("=", 1)
        out[k.strip()] = v.strip()
    return out


@dataclass(frozen=True)
class Evidence:
    run_dir: Path
    timestamp_utc: str
    apply: bool
    source: str | None
    page_limit: str | None
    depth: str | None
    summary: dict[str, Any] | None


def _parse_timestamp_dirname(name: str) -> datetime | None:
    # Expected: YYYYMMDDThhmmssZ
    if not re.fullmatch(r"\d{8}T\d{6}Z", name):
        return None
    try:
        return datetime.strptime(name, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _find_latest_evidence(out_root: Path) -> Evidence | None:
    if not out_root.is_dir():
        return None

    candidates: list[tuple[datetime, Path]] = []
    for p in out_root.iterdir():
        if not p.is_dir():
            continue
        ts = _parse_timestamp_dirname(p.name)
        if ts is None:
            continue
        candidates.append((ts, p))
    if not candidates:
        return None

    candidates.sort(reverse=True)
    ts, run_dir = candidates[0]
    meta = _parse_meta(run_dir / "00-meta.txt")
    summary_path = run_dir / "98-resource-summary.json"
    summary = None
    if summary_path.is_file():
        try:
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
        except Exception:
            summary = None

    return Evidence(
        run_dir=run_dir,
        timestamp_utc=meta.get("timestamp_utc", ts.strftime("%Y-%m-%dT%H:%M:%SZ")),
        apply=_parse_bool(meta.get("apply")),
        source=meta.get("source"),
        page_limit=meta.get("page_limit"),
        depth=meta.get("depth"),
        summary=summary,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Verify that a recent rehearsal exists and that its recorded resource peaks "
            "suggest sufficient headroom for active crawl workloads."
        )
    )
    parser.add_argument(
        "--out-root",
        default=os.environ.get("HEALTHARCHIVE_REHEARSAL_ROOT", "/srv/healtharchive/ops/rehearsal"),
        help="Rehearsal root directory (default: /srv/healtharchive/ops/rehearsal).",
    )
    parser.add_argument(
        "--max-age-hours",
        type=int,
        default=168,
        help="Fail if the latest rehearsal is older than this (default: 168 = 7 days).",
    )
    parser.add_argument(
        "--require",
        action="store_true",
        default=False,
        help="Fail if rehearsal evidence is missing.",
    )
    parser.add_argument(
        "--min-mem-available-gib",
        type=float,
        default=1.5,
        help="Fail if rehearsal min MemAvailable drops below this (default: 1.5 GiB).",
    )
    parser.add_argument(
        "--max-swap-used-gib",
        type=float,
        default=0.5,
        help="Fail if rehearsal max swap used exceeds this (default: 0.5 GiB).",
    )
    parser.add_argument(
        "--min-samples",
        type=int,
        default=6,
        help="Fail if the resource monitor captured fewer than this many samples (default: 6).",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        default=False,
        help="Emit machine-readable JSON output.",
    )
    args = parser.parse_args(argv)

    out_root = Path(args.out_root).resolve()
    evidence = _find_latest_evidence(out_root)

    now = datetime.now(timezone.utc)
    max_age = timedelta(hours=int(args.max_age_hours))

    findings: list[dict[str, str]] = []

    if evidence is None:
        msg = f"No rehearsal runs found under {out_root}"
        if args.require:
            findings.append({"level": "FAIL", "message": msg})
        else:
            findings.append({"level": "WARN", "message": msg})

        payload = {"outRoot": str(out_root), "evidence": None, "findings": findings}
        if args.json:
            print(json.dumps(payload, indent=2, sort_keys=True))
        else:
            for f in findings:
                print(f"{f['level']}: {f['message']}")
        return 1 if any(f["level"] == "FAIL" for f in findings) else 0

    ts = _parse_timestamp_dirname(evidence.run_dir.name)
    if ts is not None and now - ts > max_age:
        findings.append(
            {
                "level": "FAIL" if args.require else "WARN",
                "message": f"Latest rehearsal is too old: {evidence.run_dir.name} (max_age_hours={args.max_age_hours})",
            }
        )

    if not evidence.apply:
        findings.append(
            {
                "level": "FAIL" if args.require else "WARN",
                "message": "Latest rehearsal is DRY-RUN (apply=false); run with --apply to collect real evidence.",
            }
        )

    if evidence.summary is None:
        findings.append(
            {
                "level": "FAIL" if args.require else "WARN",
                "message": "Latest rehearsal has no resource summary (missing 98-resource-summary.json).",
            }
        )
    else:
        samples = evidence.summary.get("samples")
        if isinstance(samples, int) and samples < int(args.min_samples):
            findings.append(
                {
                    "level": "FAIL" if args.require else "WARN",
                    "message": f"Resource monitor captured too few samples: samples={samples} (min_samples={args.min_samples})",
                }
            )

        min_mem = evidence.summary.get("minMemAvailableBytes")
        max_swap = evidence.summary.get("maxSwapUsedBytes")
        min_mem_b = int(min_mem) if isinstance(min_mem, int) else None
        max_swap_b = int(max_swap) if isinstance(max_swap, int) else None

        if min_mem_b is None:
            findings.append(
                {
                    "level": "FAIL" if args.require else "WARN",
                    "message": "Resource summary missing/invalid minMemAvailableBytes.",
                }
            )
        if max_swap_b is None:
            findings.append(
                {
                    "level": "FAIL" if args.require else "WARN",
                    "message": "Resource summary missing/invalid maxSwapUsedBytes.",
                }
            )

        if min_mem_b is not None and min_mem_b < int(float(args.min_mem_available_gib) * GiB):
            findings.append(
                {
                    "level": "FAIL",
                    "message": f"Rehearsal min MemAvailable too low: {_human_bytes(min_mem_b)}",
                }
            )

        if max_swap_b is not None and max_swap_b > int(float(args.max_swap_used_gib) * GiB):
            findings.append(
                {
                    "level": "FAIL",
                    "message": f"Rehearsal max swap used too high: {_human_bytes(max_swap_b)}",
                }
            )

    payload = {
        "outRoot": str(out_root),
        "evidence": {
            "runDir": str(evidence.run_dir),
            "timestampUtc": evidence.timestamp_utc,
            "apply": evidence.apply,
            "source": evidence.source,
            "pageLimit": evidence.page_limit,
            "depth": evidence.depth,
            "resourceSummary": evidence.summary,
        },
        "findings": findings,
    }

    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print("Rehearsal evidence check")
        print("-----------------------")
        print(f"Run dir: {evidence.run_dir}")
        print(
            f"Mode: {'APPLY' if evidence.apply else 'DRY-RUN'} source={evidence.source} "
            f"page_limit={evidence.page_limit} depth={evidence.depth}"
        )
        if evidence.summary:
            print(
                f"Rehearsal mins/maxes: min_mem={_human_bytes(evidence.summary.get('minMemAvailableBytes'))} "
                f"max_swap={_human_bytes(evidence.summary.get('maxSwapUsedBytes'))}"
            )
        for f in findings:
            print(f"{f['level']}: {f['message']}")

    return 1 if any(f["level"] == "FAIL" for f in findings) else 0


if __name__ == "__main__":
    raise SystemExit(main())
