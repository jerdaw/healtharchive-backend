#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import pwd
import grp
import re
import socket
import stat
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.request import Request, urlopen


def _now_utc_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _repo_root() -> Path:
    # scripts/ is at repo_root/scripts/
    return Path(__file__).resolve().parents[1]


def _default_policy_path() -> Path:
    return _repo_root() / "docs" / "operations" / "production-baseline-policy.toml"


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _parse_env_file(path: Path) -> dict[str, str]:
    """
    Parse a simple KEY=VALUE env file without executing it.
    - Strips leading `export `
    - Ignores blank lines and comments
    - Strips surrounding single/double quotes
    """
    out: dict[str, str] = {}
    for raw_line in _read_text(path).splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].lstrip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if (value.startswith('"') and value.endswith('"')) or (value.startswith("'") and value.endswith("'")):
            value = value[1:-1]
        if key:
            out[key] = value
    return out


def _redact_database_url(value: str) -> str:
    """
    Redact credentials while keeping scheme + host/db helpful for debugging.

    - Leaves sqlite URLs untouched.
    - For URLs containing credentials (user:pass@), replaces with <redacted>@.
    """
    if value.startswith("sqlite://"):
        return value
    if "://" in value and "@" in value:
        scheme, rest = value.split("://", 1)
        after_at = rest.split("@", 1)[1]
        return f"{scheme}://<redacted>@{after_at}"
    return "<redacted>"


def _octal_mode_from_stat(st: os.stat_result) -> str:
    return format(stat.S_IMODE(st.st_mode), "04o")


def _stat_path(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"exists": False}
    st = path.stat()
    try:
        owner = pwd.getpwuid(st.st_uid).pw_name
    except KeyError:
        owner = str(st.st_uid)
    try:
        group = grp.getgrgid(st.st_gid).gr_name
    except KeyError:
        group = str(st.st_gid)
    is_dir = path.is_dir()
    return {
        "exists": True,
        "is_dir": is_dir,
        "owner": owner,
        "group": group,
        "mode": _octal_mode_from_stat(st),
        "writable": os.access(path, os.W_OK),
        "setgid": bool(st.st_mode & stat.S_ISGID) if is_dir else False,
    }


def _systemctl_query(unit: str) -> dict[str, Any]:
    def run(args: list[str]) -> tuple[int, str]:
        proc = subprocess.run(args, capture_output=True, text=True, check=False)
        return proc.returncode, (proc.stdout or proc.stderr).strip()

    enabled_code, enabled_out = run(["systemctl", "is-enabled", unit])
    active_code, active_out = run(["systemctl", "is-active", unit])
    return {
        "enabled": enabled_out if enabled_code == 0 else enabled_out,
        "active": active_out if active_code == 0 else active_out,
    }


def _user_in_group(user: str, group: str) -> bool:
    try:
        pw = pwd.getpwnam(user)
    except KeyError:
        return False
    try:
        gr = grp.getgrnam(group)
    except KeyError:
        return False
    if pw.pw_gid == gr.gr_gid:
        return True
    return user in gr.gr_mem


@dataclass(frozen=True)
class HstsCheck:
    configured_in_caddy: bool
    configured_value: str | None
    observed_on_health: bool | None
    observed_value: str | None


_HSTS_HEADER_RE = re.compile(r"Strict-Transport-Security\s+\"([^\"]+)\"")


def _check_hsts_in_caddyfile(caddyfile: Path, api_domain: str) -> tuple[bool, str | None]:
    """
    Best-effort parse of Caddyfile to determine if the API site block contains
    Strict-Transport-Security configuration.
    """
    if not caddyfile.exists():
        return False, None

    lines = _read_text(caddyfile).splitlines()
    # Remove comments but keep braces/directives.
    cleaned: list[str] = []
    for line in lines:
        if "#" in line:
            line = line.split("#", 1)[0]
        cleaned.append(line.rstrip())

    start_idx: int | None = None
    site_pat = re.compile(rf"^\s*{re.escape(api_domain)}(\s|,|\{{)")
    for i, line in enumerate(cleaned):
        if site_pat.search(line):
            start_idx = i
            break

    if start_idx is None:
        return False, None

    brace_depth = 0
    found = False
    found_value: str | None = None
    for i in range(start_idx, len(cleaned)):
        line = cleaned[i]
        brace_depth += line.count("{")
        brace_depth -= line.count("}")
        if "Strict-Transport-Security" in line:
            found = True
            m = _HSTS_HEADER_RE.search(line)
            if m:
                found_value = m.group(1)
        if i > start_idx and brace_depth <= 0:
            break
    return found, found_value


def _http_get_headers(url: str, headers: dict[str, str] | None = None, timeout_s: int = 10) -> dict[str, str]:
    req = Request(url, method="GET")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    with urlopen(req, timeout=timeout_s) as resp:  # noqa: S310 (operator-controlled URL)
        return {k: v for k, v in resp.headers.items()}


def _check_hsts_live(api_base: str, timeout_s: int = 10) -> tuple[bool, str | None]:
    try:
        headers = _http_get_headers(f"{api_base}/api/health", timeout_s=timeout_s)
    except URLError:
        return False, None
    val = headers.get("Strict-Transport-Security")
    if not val:
        return False, None
    return True, val


def _check_admin_auth_live(api_base: str, admin_token: str | None, timeout_s: int = 10) -> dict[str, Any]:
    """
    Validate admin/metrics endpoints are protected and (if token is available)
    also validate that access succeeds with the token.
    """
    results: dict[str, Any] = {}

    def get_status(url: str, hdrs: dict[str, str] | None = None) -> int | None:
        try:
            _ = _http_get_headers(url, headers=hdrs, timeout_s=timeout_s)
            return 200
        except URLError as exc:
            # urllib does not expose status code cleanly for all errors. Best-effort:
            code = getattr(exc, "code", None)
            if isinstance(code, int):
                return code
            return None

    metrics_url = f"{api_base}/metrics"
    admin_url = f"{api_base}/api/admin/jobs"

    results["metrics_without_token_status"] = get_status(metrics_url, None)
    results["admin_without_token_status"] = get_status(admin_url, None)

    if admin_token:
        hdrs = {"Authorization": f"Bearer {admin_token}"}
        results["metrics_with_token_status"] = get_status(metrics_url, hdrs)
        results["admin_with_token_status"] = get_status(admin_url, hdrs)
    else:
        results["metrics_with_token_status"] = None
        results["admin_with_token_status"] = None

    return results


def collect_observed(*, policy: dict[str, Any], mode: str = "local") -> dict[str, Any]:
    api_base = policy.get("urls", {}).get("api_base")
    if not isinstance(api_base, str) or not api_base:
        raise ValueError("Policy missing urls.api_base")

    backend_env_file = Path(policy.get("files", {}).get("backend_env_file", "/etc/healtharchive/backend.env"))
    caddyfile = Path(policy.get("files", {}).get("caddyfile", "/etc/caddy/Caddyfile"))
    api_domain = api_base.replace("https://", "").replace("http://", "").split("/", 1)[0]

    env: dict[str, Any] = {"backend_env_file": str(backend_env_file)}
    raw_env: dict[str, str] = {}
    env_read_error: str | None = None
    if backend_env_file.exists():
        try:
            raw_env = _parse_env_file(backend_env_file)
        except Exception as exc:  # noqa: BLE001
            env_read_error = f"{type(exc).__name__}: {exc}"
    else:
        env_read_error = "missing"

    admin_token = raw_env.get("HEALTHARCHIVE_ADMIN_TOKEN")
    env["HEALTHARCHIVE_ADMIN_TOKEN_present"] = bool(admin_token and admin_token.strip())

    db_url = raw_env.get("HEALTHARCHIVE_DATABASE_URL")
    if db_url:
        env["HEALTHARCHIVE_DATABASE_URL_redacted"] = _redact_database_url(db_url)

    for key in (
        "HEALTHARCHIVE_ENV",
        "HEALTHARCHIVE_ARCHIVE_ROOT",
        "HEALTHARCHIVE_PUBLIC_SITE_URL",
        "HEALTHARCHIVE_CORS_ORIGINS",
        "HA_SEARCH_RANKING_VERSION",
        "HA_PAGES_FASTPATH",
        "HEALTHARCHIVE_REPLAY_BASE_URL",
        "HEALTHARCHIVE_REPLAY_PREVIEW_DIR",
        "HEALTHARCHIVE_USAGE_METRICS_ENABLED",
        "HEALTHARCHIVE_CHANGE_TRACKING_ENABLED",
        "HEALTHARCHIVE_EXPORTS_ENABLED",
    ):
        if key in raw_env:
            env[key] = raw_env[key]

    hsts_configured, hsts_cfg_val = _check_hsts_in_caddyfile(caddyfile, api_domain=api_domain)
    hsts_seen = None
    hsts_seen_val = None
    admin_http: dict[str, Any] = {}
    if mode == "live":
        hsts_seen, hsts_seen_val = _check_hsts_live(api_base)
        admin_http = _check_admin_auth_live(api_base, admin_token if env["HEALTHARCHIVE_ADMIN_TOKEN_present"] else None)

    observed: dict[str, Any] = {
        "timestamp_utc": _now_utc_iso(),
        "host": {
            "hostname": socket.getfqdn(),
            "user": pwd.getpwuid(os.getuid()).pw_name,
            "uid": os.getuid(),
            "gid": os.getgid(),
        },
        "inputs": {
            "policy_version": policy.get("version"),
            "mode": mode,
            "caddyfile": str(caddyfile),
        },
        "env": env,
        "env_read_error": env_read_error,
        "hsts": {
            "configured_in_caddy": hsts_configured,
            "configured_value": hsts_cfg_val,
            "observed_on_health": hsts_seen,
            "observed_value": hsts_seen_val,
        },
        "operator": {
            "operator_user": policy.get("operator_user"),
            "ops_group": policy.get("ops_group"),
            "operator_in_ops_group": _user_in_group(
                str(policy.get("operator_user", "")),
                str(policy.get("ops_group", "")),
            ),
        },
        "filesystem": {},
        "systemd": {},
        "http": {
            "api_base": api_base,
            "admin_checks": admin_http,
        },
    }

    fs_paths = policy.get("filesystem", {}).get("paths", [])
    fs_out: dict[str, Any] = {}
    for entry in fs_paths:
        p = Path(entry.get("path", ""))
        if not p:
            continue
        fs_out[str(p)] = _stat_path(p)
    observed["filesystem"]["paths"] = fs_out

    units = policy.get("systemd", {}).get("units", [])
    units_out: dict[str, Any] = {}
    for entry in units:
        name = entry.get("name")
        if not isinstance(name, str) or not name:
            continue
        units_out[name] = _systemctl_query(name)
    observed["systemd"]["units"] = units_out

    return observed


def load_policy(path: Path) -> dict[str, Any]:
    if sys.version_info < (3, 11):
        raise RuntimeError("Python 3.11+ is required (uses tomllib).")
    import tomllib  # noqa: WPS433 (stdlib import gated by version)

    data = tomllib.loads(_read_text(path))
    if not isinstance(data, dict):
        raise ValueError("Policy file did not parse into a dict")
    return data


def _write_snapshot(observed: dict[str, Any], out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_path = out_dir / f"observed-{ts}.json"
    out_path.write_text(json.dumps(observed, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    latest_path = out_dir / "observed-latest.json"
    latest_path.write_text(out_path.read_text(encoding="utf-8"), encoding="utf-8")
    return out_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate an observed production baseline snapshot.")
    parser.add_argument("--policy", type=Path, default=_default_policy_path(), help="Path to policy TOML.")
    parser.add_argument(
        "--mode",
        choices=["local", "live"],
        default="local",
        help="local: parse config/filesystem only; live: also call HTTPS endpoints.",
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=None,
        help="If set, write observed snapshot JSON files under this directory.",
    )
    parser.add_argument("--stdout", action="store_true", help="Print the snapshot JSON to stdout.")

    args = parser.parse_args()
    policy = load_policy(args.policy)
    observed = collect_observed(policy=policy, mode=args.mode)

    if args.out_dir is not None:
        out_path = _write_snapshot(observed, args.out_dir)
        print(f"Wrote: {out_path}")

    if args.stdout or args.out_dir is None:
        print(json.dumps(observed, indent=2, sort_keys=True))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

