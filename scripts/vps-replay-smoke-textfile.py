#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import time
import tomllib
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from ha_backend.archive_contract import ArchiveJobConfig
from ha_backend.db import get_session
from ha_backend.job_registry import get_config_for_source
from ha_backend.models import ArchiveJob, Source


@dataclass(frozen=True)
class ReplaySmokeConfig:
    sources: list[str]
    timeout_seconds: int
    min_body_bytes: int


@dataclass(frozen=True)
class ReplayTarget:
    source_code: str
    job_id: int
    seed_url: str


def _dt_to_epoch_seconds(dt: datetime) -> int:
    return int(dt.astimezone(timezone.utc).timestamp())


def _emit(lines: list[str], line: str) -> None:
    lines.append(line.rstrip("\n"))


def _load_config(path: Path) -> ReplaySmokeConfig:
    raw = tomllib.loads(path.read_text(encoding="utf-8"))
    sources = [str(s).strip() for s in raw.get("sources", []) if str(s).strip()]
    timeout_seconds = int(raw.get("timeout_seconds", 20))
    min_body_bytes = int(raw.get("min_body_bytes", 2048))
    return ReplaySmokeConfig(
        sources=sources,
        timeout_seconds=timeout_seconds,
        min_body_bytes=min_body_bytes,
    )


def _pick_latest_indexed_jobs(session, sources: list[str]) -> list[ReplayTarget]:
    rows = (
        session.query(
            ArchiveJob.id,
            Source.code,
            ArchiveJob.config,
        )
        .join(Source, ArchiveJob.source_id == Source.id)
        .filter(ArchiveJob.status == "indexed")
        .filter(Source.code.in_(sources))
        .order_by(ArchiveJob.id.desc())
        .all()
    )

    seen: set[str] = set()
    targets: list[ReplayTarget] = []
    for job_id, source_code, raw_config in rows:
        source_code = str(source_code)
        if source_code in seen:
            continue
        cfg = ArchiveJobConfig.from_dict(raw_config or {})
        seed_url = str(cfg.seeds[0]) if cfg.seeds else ""
        if not seed_url:
            registry_cfg = get_config_for_source(source_code)
            if registry_cfg and registry_cfg.default_seeds:
                seed_url = str(registry_cfg.default_seeds[0])
        if not seed_url:
            continue
        targets.append(
            ReplayTarget(
                source_code=source_code,
                job_id=int(job_id),
                seed_url=seed_url,
            )
        )
        seen.add(source_code)
        if len(seen) >= len(sources):
            break
    return targets


def _fetch(url: str, timeout_seconds: int, min_body_bytes: int) -> tuple[int, int, float]:
    req = Request(
        url,
        headers={
            "User-Agent": "HealthArchiveReplaySmoke/1.0",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        },
        method="GET",
    )
    start = time.monotonic()
    try:
        with urlopen(req, timeout=timeout_seconds) as resp:
            status = int(resp.getcode() or 0)
            body = resp.read(min_body_bytes + 1)
            elapsed = time.monotonic() - start
            return status, len(body), elapsed
    except HTTPError as exc:
        elapsed = time.monotonic() - start
        return int(exc.code or 0), 0, elapsed
    except URLError:
        elapsed = time.monotonic() - start
        return 0, 0, elapsed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "HealthArchive VPS helper: replay smoke tests for latest indexed jobs, "
            "writing metrics to node_exporter textfile collector."
        )
    )
    parser.add_argument(
        "--config",
        default="/opt/healtharchive-backend/ops/automation/replay-smoke.toml",
        help="Config TOML path.",
    )
    parser.add_argument(
        "--out-dir",
        default="/var/lib/node_exporter/textfile_collector",
        help="node_exporter textfile collector directory.",
    )
    parser.add_argument(
        "--out-file",
        default="healtharchive_replay_smoke.prom",
        help="Output filename under --out-dir.",
    )
    args = parser.parse_args(argv)

    now = datetime.now(timezone.utc)
    metrics_ok = 1
    config: ReplaySmokeConfig | None = None
    try:
        config = _load_config(Path(args.config))
    except Exception:
        metrics_ok = 0

    base_url = (os.environ.get("HEALTHARCHIVE_REPLAY_BASE_URL") or "").strip()
    replay_enabled = 1 if base_url else 0
    if base_url:
        base_url = base_url.rstrip("/")

    lines: list[str] = []
    _emit(lines, "# HELP healtharchive_replay_smoke_metrics_ok 1 if script ran.")
    _emit(lines, "# TYPE healtharchive_replay_smoke_metrics_ok gauge")
    _emit(lines, f"healtharchive_replay_smoke_metrics_ok {metrics_ok}")
    _emit(
        lines,
        "# HELP healtharchive_replay_smoke_timestamp_seconds UNIX timestamp when these metrics were generated.",
    )
    _emit(lines, "# TYPE healtharchive_replay_smoke_timestamp_seconds gauge")
    _emit(lines, f"healtharchive_replay_smoke_timestamp_seconds {_dt_to_epoch_seconds(now)}")
    _emit(
        lines,
        "# HELP healtharchive_replay_smoke_enabled 1 if replay base URL is configured.",
    )
    _emit(lines, "# TYPE healtharchive_replay_smoke_enabled gauge")
    _emit(lines, f"healtharchive_replay_smoke_enabled {replay_enabled}")

    _emit(lines, "# HELP healtharchive_replay_smoke_target_present 1 if a replay target was found.")
    _emit(lines, "# TYPE healtharchive_replay_smoke_target_present gauge")
    _emit(lines, "# HELP healtharchive_replay_smoke_ok 1 if replay smoke check succeeded.")
    _emit(lines, "# TYPE healtharchive_replay_smoke_ok gauge")
    _emit(lines, "# HELP healtharchive_replay_smoke_status_code HTTP status code observed.")
    _emit(lines, "# TYPE healtharchive_replay_smoke_status_code gauge")
    _emit(lines, "# HELP healtharchive_replay_smoke_bytes Bytes read from response body (capped).")
    _emit(lines, "# TYPE healtharchive_replay_smoke_bytes gauge")
    _emit(lines, "# HELP healtharchive_replay_smoke_latency_seconds Request latency in seconds.")
    _emit(lines, "# TYPE healtharchive_replay_smoke_latency_seconds gauge")

    if metrics_ok == 0 or config is None or not config.sources:
        out_dir = Path(args.out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        out_file = out_dir / str(args.out_file)
        tmp = out_file.with_suffix(out_file.suffix + f".{os.getpid()}.tmp")
        tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
        tmp.chmod(0o644)
        tmp.replace(out_file)
        return 0

    if not replay_enabled:
        out_dir = Path(args.out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        out_file = out_dir / str(args.out_file)
        tmp = out_file.with_suffix(out_file.suffix + f".{os.getpid()}.tmp")
        tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
        tmp.chmod(0o644)
        tmp.replace(out_file)
        return 0

    targets: list[ReplayTarget] = []
    with get_session() as session:
        targets = _pick_latest_indexed_jobs(session, config.sources)

    targets_by_source = {t.source_code: t for t in targets}
    for source_code in config.sources:
        target = targets_by_source.get(source_code)
        labels = f'source="{source_code}"'
        if target is None:
            _emit(lines, f"healtharchive_replay_smoke_target_present{{{labels}}} 0")
            continue

        _emit(lines, f"healtharchive_replay_smoke_target_present{{{labels}}} 1")
        url = f"{base_url}/job-{target.job_id}/{target.seed_url}"
        status, body_len, latency = _fetch(
            url,
            timeout_seconds=config.timeout_seconds,
            min_body_bytes=config.min_body_bytes,
        )
        ok = 1 if status == 200 and body_len >= config.min_body_bytes else 0
        labels_with_job = f'{labels},job_id="{target.job_id}"'
        _emit(lines, f"healtharchive_replay_smoke_ok{{{labels_with_job}}} {ok}")
        _emit(lines, f"healtharchive_replay_smoke_status_code{{{labels_with_job}}} {status}")
        _emit(lines, f"healtharchive_replay_smoke_bytes{{{labels_with_job}}} {body_len}")
        _emit(
            lines, f"healtharchive_replay_smoke_latency_seconds{{{labels_with_job}}} {latency:.3f}"
        )

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / str(args.out_file)
    tmp = out_file.with_suffix(out_file.suffix + f".{os.getpid()}.tmp")
    tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
    tmp.chmod(0o644)
    tmp.replace(out_file)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
