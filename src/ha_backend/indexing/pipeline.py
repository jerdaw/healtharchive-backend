from __future__ import annotations

import logging
from pathlib import Path

from sqlalchemy.orm import Session

from ha_backend.db import get_session
from ha_backend.indexing.mapping import record_to_snapshot
from ha_backend.indexing.text_extraction import (detect_language, extract_text,
                                                 extract_title, make_snippet)
from ha_backend.indexing.warc_discovery import discover_warcs_for_job
from ha_backend.indexing.warc_reader import iter_html_records
from ha_backend.models import ArchiveJob, Snapshot

logger = logging.getLogger("healtharchive.indexing")


def _load_job(session: Session, job_id: int) -> ArchiveJob:
    job = session.get(ArchiveJob, job_id)
    if job is None:
        raise ValueError(f"ArchiveJob with id={job_id} does not exist.")
    return job


def index_job(job_id: int) -> int:
    """
    Index a completed ArchiveJob into Snapshot rows.

    Returns:
        0 on success, non-zero on failure.
    """
    with get_session() as session:
        job = _load_job(session, job_id)

        if job.source is None:
            raise ValueError(
                f"ArchiveJob {job_id} has no associated Source; cannot index."
            )

        if job.status not in ("completed", "index_failed", "indexed"):
            raise ValueError(
                f"ArchiveJob {job_id} is in status {job.status!r}, "
                "expected one of 'completed', 'index_failed', or 'indexed'."
            )

        output_dir = Path(job.output_dir)
        if not output_dir.is_dir():
            raise ValueError(
                f"ArchiveJob {job_id} output_dir does not exist or is not a directory: {output_dir}"
            )

        # Discover WARC files for this job.
        warc_paths = discover_warcs_for_job(job)
        job.warc_file_count = len(warc_paths)

        if not warc_paths:
            logger.warning(
                "No WARC files discovered for job %s in %s", job_id, output_dir
            )
            job.status = "index_failed"
            return 1

        # Mark job as indexing and clear any prior snapshots for this job to
        # make the operation idempotent.
        logger.info(
            "Starting indexing for job %s (%d WARC file(s))", job_id, len(warc_paths)
        )
        session.query(Snapshot).filter(Snapshot.job_id == job.id).delete(
            synchronize_session=False
        )
        job.indexed_page_count = 0
        job.status = "indexing"

        n_snapshots = 0

        try:
            for warc_path in warc_paths:
                for rec in iter_html_records(warc_path):
                    try:
                        # Decode bytes to text; prefer UTF-8 with replacement for robustness.
                        html = rec.body_bytes.decode("utf-8", errors="replace")
                        title = extract_title(html)
                        text = extract_text(html)
                        snippet = make_snippet(text)
                        language = detect_language(text, rec.headers)

                        snapshot = record_to_snapshot(
                            job=job,
                            source=job.source,
                            rec=rec,
                            title=title,
                            snippet=snippet,
                            language=language,
                        )
                        session.add(snapshot)
                        n_snapshots += 1

                        # Flush periodically to keep memory usage reasonable.
                        if n_snapshots % 500 == 0:
                            session.flush()
                    except Exception as rec_exc:
                        logger.warning(
                            "Skipping record in %s due to parse error: %s",
                            warc_path,
                            rec_exc,
                        )
                        continue

            job.indexed_page_count = n_snapshots
            job.status = "indexed"
            logger.info(
                "Indexing for job %s completed successfully with %d snapshot(s).",
                job_id,
                n_snapshots,
            )
            return 0
        except Exception as exc:
            logger.error("Indexing for job %s failed: %s", job_id, exc)
            job.status = "index_failed"
            return 1


__all__ = ["index_job"]
