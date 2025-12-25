#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func

from ha_backend.db import get_session
from ha_backend.models import ArchiveJob, Source


def _dt_str(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat()


@dataclass(frozen=True)
class StatusCounts:
    counts: dict[str, int]

    def get(self, status: str) -> int:
        return int(self.counts.get(status, 0) or 0)


FAIL_ACTIVE_STATUSES = {"running", "indexing"}
WARN_STATUSES = {"queued", "retryable", "completed", "failed", "index_failed"}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "HealthArchive VPS helper: report job queue hygiene (stuck/running/retryable jobs) "
            "without changing the DB."
        )
    )
    parser.add_argument("--limit", type=int, default=50, help="Max jobs to list (default: 50).")
    parser.add_argument(
        "--json", action="store_true", default=False, help="Emit machine-readable JSON output."
    )
    args = parser.parse_args(argv)

    with get_session() as session:
        rows = (
            session.query(ArchiveJob.status, func.count(ArchiveJob.id))
            .group_by(ArchiveJob.status)
            .all()
        )
        counts = StatusCounts(counts={str(status or "unknown"): int(n or 0) for status, n in rows})

        job_rows = (
            session.query(
                ArchiveJob.id,
                Source.code,
                ArchiveJob.status,
                ArchiveJob.retry_count,
                ArchiveJob.created_at,
                ArchiveJob.queued_at,
                ArchiveJob.started_at,
                ArchiveJob.finished_at,
                ArchiveJob.name,
                ArchiveJob.output_dir,
            )
            .join(Source, ArchiveJob.source_id == Source.id)
            .order_by(ArchiveJob.id.desc())
            .limit(int(args.limit))
            .all()
        )
        recent_jobs = [
            {
                "id": int(job_id),
                "source": str(source_code),
                "status": str(status),
                "retryCount": int(retry_count or 0),
                "createdAt": _dt_str(created_at),
                "queuedAt": _dt_str(queued_at),
                "startedAt": _dt_str(started_at),
                "finishedAt": _dt_str(finished_at),
                "name": str(name),
                "outputDir": str(output_dir),
            }
            for (
                job_id,
                source_code,
                status,
                retry_count,
                created_at,
                queued_at,
                started_at,
                finished_at,
                name,
                output_dir,
            ) in job_rows
        ]

    findings: list[dict[str, Any]] = []
    for status in sorted(FAIL_ACTIVE_STATUSES):
        if counts.get(status) > 0:
            findings.append(
                {
                    "level": "FAIL",
                    "key": f"jobs_{status}",
                    "message": f"{counts.get(status)} job(s) currently {status}; host is not idle for preflight.",
                }
            )

    for status in sorted(WARN_STATUSES):
        n = counts.get(status)
        if n > 0:
            findings.append(
                {
                    "level": "WARN",
                    "key": f"jobs_{status}",
                    "message": f"{n} job(s) in status {status}.",
                }
            )

    payload = {
        "counts": counts.counts,
        "findings": findings,
        "recentJobs": recent_jobs,
    }

    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0 if not any(f["level"] == "FAIL" for f in findings) else 1

    print("Job queue hygiene")
    print("-----------------")
    print("Status counts:")
    for status, n in sorted(counts.counts.items(), key=lambda x: x[0]):
        print(f"  {status}: {int(n)}")

    for f in findings:
        print(
            f"{f['level']}: {f['message']}", file=sys.stderr if f["level"] == "FAIL" else sys.stdout
        )

    print("")
    print(f"Recent jobs (limit={args.limit}):")
    print(
        "ID  Source  Status      Retries  Created_at           Queued_at            Started_at           Finished_at          Name"
    )
    for item in payload["recentJobs"]:
        print(
            f"{item['id']:<3} {item['source']:<6} {item['status']:<10} {item['retryCount']:<7} "
            f"{(item['createdAt'] or '-'):<20} {(item['queuedAt'] or '-'):<20} {(item['startedAt'] or '-'):<20} "
            f"{(item['finishedAt'] or '-'):<20} {item['name']}"
        )

    if any(f["level"] == "FAIL" for f in findings):
        print("")
        print(
            "FAIL: active jobs are running; pause/finish them before large crawl work.",
            file=sys.stderr,
        )
        return 1

    print("")
    print("OK: no active running/indexing jobs detected.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
