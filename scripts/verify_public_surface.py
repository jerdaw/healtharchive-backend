#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


@dataclass(frozen=True)
class HttpResponse:
    status: int
    headers: dict[str, str]
    body: bytes


def _normalize_base(url: str) -> str:
    return url.strip().rstrip("/")


def _http_request(
    url: str,
    *,
    timeout_s: float,
    method: str = "GET",
    headers: dict[str, str] | None = None,
    json_body: dict[str, Any] | None = None,
    read_limit_bytes: int = 64 * 1024,
) -> HttpResponse:
    data = None
    hdrs = {"User-Agent": "healtharchive-verify-public-surface/1.0"}
    if headers:
        hdrs.update(headers)

    if json_body is not None:
        data = json.dumps(json_body).encode("utf-8")
        hdrs.setdefault("Content-Type", "application/json")

    req = Request(url, data=data, method=method, headers=hdrs)
    try:
        with urlopen(req, timeout=timeout_s) as resp:
            status = int(getattr(resp, "status", 200))
            resp_headers = {k: v for k, v in resp.headers.items()}
            body = resp.read(read_limit_bytes) if read_limit_bytes else resp.read()
            return HttpResponse(status=status, headers=resp_headers, body=body)
    except HTTPError as exc:
        body = b""
        try:
            body = exc.read(read_limit_bytes) if read_limit_bytes else exc.read()
        except Exception:  # noqa: BLE001
            body = b""
        return HttpResponse(
            status=int(getattr(exc, "code", 0) or 0), headers=dict(exc.headers), body=body
        )


def _http_json(url: str, *, timeout_s: float) -> tuple[HttpResponse, Any]:
    resp = _http_request(url, timeout_s=timeout_s, method="GET", read_limit_bytes=512 * 1024)
    if resp.status != 200:
        return resp, None
    try:
        return resp, json.loads(resp.body.decode("utf-8"))
    except Exception:  # noqa: BLE001
        return resp, None


def _fail(msg: str) -> None:
    print(f"FAIL {msg}")


def _ok(msg: str) -> None:
    print(f"OK   {msg}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Verify HealthArchive public surface (frontend + public API + replay + usage metrics)."
    )
    parser.add_argument(
        "--api-base", default="https://api.healtharchive.ca", help="Backend API base URL."
    )
    parser.add_argument(
        "--frontend-base", default="https://www.healtharchive.ca", help="Frontend base URL."
    )
    parser.add_argument(
        "--timeout-seconds",
        type=float,
        default=20.0,
        help="Per-request timeout in seconds (default: 20).",
    )
    parser.add_argument(
        "--allow-usage-disabled",
        action="store_true",
        default=False,
        help="Do not fail if /api/usage reports enabled=false.",
    )
    parser.add_argument(
        "--allow-exports-disabled",
        action="store_true",
        default=False,
        help="Do not fail if /api/exports reports enabled=false.",
    )
    parser.add_argument(
        "--skip-exports",
        action="store_true",
        default=False,
        help="Skip export endpoint checks (Phase 8).",
    )
    parser.add_argument(
        "--require-source",
        action="append",
        default=[],
        help="Require that /api/sources includes this sourceCode (repeatable; e.g. --require-source cihr).",
    )
    parser.add_argument(
        "--skip-replay",
        action="store_true",
        default=False,
        help="Skip replay URL validation (Phase 6).",
    )
    parser.add_argument(
        "--skip-frontend",
        action="store_true",
        default=False,
        help="Skip frontend page checks (Phase 5 and Phase 7).",
    )

    args = parser.parse_args(argv)

    api_base = _normalize_base(str(args.api_base))
    frontend_base = _normalize_base(str(args.frontend_base))
    timeout_s = float(args.timeout_seconds)

    failures = 0

    print("HealthArchive public surface verification")
    print("-----------------------------------")
    print(f"API base:      {api_base}")
    print(f"Frontend base: {frontend_base}")
    print(f"Timeout:       {timeout_s:.0f}s")
    print("")

    health, health_json = _http_json(f"{api_base}/api/health", timeout_s=timeout_s)
    if health.status != 200 or not isinstance(health_json, dict):
        _fail(f"api health status={health.status} url={api_base}/api/health")
        failures += 1
    else:
        _ok(f"api health status=200 url={api_base}/api/health")

    stats, stats_json = _http_json(f"{api_base}/api/stats", timeout_s=timeout_s)
    if stats.status != 200 or not isinstance(stats_json, dict):
        _fail(f"api stats status={stats.status} url={api_base}/api/stats")
        failures += 1
    else:
        _ok(f"api stats status=200 snapshotsTotal={stats_json.get('snapshotsTotal')!r}")

    sources, sources_json = _http_json(f"{api_base}/api/sources", timeout_s=timeout_s)
    if sources.status != 200 or not isinstance(sources_json, list) or not sources_json:
        _fail(f"api sources status={sources.status} (expected non-empty list)")
        failures += 1
        sources_json = []
    else:
        _ok(f"api sources status=200 count={len(sources_json)}")
        required_sources = [s.strip().lower() for s in args.require_source if str(s).strip()]
        if required_sources:
            present = {
                str(row.get("sourceCode", "")).strip().lower()
                for row in sources_json
                if isinstance(row, dict)
            }
            missing = [code for code in required_sources if code not in present]
            if missing:
                _fail(f"api sources missing required sourceCode(s): {missing}")
                failures += 1
            else:
                _ok(f"api sources includes required sourceCode(s): {required_sources}")

    if not args.skip_exports:
        exports_url = f"{api_base}/api/exports"
        exports, exports_json = _http_json(exports_url, timeout_s=timeout_s)
        if exports.status != 200 or not isinstance(exports_json, dict):
            _fail(f"api exports manifest status={exports.status} url={exports_url}")
            failures += 1
        else:
            enabled = exports_json.get("enabled")
            if enabled is True:
                _ok("api exports manifest enabled=true")

                snapshots_head = _http_request(
                    f"{api_base}/api/exports/snapshots?format=jsonl&compressed=false",
                    timeout_s=timeout_s,
                    method="HEAD",
                    read_limit_bytes=0,
                )
                if snapshots_head.status != 200:
                    _fail(f"api exports snapshots HEAD status={snapshots_head.status}")
                    failures += 1
                else:
                    _ok("api exports snapshots HEAD status=200")

                changes_head = _http_request(
                    f"{api_base}/api/exports/changes?format=jsonl&compressed=false",
                    timeout_s=timeout_s,
                    method="HEAD",
                    read_limit_bytes=0,
                )
                if changes_head.status != 200:
                    _fail(f"api exports changes HEAD status={changes_head.status}")
                    failures += 1
                else:
                    _ok("api exports changes HEAD status=200")
            elif args.allow_exports_disabled:
                _ok("api exports manifest enabled=false (allowed)")
            else:
                _fail("api exports enabled=false (expected enabled=true for Phase 8)")
                failures += 1

    search_url = f"{api_base}/api/search?pageSize=1"
    search, search_json = _http_json(search_url, timeout_s=timeout_s)
    first_snapshot_id: int | None = None
    browse_url: str | None = None
    raw_snapshot_path: str | None = None
    if search.status != 200 or not isinstance(search_json, dict):
        _fail(f"api search status={search.status} url={search_url}")
        failures += 1
    else:
        results = search_json.get("results")
        if not isinstance(results, list) or not results:
            _fail(f"api search returned no results url={search_url}")
            failures += 1
        else:
            row = results[0] if isinstance(results[0], dict) else {}
            first_snapshot_id = row.get("id") if isinstance(row.get("id"), int) else None
            browse_url = row.get("browseUrl") if isinstance(row.get("browseUrl"), str) else None
            raw_snapshot_path = (
                row.get("rawSnapshotUrl") if isinstance(row.get("rawSnapshotUrl"), str) else None
            )
            _ok(
                "api search status=200 "
                + (f"snapshot_id={first_snapshot_id} " if first_snapshot_id else "")
                + (f"browseUrl={'yes' if bool(browse_url) else 'no'}" if first_snapshot_id else "")
            )

    if first_snapshot_id is not None:
        detail_url = f"{api_base}/api/snapshot/{first_snapshot_id}"
        detail, detail_json = _http_json(detail_url, timeout_s=timeout_s)
        if detail.status != 200 or not isinstance(detail_json, dict):
            _fail(f"api snapshot detail status={detail.status} url={detail_url}")
            failures += 1
        else:
            _ok(f"api snapshot detail status=200 id={detail_json.get('id')!r}")

        if raw_snapshot_path:
            raw_url = (
                raw_snapshot_path
                if raw_snapshot_path.startswith("http")
                else f"{api_base}{raw_snapshot_path}"
            )
            raw = _http_request(
                raw_url,
                timeout_s=timeout_s,
                method="GET",
                read_limit_bytes=128 * 1024,
            )
            content_type = (
                raw.headers.get("Content-Type") or raw.headers.get("content-type") or ""
            ).lower()
            if raw.status != 200 or (
                "text/html" not in content_type and "application/xhtml" not in content_type
            ):
                _fail(
                    f"raw snapshot status={raw.status} content-type={content_type!r} url={raw_url}"
                )
                failures += 1
            else:
                _ok(f"raw snapshot status=200 url={raw_url}")

        if browse_url and not args.skip_replay:
            replay = _http_request(
                browse_url,
                timeout_s=timeout_s,
                method="GET",
                read_limit_bytes=64 * 1024,
                headers={"Accept": "text/html"},
            )
            if replay.status != 200:
                _fail(f"replay browseUrl status={replay.status} url={browse_url}")
                failures += 1
            else:
                _ok(f"replay browseUrl status=200 url={browse_url}")
    else:
        _fail("no snapshot id available to test /api/snapshot, raw HTML, or replay URL")
        failures += 1

    usage, usage_json = _http_json(f"{api_base}/api/usage", timeout_s=timeout_s)
    if usage.status != 200 or not isinstance(usage_json, dict):
        _fail(f"api usage status={usage.status} url={api_base}/api/usage")
        failures += 1
    else:
        enabled = usage_json.get("enabled")
        if enabled is True:
            _ok(f"api usage enabled=true windowDays={usage_json.get('windowDays')!r}")
        elif args.allow_usage_disabled:
            _ok(f"api usage enabled=false (allowed) windowDays={usage_json.get('windowDays')!r}")
        else:
            _fail("api usage enabled=false (expected enabled=true for Phase 7)")
            failures += 1

    if not args.skip_frontend:
        pages: list[tuple[str, str]] = [
            ("archive", f"{frontend_base}/archive"),
            ("browse-by-source", f"{frontend_base}/archive/browse-by-source"),
            ("exports", f"{frontend_base}/exports"),
            ("researchers", f"{frontend_base}/researchers"),
            ("status", f"{frontend_base}/status"),
            ("impact", f"{frontend_base}/impact"),
        ]
        if first_snapshot_id is not None:
            pages.append(("snapshot", f"{frontend_base}/snapshot/{first_snapshot_id}"))

        for name, url in pages:
            resp = _http_request(url, timeout_s=timeout_s, method="GET", read_limit_bytes=64 * 1024)
            if resp.status != 200:
                _fail(f"frontend {name} status={resp.status} url={url}")
                failures += 1
            else:
                _ok(f"frontend {name} status=200 url={url}")

        # Report forwarder (safe: includes a honeypot field so the backend won't persist it)
        report_payload = {
            "category": "general_feedback",
            "description": "Automated smoke test submission (ignore).",
            "pageUrl": f"{frontend_base}/status",
            "website": "do-not-store",
        }
        try:
            report = _http_request(
                f"{frontend_base}/api/report",
                timeout_s=timeout_s,
                method="POST",
                json_body=report_payload,
                read_limit_bytes=64 * 1024,
            )
            if report.status not in (200, 201):
                _fail(
                    f"frontend report forwarder status={report.status} url={frontend_base}/api/report"
                )
                failures += 1
            else:
                parsed = None
                try:
                    parsed = json.loads(report.body.decode("utf-8"))
                except Exception:  # noqa: BLE001
                    parsed = None
                if isinstance(parsed, dict) and parsed.get("status") in ("received", "new"):
                    _ok(f"frontend report forwarder status={report.status}")
                else:
                    _fail("frontend report forwarder returned unexpected body")
                    failures += 1
        except URLError as exc:
            _fail(f"frontend report forwarder failed: {type(exc).__name__}: {exc}")
            failures += 1

    print("")
    if failures:
        print(f"FAILURES: {failures}")
        return 1
    print("All checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
