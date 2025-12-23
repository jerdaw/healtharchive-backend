#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import baseline_snapshot


@dataclass(frozen=True)
class Finding:
    level: str  # "FAIL" | "WARN"
    key: str
    message: str


def _parse_max_age_seconds(hsts_value: str | None) -> int | None:
    if not hsts_value:
        return None
    m = re.search(r"max-age\s*=\s*(\d+)", hsts_value, flags=re.IGNORECASE)
    if not m:
        return None
    try:
        return int(m.group(1))
    except ValueError:
        return None


def _expect_equal(
    findings: list[Finding], *, level: str, key: str, expected: str, actual: Any
) -> None:
    if actual != expected:
        findings.append(
            Finding(level=level, key=key, message=f"expected {expected!r} got {actual!r}")
        )


def _expect_true(
    findings: list[Finding], *, level: str, key: str, actual: Any, message: str
) -> None:
    if actual is not True:
        findings.append(Finding(level=level, key=key, message=message))


def _expect_contains_all(
    findings: list[Finding],
    *,
    level: str,
    key: str,
    expected_items: Iterable[str],
    actual_value: str | None,
) -> None:
    if actual_value is None:
        findings.append(Finding(level=level, key=key, message="value missing"))
        return
    actual_items = {item.strip() for item in actual_value.split(",") if item.strip()}
    missing = [item for item in expected_items if item not in actual_items]
    if missing:
        findings.append(
            Finding(level=level, key=key, message=f"missing required origins: {missing!r}")
        )


def _parse_csv_set(value: str | None) -> set[str] | None:
    if value is None:
        return None
    return {item.strip() for item in value.split(",") if item.strip()}


def _expect_csv_set_equal(
    findings: list[Finding],
    *,
    level: str,
    key: str,
    expected_items: Iterable[str],
    actual_value: str | None,
) -> None:
    actual_items = _parse_csv_set(actual_value)
    if actual_items is None:
        findings.append(Finding(level=level, key=key, message="value missing"))
        return

    expected_set = {str(x) for x in expected_items if str(x).strip()}
    if "*" in actual_items:
        findings.append(
            Finding(
                level=level, key=key, message="wildcard origin '*' is not allowed in production"
            )
        )
        return

    missing = sorted(expected_set - actual_items)
    extra = sorted(actual_items - expected_set)
    if missing or extra:
        parts: list[str] = []
        if missing:
            parts.append(f"missing={missing!r}")
        if extra:
            parts.append(f"extra={extra!r}")
        findings.append(Finding(level=level, key=key, message="; ".join(parts)))


def _mode_from_policy_mode(policy_mode: str | None) -> str | None:
    if not policy_mode:
        return None
    mode_str = str(policy_mode).strip()
    if not mode_str:
        return None
    return mode_str


def _expect_path(
    findings: list[Finding],
    *,
    level: str,
    path: str,
    policy_entry: dict[str, Any],
    observed_entry: dict[str, Any] | None,
) -> None:
    key_prefix = f"filesystem:{path}"
    required = bool(policy_entry.get("required", False))
    if observed_entry is None:
        if required:
            findings.append(
                Finding(level=level, key=key_prefix, message="path missing from snapshot")
            )
        return

    exists = bool(observed_entry.get("exists"))
    if required and not exists:
        findings.append(Finding(level=level, key=key_prefix, message="path does not exist"))
        return

    if not exists:
        return

    for k in ("owner", "group"):
        expected = policy_entry.get(k)
        if expected:
            actual = observed_entry.get(k)
            if actual != expected:
                findings.append(
                    Finding(
                        level=level,
                        key=f"{key_prefix}:{k}",
                        message=f"expected {expected!r} got {actual!r}",
                    )
                )

    expected_mode = _mode_from_policy_mode(policy_entry.get("mode"))
    if expected_mode:
        actual_mode = observed_entry.get("mode")
        if actual_mode != expected_mode:
            findings.append(
                Finding(
                    level=level,
                    key=f"{key_prefix}:mode",
                    message=f"expected {expected_mode!r} got {actual_mode!r}",
                )
            )

        # If the policy mode encodes setgid (2xxx), ensure the directory actually has it.
        try:
            if len(expected_mode) == 4 and expected_mode[0] == "2":
                if observed_entry.get("is_dir") and not observed_entry.get("setgid"):
                    findings.append(
                        Finding(
                            level=level,
                            key=f"{key_prefix}:setgid",
                            message="expected setgid bit to be set on directory",
                        )
                    )
        except Exception:  # noqa: BLE001
            pass

    if policy_entry.get("must_be_writable") is True and observed_entry.get("writable") is not True:
        findings.append(
            Finding(level=level, key=f"{key_prefix}:writable", message="expected writable=true")
        )


def evaluate(
    policy: dict[str, Any], observed: dict[str, Any]
) -> tuple[list[Finding], list[Finding]]:
    required: list[Finding] = []
    warned: list[Finding] = []

    def fail(key: str, message: str) -> None:
        required.append(Finding(level="FAIL", key=key, message=message))

    def warn(key: str, message: str) -> None:
        warned.append(Finding(level="WARN", key=key, message=message))

    # Env file parse
    if observed.get("env_read_error"):
        fail("env_read_error", f"could not read env file: {observed.get('env_read_error')!r}")

    env = observed.get("env", {})
    if not isinstance(env, dict):
        fail("env", "env snapshot missing or invalid")
        env = {}

    # Operator membership
    operator = observed.get("operator", {})
    if isinstance(operator, dict):
        if operator.get("operator_in_ops_group") is not True:
            fail(
                "operator_in_ops_group",
                "operator user is not in the expected ops group; add membership and re-login",
            )

    # Required env equality checks
    required_env = policy.get("backend_env_required", {})
    if isinstance(required_env, dict):
        for k, v in required_env.items():
            if not isinstance(v, str):
                fail(f"policy:backend_env_required:{k}", "expected a string value in policy")
                continue
            _expect_equal(required, level="FAIL", key=f"env:{k}", expected=v, actual=env.get(k))

    # Required CORS origins
    exact_csv_env = policy.get("backend_env_csv_set_equals", {})
    exact_required = (
        exact_csv_env.get("HEALTHARCHIVE_CORS_ORIGINS") if isinstance(exact_csv_env, dict) else None
    )
    if isinstance(exact_required, list):
        _expect_csv_set_equal(
            required,
            level="FAIL",
            key="env:HEALTHARCHIVE_CORS_ORIGINS",
            expected_items=[str(x) for x in exact_required],
            actual_value=env.get("HEALTHARCHIVE_CORS_ORIGINS"),
        )
    else:
        contains_env = policy.get("backend_env_contains", {})
        cors_required = (
            contains_env.get("HEALTHARCHIVE_CORS_ORIGINS")
            if isinstance(contains_env, dict)
            else None
        )
        if isinstance(cors_required, list):
            _expect_contains_all(
                required,
                level="FAIL",
                key="env:HEALTHARCHIVE_CORS_ORIGINS",
                expected_items=[str(x) for x in cors_required],
                actual_value=env.get("HEALTHARCHIVE_CORS_ORIGINS"),
            )

    # Admin token configured + endpoints protected (live checks only)
    security = policy.get("security", {})
    if isinstance(security, dict) and security.get("admin_token_required") is True:
        _expect_true(
            required,
            level="FAIL",
            key="env:HEALTHARCHIVE_ADMIN_TOKEN_present",
            actual=env.get("HEALTHARCHIVE_ADMIN_TOKEN_present"),
            message="admin token is not configured in env file (required in production)",
        )

    if observed.get("inputs", {}).get("mode") == "live":
        admin_checks = observed.get("http", {}).get("admin_checks", {})
        if isinstance(admin_checks, dict):
            if admin_checks.get("metrics_without_token_status") != 403:
                fail(
                    "http:/metrics:no-token",
                    f"expected 403 got {admin_checks.get('metrics_without_token_status')!r}",
                )
            if admin_checks.get("admin_without_token_status") != 403:
                fail(
                    "http:/api/admin/jobs:no-token",
                    f"expected 403 got {admin_checks.get('admin_without_token_status')!r}",
                )
            if env.get("HEALTHARCHIVE_ADMIN_TOKEN_present") is True:
                if admin_checks.get("metrics_with_token_status") != 200:
                    fail(
                        "http:/metrics:with-token",
                        f"expected 200 got {admin_checks.get('metrics_with_token_status')!r}",
                    )
                if admin_checks.get("admin_with_token_status") != 200:
                    fail(
                        "http:/api/admin/jobs:with-token",
                        f"expected 200 got {admin_checks.get('admin_with_token_status')!r}",
                    )
        else:
            warn("http:admin_checks", "missing live admin endpoint checks")
    else:
        warn("http:admin_checks", "skipped (mode=local)")

    # Live CORS checks (optional but recommended)
    if observed.get("inputs", {}).get("mode") == "live":
        cors_checks = observed.get("http", {}).get("cors_checks", {})
        if isinstance(cors_checks, dict):
            allowed = cors_checks.get("allowed", [])
            disallowed = cors_checks.get("disallowed", [])
            if isinstance(allowed, list):
                for row in allowed:
                    if not isinstance(row, dict):
                        continue
                    origin = row.get("origin")
                    allow_origin = row.get("allow_origin")
                    status = row.get("status")
                    if status != 200:
                        fail(f"cors:allowed:{origin}", f"expected 200 got {status!r}")
                        continue
                    if allow_origin != origin:
                        fail(
                            f"cors:allowed:{origin}",
                            f"expected Access-Control-Allow-Origin={origin!r} got {allow_origin!r}",
                        )
            if isinstance(disallowed, list):
                for row in disallowed:
                    if not isinstance(row, dict):
                        continue
                    origin = row.get("origin")
                    allow_origin = row.get("allow_origin")
                    status = row.get("status")
                    if status != 200:
                        warn(
                            f"cors:disallowed:{origin}",
                            f"expected 200 got {status!r} (check reachability)",
                        )
                        continue
                    if allow_origin:
                        fail(
                            f"cors:disallowed:{origin}",
                            f"expected no Access-Control-Allow-Origin, got {allow_origin!r}",
                        )
        else:
            warn("cors_checks", "missing cors_checks in snapshot")
    else:
        warn("cors_checks", "skipped (mode=local)")

    # HSTS: local requires it be configured in Caddy; live additionally requires it be observed.
    hsts_policy = policy.get("security", {})
    if isinstance(hsts_policy, dict) and hsts_policy.get("hsts_required") is True:
        hsts = observed.get("hsts", {})
        if not isinstance(hsts, dict):
            fail("hsts", "missing hsts snapshot")
        else:
            if hsts.get("configured_in_caddy") is not True:
                fail(
                    "hsts:configured_in_caddy",
                    "HSTS not configured in Caddyfile for API site block",
                )

            min_age = hsts_policy.get("hsts_header_min_max_age_seconds")
            if isinstance(min_age, int):
                cfg_age = _parse_max_age_seconds(hsts.get("configured_value"))
                if cfg_age is None or cfg_age < min_age:
                    fail(
                        "hsts:configured_value",
                        f"expected max-age >= {min_age} in Caddy config, got {hsts.get('configured_value')!r}",
                    )

            if observed.get("inputs", {}).get("mode") == "live":
                if hsts.get("observed_on_health") is not True:
                    fail("hsts:observed_on_health", "HSTS header not observed on /api/health")
                else:
                    obs_age = _parse_max_age_seconds(hsts.get("observed_value"))
                    if isinstance(min_age, int) and (obs_age is None or obs_age < min_age):
                        fail(
                            "hsts:observed_value",
                            f"expected max-age >= {min_age} in live header, got {hsts.get('observed_value')!r}",
                        )
            else:
                warn("hsts:observed_on_health", "skipped (mode=local)")

    # Filesystem paths
    fs_policy_paths = policy.get("filesystem", {}).get("paths", [])
    fs_obs_paths = observed.get("filesystem", {}).get("paths", {})
    if isinstance(fs_policy_paths, list) and isinstance(fs_obs_paths, dict):
        for entry in fs_policy_paths:
            path = entry.get("path")
            if not isinstance(path, str) or not path:
                continue
            _expect_path(
                required,
                level="FAIL",
                path=path,
                policy_entry=entry,
                observed_entry=fs_obs_paths.get(path),
            )

    # systemd units
    units_policy = policy.get("systemd", {}).get("units", [])
    units_obs = observed.get("systemd", {}).get("units", {})
    if isinstance(units_policy, list) and isinstance(units_obs, dict):
        for entry in units_policy:
            name = entry.get("name")
            if not isinstance(name, str) or not name:
                continue
            status = units_obs.get(name)
            if entry.get("required") is True and not status:
                fail(f"systemd:{name}", "missing unit status")
                continue
            if not isinstance(status, dict):
                continue
            if entry.get("must_be_enabled") is True:
                enabled = str(status.get("enabled") or "")
                if not enabled.startswith("enabled"):
                    fail(f"systemd:{name}:enabled", f"expected enabled, got {enabled!r}")
            if entry.get("must_be_active") is True:
                active = str(status.get("active") or "")
                if active != "active":
                    fail(f"systemd:{name}:active", f"expected active, got {active!r}")
            else:
                active = str(status.get("active") or "")
                if active != "active":
                    warn(f"systemd:{name}:active", f"not active ({active!r})")

    return required, warned


def _write_outputs(
    out_dir: Path, *, observed: dict[str, Any], report_text: str
) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    observed_path = out_dir / f"observed-{ts}.json"
    observed_path.write_text(
        json.dumps(observed, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    report_path = out_dir / f"drift-report-{ts}.txt"
    report_path.write_text(report_text, encoding="utf-8")

    (out_dir / "observed-latest.json").write_text(
        observed_path.read_text(encoding="utf-8"), encoding="utf-8"
    )
    (out_dir / "drift-report-latest.txt").write_text(report_text, encoding="utf-8")

    return observed_path, report_path


def _format_report(
    required: list[Finding], warned: list[Finding], *, policy_path: Path, mode: str
) -> str:
    lines: list[str] = []
    lines.append("HealthArchive baseline drift report")
    lines.append(f"timestamp_utc={datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}")
    lines.append(f"policy={policy_path}")
    lines.append(f"mode={mode}")
    lines.append("")

    if not required and not warned:
        lines.append("PASS: No drift detected.")
        return "\n".join(lines) + "\n"

    if required:
        lines.append("FAILURES (must fix)")
        for f in required:
            lines.append(f"- {f.key}: {f.message}")
        lines.append("")

    if warned:
        lines.append("WARNINGS (recommended)")
        for f in warned:
            lines.append(f"- {f.key}: {f.message}")
        lines.append("")

    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Compare production baseline policy against observed VPS state."
    )
    parser.add_argument("--policy", type=Path, default=baseline_snapshot._default_policy_path())
    parser.add_argument(
        "--mode",
        choices=["local", "live"],
        default="local",
        help="local: file/system checks only; live: also call HTTPS endpoints.",
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=Path("/srv/healtharchive/ops/baseline"),
        help="Where to write observed snapshots and drift reports (VPS).",
    )
    parser.add_argument(
        "--no-write",
        action="store_true",
        help="Do not write any files; print report only (useful for quick checks).",
    )
    parser.add_argument(
        "--json", action="store_true", help="Also print observed snapshot JSON to stdout."
    )
    args = parser.parse_args()

    policy = baseline_snapshot.load_policy(args.policy)
    observed = baseline_snapshot.collect_observed(policy=policy, mode=args.mode)

    required, warned = evaluate(policy, observed)
    report = _format_report(required, warned, policy_path=args.policy, mode=args.mode)
    sys.stdout.write(report)

    if args.json:
        sys.stdout.write(json.dumps(observed, indent=2, sort_keys=True) + "\n")

    if not args.no_write:
        # Fail early with a clear message if the directory isn't writable; we want ops artifacts
        # to be durable and easy to diff over time.
        if not args.out_dir.exists():
            raise SystemExit(
                f"ERROR: out dir does not exist: {args.out_dir} (create it per production runbook)"
            )
        if not os.access(args.out_dir, os.W_OK):
            raise SystemExit(f"ERROR: out dir not writable: {args.out_dir}")
        observed_path, report_path = _write_outputs(
            args.out_dir, observed=observed, report_text=report
        )
        sys.stderr.write(f"Wrote: {observed_path}\nWrote: {report_path}\n")

    return 1 if required else 0


if __name__ == "__main__":
    raise SystemExit(main())
